// Decoding of OBD-II (SAE J1979) replies as returned by an ELM327 with
// echo, spaces and headers turned off.

// Split cleaned ELM327 reply lines into messages (arrays of bytes).
// Handles the ELM327 multi-frame CAN format:
//   014
//   0:490201314434
//   1:47503030523535
export function parseMessages(lines) {
  const messages = [];
  let current = null;

  for (const raw of lines) {
    const line = raw.replace(/\s+/g, '').toUpperCase();

    if (/^[0-9A-F]{3}$/.test(line)) {
      current = { length: parseInt(line, 16), bytes: [] };
      messages.push(current);
      continue;
    }

    const frame = line.match(/^([0-9A-F]):([0-9A-F]+)$/);
    if (frame) {
      if (!current || (frame[1] === '0' && current.bytes.length > 0)) {
        current = { length: null, bytes: [] };
        messages.push(current);
      }
      current.bytes.push(...hexToBytes(frame[2]));
      continue;
    }

    if (/^([0-9A-F]{2})+$/.test(line)) {
      current = null;
      messages.push({ length: null, bytes: hexToBytes(line) });
    }
  }

  return messages.map((m) => (m.length != null ? m.bytes.slice(0, m.length) : m.bytes));
}

export function hexToBytes(hex) {
  const bytes = [];
  for (let i = 0; i + 1 < hex.length; i += 2) {
    bytes.push(parseInt(hex.slice(i, i + 2), 16));
  }
  return bytes;
}

// ELM327 protocol numbers 6-C are CAN based (ISO 15765, J1939, user CAN).
export function isCanProtocol(protocolNumber) {
  const n = parseInt(String(protocolNumber).replace(/^A/i, ''), 16);
  return n >= 6 && n <= 0xc;
}

const DTC_LETTERS = ['P', 'C', 'B', 'U'];

export function decodeDtc(a, b) {
  const letter = DTC_LETTERS[a >> 6];
  const digit = (a >> 4) & 0x3;
  const rest = (((a & 0x0f) << 8) | b).toString(16).toUpperCase().padStart(3, '0');
  return `${letter}${digit}${rest}`;
}

export function encodeDtc(code) {
  const letter = DTC_LETTERS.indexOf(code[0].toUpperCase());
  const value = (letter << 14) | (parseInt(code[1], 16) << 12) | parseInt(code.slice(2), 16);
  return [value >> 8, value & 0xff];
}

// Parse a mode 03 / 07 / 0A reply. On CAN the byte after the service id is
// the number of codes; older protocols pad every line to three codes.
export function parseDtcs(messages, mode, isCan) {
  const responseId = mode + 0x40;
  const codes = new Set();

  for (const msg of messages) {
    if (msg[0] !== responseId) continue;

    let pairs;
    if (isCan) {
      const count = msg[1] ?? 0;
      pairs = msg.slice(2, 2 + count * 2);
    } else {
      pairs = msg.slice(1);
    }

    for (let i = 0; i + 1 < pairs.length; i += 2) {
      if (pairs[i] === 0 && pairs[i + 1] === 0) continue;
      codes.add(decodeDtc(pairs[i], pairs[i + 1]));
    }
  }

  return [...codes];
}

// Data bytes for a mode 01 (current) or mode 02 (freeze frame) PID reply.
// Mode 02 replies carry the frame number before the data.
export function pidData(messages, pid, mode = 1) {
  const skip = mode === 2 ? 3 : 2;
  const msg = messages.find((m) => m[0] === mode + 0x40 && m[1] === pid);
  return msg ? msg.slice(skip) : null;
}

// PIDs 00, 20, 40 ... return a 32-bit mask of which of the next 32 PIDs exist.
// When several ECUs answer, their masks are merged.
export function parseSupportedPids(messages, base) {
  const supported = new Set();
  for (const msg of messages) {
    if (msg[0] !== 0x41 || msg[1] !== base) continue;
    msg.slice(2, 6).forEach((byte, i) => {
      for (let bit = 0; bit < 8; bit++) {
        if (byte & (0x80 >> bit)) supported.add(base + i * 8 + bit + 1);
      }
    });
  }
  return supported;
}

