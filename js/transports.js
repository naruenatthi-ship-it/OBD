// Browser transports for ELM327 adapters. Each one exposes connect(),
// disconnect(), write(text) and calls `onData(text)` / `onDisconnect()`.

// GATT services used by common BLE ELM327 clones (Vgate iCar Pro BLE,
// Veepeak BLE, vLinker, generic "OBDII"/"OBDBLE" adapters).
const BLE_SERVICES = [
  0xfff0,
  0xffe0,
  0x18f0,
  'e7810a71-73ae-499d-8c15-faa9aef0c3f2',
];

const BLE_CHUNK = 20;

export class BleTransport {
  static isSupported() {
    return typeof navigator !== 'undefined' && 'bluetooth' in navigator;
  }

  constructor() {
    this.label = 'Bluetooth LE';
    this.onData = () => {};
    this.onDisconnect = () => {};
    this.decoder = new TextDecoder();
    this.encoder = new TextEncoder();
  }

  async connect() {
    this.device = await navigator.bluetooth.requestDevice({
      acceptAllDevices: true,
      optionalServices: BLE_SERVICES,
    });
    this.label = this.device.name || 'Bluetooth LE';
    this.device.addEventListener('gattserverdisconnected', () => this.onDisconnect());

    const server = await this.device.gatt.connect();
    const services = await server.getPrimaryServices();

    for (const service of services) {
      const chars = await service.getCharacteristics();
      const notify = chars.find((c) => c.properties.notify || c.properties.indicate);
      const write = chars.find((c) => c.properties.write || c.properties.writeWithoutResponse);
      if (notify && write) {
        this.rx = notify;
        this.tx = write;
        break;
      }
    }

    if (!this.rx) {
      server.disconnect();
      throw new Error(
        'อุปกรณ์นี้ไม่มีช่องสื่อสารแบบ ELM327 — ตรวจสอบว่าเป็นตัว OBD-II ชนิด Bluetooth LE (BLE 4.0 ขึ้นไป)',
      );
    }

    this.rx.addEventListener('characteristicvaluechanged', (event) => {
      this.onData(this.decoder.decode(event.target.value));
    });
    await this.rx.startNotifications();
  }

  async write(text) {
    const bytes = this.encoder.encode(text);
    for (let i = 0; i < bytes.length; i += BLE_CHUNK) {
      const chunk = bytes.slice(i, i + BLE_CHUNK);
      if (this.tx.properties.writeWithoutResponse) {
        await this.tx.writeValueWithoutResponse(chunk);
      } else {
        await this.tx.writeValueWithResponse(chunk);
      }
    }
  }

  async disconnect() {
    try {
      await this.rx?.stopNotifications();
    } catch {
      // The device may already be gone.
    }
    this.device?.gatt?.disconnect();
  }
}

// Serial Port Profile, for Bluetooth Classic adapters paired with the computer.
const SPP_UUID = '00001101-0000-1000-8000-00805f9b34fb';

export class SerialTransport {
  static isSupported() {
    return typeof navigator !== 'undefined' && 'serial' in navigator;
  }

  constructor({ baudRate = 38400 } = {}) {
    this.label = 'USB / Serial';
    this.baudRate = baudRate;
    this.onData = () => {};
    this.onDisconnect = () => {};
    this.encoder = new TextEncoder();
  }

  async connect() {
    this.port = await navigator.serial.requestPort({
      allowedBluetoothServiceClassIds: [SPP_UUID],
    });
    await this.port.open({ baudRate: this.baudRate });
    this.writer = this.port.writable.getWriter();
    this.readLoop();
  }

  async readLoop() {
    const decoder = new TextDecoder();
    this.reader = this.port.readable.getReader();
    try {
      for (;;) {
        const { value, done } = await this.reader.read();
        if (done) break;
        this.onData(decoder.decode(value, { stream: true }));
      }
    } catch {
      // Port was unplugged or closed.
    } finally {
      this.reader.releaseLock();
      if (!this.closing) this.onDisconnect();
    }
  }

  async write(text) {
    await this.writer.write(this.encoder.encode(text));
  }

  async disconnect() {
    this.closing = true;
    try {
      await this.reader?.cancel();
      this.writer?.releaseLock();
      await this.port?.close();
    } catch {
      // Ignore errors while tearing down.
    }
  }
}
