// A fake ELM327 + car, used for the demo mode and for tests. It mimics the
// adapter's text protocol (echo, spaces, prompt, multi-frame CAN replies) so
// the same code path is exercised as with a real adapter.

import { encodeDtc, encodeSupportedPids } from './obd.js';

const SUPPORTED = [
  0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10, 0x11, 0x1f, 0x20,
  0x21, 0x2f, 0x31, 0x33, 0x40,
  0x42, 0x46, 0x5c,
];

export class SimulatorTransport {
  constructor({ delay = 15 } = {}) {
    this.label = 'รถจำลอง (โหมดทดลอง)';
    this.delay = delay;
    this.onData = () => {};
    this.onDisconnect = () => {};
    this.reset();
  }

  reset() {
    this.echo = true;
    this.spaces = true;
    this.stored = ['P0301', 'P0171', 'P0420'];
    this.pending = ['P0133'];
    this.freezeFrame = { dtc: 'P0301', 0x04: 0x8c, 0x05: 0x7d, 0x0c: [0x0b, 0xb8], 0x0d: 0x3c, 0x11: 0x40 };
    this.start = Date.now();
  }

  async connect() {
    this.start = Date.now();
  }

  async disconnect() {}

  async write(text) {
    const command = text.trim().toUpperCase().replace(/\s/g, '');
    const lines = this.respond(command);
    const reply = (this.echo ? command + '\r' : '') + lines.join('\r') + '\r\r>';
    // Deliver in two chunks, like a real adapter often does.
    const cut = Math.floor(reply.length / 2);
    setTimeout(() => {
      this.onData(reply.slice(0, cut));
      setTimeout(() => this.onData(reply.slice(cut)), 1);
    }, this.delay);
  }

  respond(cmd) {
    if (cmd.startsWith('AT')) return this.at(cmd.slice(2));

    const mode = cmd.slice(0, 2);
    const pid = parseInt(cmd.slice(2, 4), 16);
    switch (mode) {
      case '01':
        return this.current(pid);
      case '02':
        return this.freeze(pid);
      case '03':
        return this.dtcReply(0x43, this.stored);
      case '04':
        this.stored = [];
        this.pending = [];
        this.freezeFrame = null;
        return [this.fmt([0x44])];
      case '07':
        return this.dtcReply(0x47, this.pending);
      case '0A':
        return this.dtcReply(0x4a, []);
      case '09':
        if (pid === 0x02) return this.vin();
        return ['NO DATA'];
      default:
        return ['?'];
    }
  }

  at(cmd) {
    if (cmd === 'Z') {
      this.echo = true;
      this.spaces = true;
      return ['', 'ELM327 v1.5'];
    }
    if (cmd === 'I') return ['ELM327 v1.5'];
    if (cmd === 'E0' || cmd === 'E1') this.echo = cmd === 'E1';
    if (cmd === 'S0' || cmd === 'S1') this.spaces = cmd === 'S1';
    if (cmd === 'RV') return [(12.4 + Math.random() * 0.3).toFixed(1) + 'V'];
    if (cmd === 'DPN') return ['A6'];
    if (cmd === 'DP') return ['AUTO, ISO 15765-4 (CAN 11/500)'];
    return ['OK'];
  }

  current(pid) {
    for (const base of [0x00, 0x20, 0x40]) {
      if (pid === base) return [this.fmt([0x41, pid, ...encodeSupportedPids(SUPPORTED, base)])];
    }
    if (!SUPPORTED.includes(pid)) return ['NO DATA'];
    const data = this.liveValue(pid);
    return [this.fmt([0x41, pid, ...data])];
  }

  liveValue(pid) {
    const t = (Date.now() - this.start) / 1000;
    const wave = (period) => (Math.sin((t * 2 * Math.PI) / period) + 1) / 2;
    const rpm = Math.round(800 + wave(9) * 2400) * 4;
    const speed = Math.round(wave(9) * 80);

    switch (pid) {
      case 0x01: {
        const mil = this.stored.length > 0 ? 0x80 : 0;
        return [mil | this.stored.length, 0x07, 0xe5, 0x00];
      }
      case 0x03:
        return [0x02, 0x00];
      case 0x04:
        return [Math.round(60 + wave(9) * 120)];
      case 0x05:
        return [Math.min(130, Math.round(60 + t * 2))];
      case 0x06:
        return [Math.round(128 + (wave(3) - 0.5) * 12)];
      case 0x07:
        return [this.stored.includes('P0171') ? 145 : 130];
      case 0x0b:
        return [Math.round(30 + wave(9) * 60)];
      case 0x0c:
        return [rpm >> 8, rpm & 0xff];
      case 0x0d:
        return [speed];
      case 0x0e:
        return [Math.round(128 + 20 + wave(5) * 20)];
      case 0x0f:
        return [72];
      case 0x10: {
        const maf = Math.round((2 + wave(9) * 25) * 100);
        return [maf >> 8, maf & 0xff];
      }
      case 0x11:
        return [Math.round(30 + wave(9) * 120)];
      case 0x1f: {
        const s = Math.round(t);
        return [s >> 8, s & 0xff];
      }
      case 0x21:
        return this.stored.length ? [0x00, 0x2a] : [0x00, 0x00];
      case 0x2f:
        return [160];
      case 0x31:
        return [0x05, 0xdc];
      case 0x33:
        return [101];
      case 0x42: {
        const mv = Math.round(14100 + wave(4) * 200);
        return [mv >> 8, mv & 0xff];
      }
      case 0x46:
        return [72];
      case 0x5c:
        return [Math.min(140, Math.round(60 + t * 1.5))];
      default:
        return [0x00];
    }
  }

  freeze(pid) {
    const ff = this.freezeFrame;
    if (!ff) return ['NO DATA'];
    if (pid === 0x02) return [this.fmt([0x42, 0x02, 0x00, ...encodeDtc(ff.dtc)])];
    if (!(pid in ff)) return ['NO DATA'];
    return [this.fmt([0x42, pid, 0x00, ...[ff[pid]].flat()])];
  }

  dtcReply(id, codes) {
    return this.canReply([id, codes.length, ...codes.flatMap(encodeDtc)]);
  }

  vin() {
    const vin = 'DEMOVIN1234567890';
    return this.canReply([0x49, 0x02, 0x01, ...[...vin].map((c) => c.charCodeAt(0))]);
  }

  // ISO 15765 replies longer than 7 bytes are split into numbered frames.
  canReply(bytes) {
    if (bytes.length <= 7) return [this.fmt(bytes)];
    const lines = [bytes.length.toString(16).toUpperCase().padStart(3, '0')];
    let offset = 0;
    for (let index = 0; offset < bytes.length; index++) {
      const size = index === 0 ? 6 : 7;
      const frame = bytes.slice(offset, offset + size);
      while (frame.length < size) frame.push(0);
      lines.push(`${(index % 16).toString(16).toUpperCase()}:${this.spaces ? ' ' : ''}${this.fmt(frame)}`);
      offset += size;
    }
    return lines;
  }

  fmt(bytes) {
    return bytes
      .map((b) => b.toString(16).toUpperCase().padStart(2, '0'))
      .join(this.spaces ? ' ' : '');
  }
}