export function encodeSupportedPids(pids, base) {
  const bytes = [0, 0, 0, 0];
  for (const pid of pids) {
    const offset = pid - base - 1;
    if (offset < 0 || offset > 31) continue;
    bytes[offset >> 3] |= 0x80 >> (offset & 7);
  }
  return bytes;
}

// Mode 09 PID 02. Every message (one on CAN, five on older protocols) starts
// with 49 02 <sequence>, followed by ASCII characters of the VIN.
export function parseVin(messages) {
  const chars = messages
    .filter((m) => m[0] === 0x49 && m[1] === 0x02)
    .flatMap((m) => m.slice(3))
    .filter((b) => b > 0x20 && b < 0x7f)
    .map((b) => String.fromCharCode(b))
    .join('')
    .replace(/[^A-Z0-9]/gi, '');
  return chars.length >= 17 ? chars.slice(-17) : chars || null;
}

export function parseMonitorStatus(data) {
  if (!data || data.length < 1) return null;
  return { mil: Boolean(data[0] & 0x80), dtcCount: data[0] & 0x7f };
}

const temp = (a) => a - 40;
const percent = (a) => (a * 100) / 255;
const trim = (a) => ((a - 128) * 100) / 128;
const word = (a, b) => a * 256 + b;

// Mode 01 PIDs shown in the app. `digits` controls display rounding.
export const PIDS = {
  0x04: { name: 'ภาระเครื่องยนต์', unit: '%', digits: 0, decode: percent },
  0x05: { name: 'อุณหภูมิน้ำหล่อเย็น', unit: '°C', digits: 0, decode: temp },
  0x06: { name: 'Fuel Trim ระยะสั้น (B1)', unit: '%', digits: 1, decode: trim },
  0x07: { name: 'Fuel Trim ระยะยาว (B1)', unit: '%', digits: 1, decode: trim },
  0x0a: { name: 'แรงดันเชื้อเพลิง', unit: 'kPa', digits: 0, decode: (a) => a * 3 },
  0x0b: { name: 'แรงดันท่อร่วมไอดี (MAP)', unit: 'kPa', digits: 0, decode: (a) => a },
  0x0c: { name: 'รอบเครื่องยนต์', unit: 'rpm', digits: 0, decode: (a, b) => word(a, b) / 4 },
  0x0d: { name: 'ความเร็วรถ', unit: 'km/h', digits: 0, decode: (a) => a },
  0x0e: { name: 'องศาไฟจุดระเบิด', unit: '°', digits: 1, decode: (a) => a / 2 - 64 },
  0x0f: { name: 'อุณหภูมิอากาศเข้า', unit: '°C', digits: 0, decode: temp },
  0x10: { name: 'อัตราการไหลอากาศ (MAF)', unit: 'g/s', digits: 2, decode: (a, b) => word(a, b) / 100 },
  0x11: { name: 'ตำแหน่งลิ้นปีกผีเสื้อ', unit: '%', digits: 0, decode: percent },
  0x1f: { name: 'เวลาตั้งแต่ติดเครื่อง', unit: 's', digits: 0, decode: word },
  0x21: { name: 'ระยะทางขณะไฟเตือนติด', unit: 'km', digits: 0, decode: word },
  0x2f: { name: 'ระดับน้ำมันเชื้อเพลิง', unit: '%', digits: 0, decode: percent },
  0x31: { name: 'ระยะทางตั้งแต่ลบโค้ด', unit: 'km', digits: 0, decode: word },
  0x33: { name: 'ความกดอากาศ', unit: 'kPa', digits: 0, decode: (a) => a },
  0x42: { name: 'แรงดันไฟกล่อง ECU', unit: 'V', digits: 2, decode: (a, b) => word(a, b) / 1000 },
  0x46: { name: 'อุณหภูมิภายนอก', unit: '°C', digits: 0, decode: temp },
  0x5c: { name: 'อุณหภูมิน้ำมันเครื่อง', unit: '°C', digits: 0, decode: temp },
};

export function decodePid(pid, data) {
  const def = PIDS[pid];
  if (!def || !data || data.length === 0) return null;
  return def.decode(...data);
}

export function formatPid(pid, value) {
  const def = PIDS[pid];
  if (value == null || !def) return '—';
  return value.toLocaleString('th-TH', {
    minimumFractionDigits: def.digits,
    maximumFractionDigits: def.digits,
  });
}
