// Talks to an ELM327 (or compatible) adapter over any transport that offers
// write(text) and delivers incoming text to the onData callback.

import {
  parseMessages,
  parseDtcs,
  parseSupportedPids,
  parseVin,
  parseMonitorStatus,
  pidData,
  decodePid,
  decodeDtc,
  isCanProtocol,
} from './obd.js';

const ERRORS = {
  'UNABLE TO CONNECT': 'ติดต่อกล่อง ECU ไม่ได้ — เปิดสวิตช์กุญแจ (ON) หรือติดเครื่องแล้วหรือยัง?',
  'CAN ERROR': 'สื่อสารบนบัส CAN ผิดพลาด',
  'BUS ERROR': 'สัญญาณบนบัสผิดปกติ',
  'BUS BUSY': 'บัสไม่ว่าง ลองใหม่อีกครั้ง',
  'FB ERROR': 'สัญญาณป้อนกลับผิดพลาด (FB ERROR)',
  'DATA ERROR': 'ข้อมูลที่ได้รับเสียหาย',
  'BUFFER FULL': 'บัฟเฟอร์ของอุปกรณ์เต็ม',
  STOPPED: 'คำสั่งถูกขัดจังหวะ',
  ERROR: 'อุปกรณ์แจ้งข้อผิดพลาด',
  '?': 'อุปกรณ์ไม่รู้จักคำสั่ง',
};

const NOISE = /^(SEARCHING\.*|BUS ?INIT:?.*)$/;

export const PROTOCOLS = {
  1: 'SAE J1850 PWM',
  2: 'SAE J1850 VPW',
  3: 'ISO 9141-2',
  4: 'ISO 14230-4 KWP (5 baud)',
  5: 'ISO 14230-4 KWP (fast)',
  6: 'ISO 15765-4 CAN (11 bit, 500 kbaud)',
  7: 'ISO 15765-4 CAN (29 bit, 500 kbaud)',
  8: 'ISO 15765-4 CAN (11 bit, 250 kbaud)',
  9: 'ISO 15765-4 CAN (29 bit, 250 kbaud)',
  A: 'SAE J1939 CAN',
};

export class ObdError extends Error {}

export class Elm327 {
  constructor(transport, { log = () => {}, timeout = 5000 } = {}) {
    this.transport = transport;
    this.log = log;
    this.timeout = timeout;
    this.buffer = '';
    this.pending = null;
    this.queue = Promise.resolve();
    this.isCan = true;
    this.protocol = null;
    this.supportedPids = new Set();
    transport.onData = (text) => this.receive(text);
  }

  receive(text) {
    this.buffer += text.replace(/\0/g, '');
    const end = this.buffer.indexOf('>');
    if (end === -1 || !this.pending) return;
    const reply = this.buffer.slice(0, end);
    this.buffer = this.buffer.slice(end + 1);
    this.pending.resolve(reply);
  }

  // Send one command and resolve with the raw reply (without the prompt).
  // Commands are queued so replies can never interleave.
  send(command, timeout = this.timeout) {
    const run = () =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          this.pending = null;
          reject(new ObdError(`หมดเวลารอการตอบกลับของคำสั่ง ${command}`));
        }, timeout);
        this.pending = {
          resolve: (reply) => {
            clearTimeout(timer);
            this.pending = null;
            this.log('rx', reply.trim());
            resolve(reply);
          },
        };
        this.buffer = '';
        this.log('tx', command);
        this.transport.write(command + '\r').catch((err) => {
          clearTimeout(timer);
          this.pending = null;
          reject(err);
        });
      });

    const result = this.queue.then(run, run);
    this.queue = result.catch(() => {});
    return result;
  }

  // Send a command and return the meaningful reply lines.
  // Returns [] for NO DATA; throws ObdError for adapter error replies.
  async query(command, timeout) {
    const reply = await this.send(command, timeout);
    const lines = reply
      .split(/[\r\n]+/)
      .map((l) => l.trim())
      .filter((l) => l && l.replace(/\s/g, '') !== command && !NOISE.test(l));

    if (lines.some((l) => l === 'NO DATA')) return [];
    for (const line of lines) {
      const key = Object.keys(ERRORS).find((k) => line === k || line.startsWith(k + ' '));
      if (key) throw new ObdError(ERRORS[key]);
    }
    return lines;
  }

  async messages(command, timeout) {
    return parseMessages(await this.query(command, timeout));
  }

  async initialize() {
    const id = await this.query('ATZ', 8000);
    const version = id.find((l) => /ELM|OBD|STN/i.test(l)) ?? id.join(' ');
    for (const cmd of ['ATE0', 'ATL0', 'ATS0', 'ATH0', 'ATAT1', 'ATSP0']) {
      await this.query(cmd);
    }

    // The first OBD request makes the adapter search for the car's protocol,
    // which can take several seconds on older cars.
    const first = await this.messages('0100', 20000);
    this.supportedPids = parseSupportedPids(first, 0x00);
    for (const base of [0x20, 0x40, 0x60]) {
      if (!this.supportedPids.has(base)) break;
      const more = parseSupportedPids(await this.messages('01' + hex(base)), base);
      more.forEach((p) => this.supportedPids.add(p));
    }

    const dpn = (await this.query('ATDPN'))[0] ?? '';
    const number = dpn.replace(/^A/i, '').toUpperCase();
    this.isCan = isCanProtocol(number);
    this.protocol = PROTOCOLS[number] ?? dpn;

    return { version, protocol: this.protocol, isCan: this.isCan };
  }

  async readVoltage() {
    const line = (await this.query('ATRV'))[0];
    const value = parseFloat(line);
    return Number.isFinite(value) ? value : null;
  }

  async readVin() {
    try {
      return parseVin(await this.messages('0902', 8000));
    } catch {
      return null;
    }
  }

  async readStatus() {
    return parseMonitorStatus(pidData(await this.messages('0101'), 0x01));
  }

  // mode 03 = stored, 07 = pending, 0A = permanent codes
  async readDtcs(mode = 0x03) {
    return parseDtcs(await this.messages(hex(mode)), mode, this.isCan);
  }

  async clearDtcs() {
    const lines = await this.query('04', 10000);
    return lines.some((l) => l.replace(/\s/g, '').startsWith('44'));
  }

  async readPid(pid) {
    return decodePid(pid, pidData(await this.messages('01' + hex(pid)), pid));
  }

  // Freeze frame 0: the code that triggered it, then the PID snapshot.
  async readFreezeFrame(pids) {
    const dtcData = pidData(await this.messages('020200'), 0x02, 2);
    if (!dtcData || dtcData.length < 2 || (dtcData[0] === 0 && dtcData[1] === 0)) return null;

    const values = {};
    for (const pid of pids) {
      try {
        const value = decodePid(pid, pidData(await this.messages('02' + hex(pid) + '00'), pid, 2));
        if (value != null) values[pid] = value;
      } catch {
        // Not every ECU stores every PID in the freeze frame.
      }
    }
    return { dtc: decodeDtc(dtcData[0], dtcData[1]), values };
  }
}

function hex(n) {
  return n.toString(16).toUpperCase().padStart(2, '0');
}
