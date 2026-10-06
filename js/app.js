import { Elm327 } from './elm327.js';
import { BleTransport, SerialTransport } from './transports.js';
import { SimulatorTransport } from './simulator.js';
import { PIDS, formatPid } from './obd.js';
import { describeDtc } from './dtc-codes.js';

// Live data order; only PIDs the car reports as supported are polled.
const LIVE_PIDS = [0x0c, 0x0d, 0x05, 0x04, 0x11, 0x0f, 0x10, 0x0b, 0x0e, 0x06, 0x07, 0x2f, 0x42, 0x5c, 0x46, 0x33, 0x1f];
const BIG_PIDS = new Set([0x0c, 0x0d]);
const FREEZE_PIDS = [0x04, 0x05, 0x06, 0x07, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10, 0x11];

const $ = (id) => document.getElementById(id);

let elm = null;
let transport = null;
let polling = false;

function setStatus(state, text) {
  $('status').dataset.state = state;
  $('status').textContent = text;
}

function showError(err) {
  const message = typeof err === 'string' ? err : friendlyError(err);
  $('error').textContent = message;
  $('error').classList.toggle('hidden', !message);
}

function friendlyError(err) {
  if (!err) return '';
  if (err.name === 'NotFoundError') return 'ไม่ได้เลือกอุปกรณ์';
  if (err.name === 'SecurityError') return 'เบราว์เซอร์ไม่อนุญาต — ต้องเปิดหน้านี้ผ่าน https:// หรือ localhost';
  if (err.name === 'NetworkError') return 'เชื่อมต่ออุปกรณ์ไม่สำเร็จ — ลองถอดเสียบ ELM327 แล้วลองใหม่';
  return err.message || String(err);
}

function log(direction, text) {
  const el = $('log');
  const time = new Date().toLocaleTimeString('th-TH', { hour12: false });
  el.textContent += `${time} ${direction === 'tx' ? '→' : '←'} ${text.replace(/[\r\n]+/g, ' | ')}\n`;
  if (el.textContent.length > 40000) el.textContent = el.textContent.slice(-30000);
  el.scrollTop = el.scrollHeight;
}

function setBusy(busy) {
  for (const id of ['btn-read', 'btn-clear']) $(id).disabled = busy;
}

function showConnected(connected) {
  $('connect-buttons').classList.toggle('hidden', connected);
  $('connected-buttons').classList.toggle('hidden', !connected);
  for (const id of ['summary-card', 'dtc-card', 'live-card']) {
    $(id).classList.toggle('hidden', !connected);
  }
  if (!connected) $('freeze-card').classList.add('hidden');
}

async function connect(makeTransport) {
  showError('');
  try {
    transport = makeTransport();
    setStatus('busy', 'กำลังเชื่อมต่อ…');
    await transport.connect();
    transport.onDisconnect = () => handleDisconnect('อุปกรณ์ถูกตัดการเชื่อมต่อ');
    elm = new Elm327(transport, { log });

    setStatus('busy', 'กำลังค้นหาโปรโตคอลรถ…');
    const info = await elm.initialize();
    log('rx', `เชื่อมต่อแล้ว: ${info.version}, ${info.protocol}`);

    $('device-name').textContent = transport.label;
    $('fact-protocol').textContent = info.protocol;
    showConnected(true);
    buildTiles();
    setStatus('ok', 'เชื่อมต่อแล้ว');

    await refreshSummary();
    await readCodes();
  } catch (err) {
    console.error(err);
    showError(err);
    await disconnect();
  }
}

async function disconnect() {
  polling = false;
  const t = transport;
  transport = null;
  elm = null;
  if (t) {
    t.onDisconnect = () => {};
    await t.disconnect().catch(() => {});
  }
  showConnected(false);
  resetSummary();
  setStatus('idle', 'ยังไม่เชื่อมต่อ');
  $('btn-live').textContent = 'เริ่มอ่านค่า';
}

function handleDisconnect(reason) {
  if (!transport) return;
  disconnect();
  showError(reason);
}

function resetSummary() {
  for (const id of ['mil-state', 'fact-count', 'fact-volt', 'fact-protocol', 'fact-vin']) {
    $(id).textContent = '—';
  }
  delete $('fact-vin').dataset.loaded;
  $('mil').dataset.on = 'false';
  $('mil').classList.remove('known');
  $('dtc-results').innerHTML = '<p class="empty">กด “อ่านโค้ด” เพื่อดึงโค้ดปัญหาจากกล่อง ECU</p>';
  $('freeze-results').replaceChildren();
}

