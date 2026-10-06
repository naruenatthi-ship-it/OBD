import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  parseMessages,
  decodeDtc,
  encodeDtc,
  parseDtcs,
  parseSupportedPids,
  encodeSupportedPids,
  parseVin,
  pidData,
  decodePid,
  isCanProtocol,
} from '../js/obd.js';
import { describeDtc } from '../js/dtc-codes.js';
import { Elm327 } from '../js/elm327.js';
import { SimulatorTransport } from '../js/simulator.js';

test('decodes DTC bytes for every system letter', () => {
  assert.equal(decodeDtc(0x01, 0x33), 'P0133');
  assert.equal(decodeDtc(0x03, 0x01), 'P0301');
  assert.equal(decodeDtc(0x41, 0x23), 'C0123');
  assert.equal(decodeDtc(0x9a, 0xbc), 'B1ABC');
  assert.equal(decodeDtc(0xc1, 0x00), 'U0100');
  assert.equal(decodeDtc(0x24, 0x63), 'P2463');
  for (const code of ['P0420', 'C0035', 'B1ABC', 'U0100', 'P2002']) {
    assert.equal(decodeDtc(...encodeDtc(code)), code);
  }
});

test('parses single-frame CAN DTC reply with count byte', () => {
  const messages = parseMessages(['430203010171']);
  assert.deepEqual(parseDtcs(messages, 0x03, true), ['P0301', 'P0171']);
});

test('parses multi-frame CAN DTC reply', () => {
  const lines = ['00A', '0:430403010171', '1:04200133000000'];
  assert.deepEqual(parseDtcs(parseMessages(lines), 0x03, true), ['P0301', 'P0171', 'P0420', 'P0133']);
});

test('parses legacy (non-CAN) DTC reply padded with zeros', () => {
  const lines = ['43 01 33 03 01 00 00', '43 04 20 00 00 00 00'];
  assert.deepEqual(parseDtcs(parseMessages(lines), 0x03, false), ['P0133', 'P0301', 'P0420']);
});

test('no codes', () => {
  assert.deepEqual(parseDtcs(parseMessages(['4300']), 0x03, true), []);
  assert.deepEqual(parseDtcs(parseMessages(['43 00 00 00 00 00 00']), 0x03, false), []);
});

test('merges supported PID masks from several ECUs', () => {
  const supported = parseSupportedPids(parseMessages(['4100BE3EB811', '410080000000']), 0x00);
  for (const pid of [0x01, 0x03, 0x04, 0x05, 0x0c, 0x0d, 0x20]) assert.ok(supported.has(pid), pid);
  assert.ok(!supported.has(0x02));

  const pids = [0x21, 0x2f, 0x40];
  assert.deepEqual([...parseSupportedPids([[0x41, 0x20, ...encodeSupportedPids(pids, 0x20)]], 0x20)], pids);
});

test('decodes common PIDs', () => {
  assert.equal(decodePid(0x0c, pidData(parseMessages(['410C1AF8']), 0x0c)), 1726);
  assert.equal(decodePid(0x05, pidData(parseMessages(['41 05 7B']), 0x05)), 83);
  assert.equal(decodePid(0x0d, pidData(parseMessages(['410D3C']), 0x0d)), 60);
  assert.equal(decodePid(0x42, pidData(parseMessages(['41423700']), 0x42)), 14.08);
  assert.equal(decodePid(0x0c, pidData(parseMessages(['420C000BB8']), 0x0c, 2)), 750);
});

test('parses VIN from CAN and legacy replies', () => {
  const can = ['014', '0:490201314434', '1:47503030523535', '2:42313233343536'];
  assert.equal(parseVin(parseMessages(can)), '1D4GP00R55B123456');

  const legacy = [
    '49020100000031',
    '49020244344750',
    '49020330305235',
    '49020435423132',
    '49020533343536',
  ];
  assert.equal(parseVin(parseMessages(legacy)), '1D4GP00R55B123456');
});

test('identifies CAN protocols', () => {
  assert.ok(isCanProtocol('A6'));
  assert.ok(isCanProtocol('8'));
  assert.ok(!isCanProtocol('3'));
  assert.ok(!isCanProtocol('A5'));
});

test('describes codes in Thai', () => {
  assert.match(describeDtc('P0302').description, /สูบ 2/);
  assert.match(describeDtc('P0141').description, /Bank 1 เซนเซอร์ 2/);
  assert.match(describeDtc('P0161').description, /ฮีตเตอร์.*Bank 2 เซนเซอร์ 2/);
  assert.ok(describeDtc('P0420').known);
  assert.ok(describeDtc('P1234').manufacturer);
  assert.ok(!describeDtc('P2002').manufacturer);
  assert.ok(describeDtc('U1234').manufacturer);
  assert.match(describeDtc('P0999').description, /ระบบเกียร์/);
});

test('full session against the simulator', async () => {
  const elm = new Elm327(new SimulatorTransport({ delay: 1 }));
  const info = await elm.initialize();
  assert.equal(info.version, 'ELM327 v1.5');
  assert.ok(info.isCan);
  assert.ok(elm.supportedPids.has(0x0c));
  assert.ok(elm.supportedPids.has(0x5c));

  assert.equal(await elm.readVin(), 'DEMOVIN1234567890');
  assert.deepEqual(await elm.readStatus(), { mil: true, dtcCount: 3 });
  assert.deepEqual(await elm.readDtcs(0x03), ['P0301', 'P0171', 'P0420']);
  assert.deepEqual(await elm.readDtcs(0x07), ['P0133']);

  const rpm = await elm.readPid(0x0c);
  assert.ok(rpm >= 800 && rpm <= 3200, rpm);

  const ff = await elm.readFreezeFrame([0x04, 0x05, 0x0c, 0x0d, 0x11, 0x10]);
  assert.equal(ff.dtc, 'P0301');
  assert.equal(ff.values[0x0c], 750);
  assert.equal(ff.values[0x0d], 60);
  assert.ok(!(0x10 in ff.values));

  assert.ok(await elm.clearDtcs());
  assert.deepEqual(await elm.readDtcs(0x03), []);
  assert.deepEqual(await elm.readStatus(), { mil: false, dtcCount: 0 });
  assert.equal(await elm.readFreezeFrame([0x0c]), null);
});

test('adapter errors become readable messages', async () => {
  const transport = new SimulatorTransport({ delay: 1 });
  transport.respond = () => ['SEARCHING...', 'UNABLE TO CONNECT'];
  const elm = new Elm327(transport);
  await assert.rejects(elm.query('0100'), /ECU/);
});