async function refreshSummary() {
  const [status, volt, vin] = [
    await elm.readStatus().catch(() => null),
    await elm.readVoltage().catch(() => null),
    $('fact-vin').dataset.loaded ? null : await elm.readVin(),
  ];

  const mil = $('mil');
  if (status) {
    mil.dataset.on = String(status.mil);
    mil.classList.add('known');
    $('mil-state').textContent = status.mil ? 'ติด (Check Engine)' : 'ดับ — ปกติ';
    $('fact-count').textContent = `${status.dtcCount} รายการ`;
  }
  if (volt != null) $('fact-volt').textContent = `${volt.toFixed(1)} V`;
  if (vin) {
    $('fact-vin').textContent = vin;
    $('fact-vin').dataset.loaded = '1';
  } else if (!$('fact-vin').dataset.loaded) {
    $('fact-vin').textContent = 'รถไม่รองรับการอ่าน VIN';
  }
}

const GROUPS = [
  { mode: 0x03, title: 'โค้ดที่บันทึกไว้ (ทำให้ไฟเตือนติด)', className: '' },
  { mode: 0x07, title: 'โค้ดรอยืนยัน (ตรวจพบครั้งแรก ยังไม่ติดไฟเตือน)', className: 'pending' },
  { mode: 0x0a, title: 'โค้ดถาวร (ลบไม่ได้ จะหายเองเมื่อระบบตรวจซ้ำแล้วผ่าน)', className: '' },
];

async function readCodes() {
  if (!elm) return;
  setBusy(true);
  showError('');
  const results = $('dtc-results');
  results.innerHTML = '<p class="empty">กำลังอ่านโค้ด…</p>';

  try {
    const groups = [];
    for (const group of GROUPS) {
      let codes = [];
      try {
        codes = await elm.readDtcs(group.mode);
      } catch (err) {
        // Mode 0A only exists on 2010+ cars; older ones answer with an error.
        if (group.mode !== 0x0a) throw err;
      }
      if (codes.length) groups.push({ ...group, codes });
    }

    results.replaceChildren();
    if (!groups.length) {
      results.innerHTML = '<div class="all-clear">✓ ไม่พบโค้ดปัญหา</div>';
    }
    for (const group of groups) results.append(renderGroup(group));

    await readFreezeFrame();
    await refreshSummary();
  } catch (err) {
    results.innerHTML = '<p class="empty">อ่านโค้ดไม่สำเร็จ</p>';
    showError(err);
  } finally {
    setBusy(false);
  }
}

function renderGroup({ title, codes, className }) {
  const wrap = document.createElement('div');
  wrap.className = 'dtc-group';
  const heading = document.createElement('h3');
  heading.textContent = `${title} · ${codes.length}`;
  const list = document.createElement('ul');
  list.className = 'dtc-list';

  for (const code of codes) {
    const info = describeDtc(code);
    const item = document.createElement('li');
    item.className = `dtc ${className}`;
    item.innerHTML = `
      <span class="code"></span>
      <span class="desc"></span>
      <span class="sys"></span>
      ${info.tip ? '<span class="tip"></span>' : ''}`;
    item.querySelector('.code').textContent = code;
    item.querySelector('.desc').textContent = info.description;
    item.querySelector('.sys').textContent = info.system + ' · ';
    const link = document.createElement('a');
    link.className = 'search-link';
    link.href = `https://www.google.com/search?q=${encodeURIComponent(code + ' OBD')}`;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'ค้นหาเพิ่มเติม';
    item.querySelector('.sys').append(link);
    if (info.tip) item.querySelector('.tip').textContent = info.tip;
    list.append(item);
  }

  wrap.append(heading, list);
  return wrap;
}

async function readFreezeFrame() {
  const card = $('freeze-card');
  const pids = FREEZE_PIDS.filter((p) => elm.supportedPids.has(p));
  const frame = await elm.readFreezeFrame(pids).catch(() => null);
  if (!frame) {
    card.classList.add('hidden');
    return;
  }

  const tiles = document.createElement('div');
  tiles.className = 'tiles';
  for (const [pid, value] of Object.entries(frame.values)) {
    tiles.append(makeTile(Number(pid), value));
  }

  const caption = document.createElement('p');
  caption.className = 'empty';
  caption.textContent = `บันทึกเมื่อเกิดโค้ด ${frame.dtc} — ${describeDtc(frame.dtc).description}`;
  caption.style.marginBottom = '10px';

  $('freeze-results').replaceChildren(caption, tiles);
  card.classList.remove('hidden');
}

async function clearCodes() {
  if (!elm) return;
  const ok = confirm(
    'ลบโค้ดปัญหาและดับไฟเตือนเครื่องยนต์?\n\n' +
      '• ควรบิดกุญแจ ON แต่ไม่ติดเครื่อง\n' +
      '• ข้อมูล Freeze Frame และผลตรวจความพร้อม (Readiness) จะถูกล้างด้วย ' +
      'รถต้องขับอีกระยะก่อนจะผ่านการตรวจสภาพ\n' +
      '• ถ้ายังไม่ได้ซ่อม โค้ดจะกลับมาอีก',
  );
  if (!ok) return;

  setBusy(true);
  try {
    const cleared = await elm.clearDtcs();
    if (!cleared) throw new Error('กล่อง ECU ไม่ยืนยันการลบโค้ด — ลองบิดกุญแจ ON โดยไม่ติดเครื่อง');
    log('rx', 'ลบโค้ดเรียบร้อย');
  } catch (err) {
    showError(err);
  } finally {
    setBusy(false);
  }
  await readCodes();
}

function makeTile(pid, value) {
  const def = PIDS[pid];
  const tile = document.createElement('div');
  tile.className = 'tile' + (BIG_PIDS.has(pid) ? ' big' : '');
  tile.dataset.pid = pid;
  tile.innerHTML = '<div class="label"></div><div><span class="value"></span><span class="unit"></span></div>';
  tile.querySelector('.label').textContent = def.name;
  tile.querySelector('.label').title = def.name;
  tile.querySelector('.value').textContent = formatPid(pid, value);
  tile.querySelector('.unit').textContent = def.unit;
  return tile;
}

function buildTiles() {
  const pids = LIVE_PIDS.filter((p) => elm.supportedPids.has(p));
  $('tiles').replaceChildren(...pids.map((pid) => makeTile(pid, null)));
  if (!pids.length) {
    $('tiles').innerHTML = '<p class="empty">รถคันนี้ไม่รายงานค่าเซนเซอร์มาตรฐาน</p>';
  }
}

async function toggleLive() {
  if (polling) {
    polling = false;
    return;
  }
  polling = true;
  $('btn-live').textContent = 'หยุดอ่านค่า';

  const pids = LIVE_PIDS.filter((p) => elm?.supportedPids.has(p));
  let round = 0;
  try {
    while (polling && elm) {
      for (const pid of pids) {
        if (!polling || !elm) break;
        // Fast-changing values every round, slow ones every fourth round.
        if (round % 4 !== 0 && !BIG_PIDS.has(pid) && ![0x04, 0x11, 0x10, 0x0b].includes(pid)) continue;
        const value = await elm.readPid(pid).catch(() => null);
        const el = $('tiles').querySelector(`.tile[data-pid="${pid}"] .value`);
        if (el) el.textContent = formatPid(pid, value);
      }
      if (round % 8 === 0 && elm) {
        const volt = await elm.readVoltage().catch(() => null);
        if (volt != null) $('fact-volt').textContent = `${volt.toFixed(1)} V`;
      }
      round++;
    }
  } catch (err) {
    showError(err);
  }
  polling = false;
  $('btn-live').textContent = 'เริ่มอ่านค่า';
}

function checkBrowser() {
  const notes = [];
  const ble = BleTransport.isSupported();
  const serial = SerialTransport.isSupported();
  $('btn-ble').disabled = !ble;
  $('btn-serial').disabled = !serial;

  if (!window.isSecureContext) {
    notes.push('ต้องเปิดหน้านี้ผ่าน https:// หรือ http://localhost จึงจะเชื่อมต่ออุปกรณ์ได้');
  } else if (!ble && !serial) {
    const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
    notes.push(
      ios
        ? 'Safari บน iPhone/iPad ยังไม่รองรับ Bluetooth บนเว็บ — ให้ติดตั้งแอปเบราว์เซอร์ “Bluefy” แล้วเปิดหน้านี้ผ่าน Bluefy'
        : 'เบราว์เซอร์นี้ไม่รองรับการเชื่อมต่ออุปกรณ์ — ให้ใช้ Google Chrome หรือ Microsoft Edge',
    );
  } else if (!ble) {
    notes.push('เบราว์เซอร์นี้ไม่รองรับ Bluetooth บนเว็บ ใช้ได้เฉพาะ USB / Serial');
  }

  if (notes.length) {
    $('browser-notice').textContent = notes.join(' ');
    $('browser-notice').classList.remove('hidden');
  }
}

$('btn-ble').addEventListener('click', () => connect(() => new BleTransport()));
$('btn-serial').addEventListener('click', () =>
  connect(() => new SerialTransport({ baudRate: Number($('baud').value) })),
);
$('btn-demo').addEventListener('click', () => connect(() => new SimulatorTransport()));
$('btn-disconnect').addEventListener('click', () => {
  showError('');
  disconnect();
});
$('btn-read').addEventListener('click', readCodes);
$('btn-clear').addEventListener('click', clearCodes);
$('btn-live').addEventListener('click', toggleLive);

checkBrowser();
