"""Scanner-style pages built from files the other commands already saved:

- topology: every ECU found by `ecus`, coloured by the codes from `dtc`
- dyno: power and torque against speed, from drive recordings
- efficiency map: motor efficiency by rpm and torque, from the same files
- charge curve: charge power, temperatures and cell voltages against SOC

Nothing here talks to the car.
"""

import csv
import datetime
import glob
import html
import json
import math
import os

from . import dtc, pids
from .report import nice_ticks, tick_label

# ECUs whose job is confirmed on a real car (see docs/TODO.md)
ECU_NAMES = {
    "deepal-s05": {
        "7A1": "แบตเตอรี่ไฟแรงสูง (BMS)",
        "7A7": "มอเตอร์ / อินเวอร์เตอร์ (EDS2)",
    },
}

CSS = """
:root { --bg:#f2f5f4; --surface:#fff; --ink:#16211f; --muted:#5c6b68;
  --line:#d5dddb; --s1:#0d7a70; --s2:#c2601b; --s3:#2c68b0; --s4:#8a4fb5;
  --ok:#1f8a4c; --warn:#b7791f; --bad:#c0392b; --none:#8a9794;
  --okbg:#e3f4ea; --warnbg:#fbf0dc; --badbg:#fbe4e1; --nonebg:#eef1f0;
  color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1514; --surface:#17201e;
  --ink:#e2eae8; --muted:#94a4a0; --line:#2b3835; --s1:#3fc0b2;
  --s2:#f0954f; --s3:#6ea5ec; --s4:#c193e6; --ok:#4cc985; --warn:#e5b04f;
  --bad:#f0776a; --none:#7d8a87; --okbg:#173326; --warnbg:#3a2f17;
  --badbg:#3d1f1c; --nonebg:#1e2826; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans Thai","Noto Sans Thai",Tahoma,sans-serif;
  padding:20px 16px 40px; line-height:1.5; }
main { max-width:1100px; margin:0 auto; display:grid; gap:16px; }
h1 { margin:0; font-size:1.4rem; } h2 { margin:0 0 6px; font-size:1rem; }
a { color:var(--s3); }
.muted { color:var(--muted); font-size:.88rem; }
.card { background:var(--surface); border:1px solid var(--line);
  border-radius:12px; padding:14px; min-width:0; }
.grid { display:grid; gap:16px;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr)); }
.stats { display:flex; flex-wrap:wrap; gap:10px; }
.stat { background:var(--surface); border:1px solid var(--line);
  border-radius:12px; padding:10px 14px; min-width:120px; }
.stat b { display:block; font-size:1.5rem; font-variant-numeric:tabular-nums; }
svg { width:100%; height:auto; display:block; }
svg text { fill:var(--muted); font:11px ui-monospace,Menlo,Consolas,monospace; }
svg .grid { stroke:var(--line); stroke-width:1; }
table { border-collapse:collapse; width:100%; font-size:.88rem;
  font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-weight:500; }
.table { overflow-x:auto; }
"""


def page(title, body, now=None):
    now = now or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return ('<!doctype html><html lang="th"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, '
            'initial-scale=1"><title>%s</title><style>%s</style></head>'
            '<body><main><header><h1>%s</h1><div class="muted">สร้างเมื่อ %s'
            ' · <a href="/">หน้าหลัก</a> · <a href="/files">ไฟล์</a></div>'
            '</header>%s</main></body></html>'
            % (html.escape(title), CSS, html.escape(title), now, body))


def num(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def latest(folder, pattern):
    files = glob.glob(os.path.join(folder, pattern))
    return max(files, key=os.path.getmtime) if files else None


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- charts

def plot(series, xlabel, ylabel, x_range=None, H=260):
    """series: [(label, color, [(x, y)], "line" | "dots")] -> SVG"""
    pts = [p for _, _, s, _ in series for p in s]
    if not pts:
        return ""
    W, L, R, T, B = 520, 48, 14, 12, 34  # narrow: text stays readable
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xt = nice_ticks(*(x_range or (min(xs), max(xs))))
    yt = nice_ticks(min(min(ys), 0) if min(ys) >= 0 and max(ys) > 0 and
                    min(ys) < max(ys) * 0.3 else min(ys), max(ys))
    x0, x1, y0, y1 = xt[0], xt[-1], yt[0], yt[-1]

    def X(v):
        return L + (v - x0) / (x1 - x0) * (W - L - R)

    def Y(v):
        return T + (1 - (v - y0) / (y1 - y0)) * (H - T - B)

    out = ['<svg viewBox="0 0 %d %d" role="img">' % (W, H)]
    for v in yt:
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>'
                   '<text x="%d" y="%.1f" text-anchor="end">%s</text>'
                   % (L, W - R, Y(v), Y(v), L - 6, Y(v) + 4, tick_label(v)))
    for v in xt:
        out.append('<text x="%.1f" y="%d" text-anchor="middle">%s</text>'
                   % (X(v), H - 14, tick_label(v)))
    out.append('<text x="%d" y="%d" text-anchor="end">%s</text>'
               % (W - R, H - 1, html.escape(xlabel)))
    out.append('<text x="4" y="%d">%s</text>' % (T + 4, html.escape(ylabel)))
    for label, color, s, kind in series:
        if not s:
            continue
        if kind == "dots":
            out.append('<g fill="%s" fill-opacity=".35">%s</g>' % (color, "".join(
                '<circle cx="%.1f" cy="%.1f" r="2.5"/>' % (X(x), Y(y))
                for x, y in s)))
        else:
            d = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(x), Y(y))
                         for i, (x, y) in enumerate(s))
            out.append('<path d="%s" fill="none" stroke="%s" '
                       'stroke-width="2.5"/>' % (d, color))
    out.append("</svg>")
    return "".join(out)


def legend(items):
    return '<div class="muted">%s</div>' % " · ".join(
        '<span style="color:%s">●</span> %s' % (c, html.escape(t))
        for t, c in items)


def card(title, inner, note=""):
    if not inner:
        return ""
    return '<section class="card"><h2>%s</h2>%s%s</section>' % (
        html.escape(title), inner,
        '<p class="muted">%s</p>' % note if note else "")


# -------------------------------------------------------------- topology

def ecu_status(codes):
    """'bad' (a code failing right now), 'warn' (other active codes),
    'ok' (none) or 'none' (no DTC reading)."""
    if codes is None:
        return "none"
    statuses = [c["status"] for c in codes]
    if any(s & dtc.TEST_FAILED for s in statuses):
        return "bad"
    if any(s & dtc.ACTIVE_MASK for s in statuses):
        return "warn"
    return "ok"


STATUS_TEXT = {"bad": "มีโค้ดที่กำลังเกิดอยู่", "warn": "มีโค้ดเก่า/รอยืนยัน",
               "ok": "ปกติ ไม่มีโค้ด", "none": "ยังไม่ได้อ่านโค้ด"}


def load_topology(folder):
    """ECUs from the newest ecus_*.csv joined with the newest DTC reading
    (dtc_*.csv, or the last scan in dtc_history.json)."""
    ecus = {}
    path = latest(folder, "ecus_*.csv")
    if path:
        for r in read_csv(path):
            ecus[r["header"].upper()] = dict(r)
    codes, dtc_time, dtc_path = None, None, latest(folder, "dtc_*.csv")
    hist = dtc.load_history(os.path.join(folder, "dtc_history.json"))
    hist_path = os.path.join(folder, "dtc_history.json")
    use_hist = hist and (not dtc_path or os.path.getmtime(hist_path) >
                         os.path.getmtime(dtc_path))
    answered = set()
    if use_hist:
        scan = hist[-1]
        codes, dtc_time = {}, scan.get("time")
        answered = set(scan.get("answered", []))
        for c in scan["codes"]:
            codes.setdefault(c["header"], []).append({
                "code": c["code"], "status": c["status"],
                "status_text": dtc.status_text(c["status"]),
                "description": dtc.describe(c["code"])})
    elif dtc_path:
        codes = {}
        dtc_time = datetime.datetime.fromtimestamp(
            os.path.getmtime(dtc_path)).strftime("%Y-%m-%d %H:%M")
        for r in read_csv(dtc_path):
            codes.setdefault(r["header"].upper(), []).append({
                "code": r["code"], "status": int(r["status"], 16),
                "status_text": r.get("status_text", ""),
                "description": r.get("description", "")})
        # an ECU found by `ecus` that reported no codes still answered
        answered = set(ecus)
    for h in set(codes or {}) | answered:
        ecus.setdefault(h, {"header": h})
    for h, e in ecus.items():
        if codes is None or (h not in answered and h not in codes):
            e["codes"] = None
        else:
            e["codes"] = codes.get(h, [])
        e["state"] = ecu_status(e["codes"])
    return {"ecus": [ecus[h] for h in sorted(ecus)],
            "ecus_file": os.path.basename(path) if path else None,
            "dtc_time": dtc_time}


def ecu_card(e, names):
    state = e["state"]
    name = names.get(e["header"]) or e.get("system_name") or ""
    part = e.get("part_number") or ""
    rows = [(label, e.get(key)) for key, label in (
        ("part_number", "รหัสชิ้นส่วน"), ("supplier", "ผู้ผลิต"),
        ("system_name", "ชื่อระบบ"), ("hw_version", "ฮาร์ดแวร์"),
        ("sw_version", "ซอฟต์แวร์"), ("response", "ตอบกลับทาง"))]
    info = "".join("<tr><th>%s</th><td>%s</td></tr>" % (
        k, html.escape(v)) for k, v in rows if v)
    codes = "".join(
        '<li><b>%s</b> %s<br><span class="muted">%s</span></li>' % (
            html.escape(c["code"]), html.escape(c.get("description") or ""),
            html.escape(c.get("status_text") or "")) for c in e["codes"] or [])
    count = len(e["codes"] or [])
    return (
        '<details class="ecu %s"><summary><span class="dot"></span>'
        '<span class="hdr">%s</span><span class="nm">%s<small>%s</small></span>'
        '<span class="cnt">%s</span></summary><div class="body">'
        '<table>%s</table>%s</div></details>' % (
            state, html.escape(e["header"]),
            html.escape(name or "กล่อง %s" % e["header"]),
            html.escape(part), "%d โค้ด" % count if count else
            STATUS_TEXT[state] if state != "ok" else "ปกติ",
            info or '<tr><td class="muted">ไม่มีข้อมูลรหัสกล่อง</td></tr>',
            '<ul class="codes">%s</ul>' % codes if codes else ""))


TOPO_CSS = """
.bus { position:relative; padding-left:26px; display:grid; gap:8px; }
.bus::before { content:""; position:absolute; left:9px; top:-8px; bottom:14px;
  width:4px; border-radius:2px; background:var(--line); }
.obd { font-weight:600; margin:0 0 10px; display:flex; gap:8px;
  align-items:center; }
.obd span { background:var(--ink); color:var(--bg); border-radius:8px;
  padding:2px 10px; font-size:.85rem; }
.ecu { position:relative; background:var(--surface); border:1px solid var(--line);
  border-left:6px solid var(--c); border-radius:10px; }
.ecu::before { content:""; position:absolute; left:-23px; top:20px; width:17px;
  height:3px; background:var(--line); }
.ecu.ok { --c:var(--ok); } .ecu.warn { --c:var(--warn); background:var(--warnbg); }
.ecu.bad { --c:var(--bad); background:var(--badbg); } .ecu.none { --c:var(--none); }
.ecu summary { list-style:none; cursor:pointer; display:flex; gap:10px;
  align-items:center; padding:9px 12px; }
.ecu summary::-webkit-details-marker { display:none; }
.dot { width:12px; height:12px; border-radius:50%; background:var(--c);
  flex:none; }
.hdr { font:600 .95rem ui-monospace,Menlo,Consolas,monospace; flex:none; }
.nm { flex:1; min-width:0; } .nm small { display:block; color:var(--muted); }
.cnt { color:var(--c); font-size:.85rem; font-weight:600; text-align:right; }
.body { padding:0 12px 12px; } .codes { margin:8px 0 0; padding-left:18px; }
.groups { display:grid; gap:16px;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); }
"""


def topology_html(topo, car="deepal-s05"):
    names = ECU_NAMES.get(car, {})
    ecus = topo["ecus"]
    if not ecus:
        return page("แผนผังกล่อง ECU", '<section class="card">ยังไม่มีข้อมูล'
                    ' ให้รัน <code>obd topo</code> (หรือ ecus แล้ว dtc) ก่อน'
                    '</section>')
    counts = {s: sum(1 for e in ecus if e["state"] == s) for s in STATUS_TEXT}
    stats = "".join(
        '<div class="stat" style="color:var(--%s)"><b>%d</b>%s</div>' % (
            {"ok": "ok", "warn": "warn", "bad": "bad", "none": "none"}[s],
            counts[s], STATUS_TEXT[s]) for s in ("bad", "warn", "ok", "none")
        if counts[s] or s != "none")
    hv = [e for e in ecus if (e.get("part_number") or "").startswith("21")
          or e["header"] in names]
    other = [e for e in ecus if e not in hv]
    groups = []
    for title, items in (("ระบบขับเคลื่อนไฟฟ้า (รหัสกลุ่ม 21xx)", hv),
                         ("กล่องอื่นๆ ในรถ", other)):
        if items:
            groups.append(
                '<section class="card"><h2>%s · %d กล่อง</h2>'
                '<div class="obd"><span>OBD</span>สาย CAN</div><div class="bus">'
                '%s</div></section>' % (html.escape(title), len(items),
                                        "".join(ecu_card(e, names)
                                                for e in items)))
    src = "ECU จาก %s · โค้ดจาก %s" % (
        topo["ecus_file"] or "-", topo["dtc_time"] or "ยังไม่ได้อ่าน")
    body = ('<style>%s</style><div class="stats"><div class="stat"><b>%d</b>'
            'กล่องที่ตอบ</div>%s</div><div class="groups">%s</div>'
            '<p class="muted">%s · กดที่กล่องเพื่อดูรายละเอียด · ชื่อกล่องที่ใส่ไว้'
            'คือที่ยืนยันกับรถจริงแล้ว นอกนั้นแสดงรหัสชิ้นส่วนให้เทียบเอง · '
            'สีเหลืองส่วนมากเป็นโค้ดเก่าหรือขาดการสื่อสารชั่วคราว (U-code)'
            ' ไม่ได้แปลว่าเสีย</p>' % (TOPO_CSS, len(ecus), stats,
                                       "".join(groups), html.escape(src)))
    return page("แผนผังกล่อง ECU", body)


def write_topology(folder, out, car="deepal-s05"):
    with open(out, "w", encoding="utf-8") as f:
        f.write(topology_html(load_topology(folder), car))
    return out


# -------------------------------------------------------- dyno, eff. map

def drive_samples(rows):
    """[(rpm, torque Nm, pack V, pack A)] from record/drive CSV rows. Raw
    motor DIDs (record --dids-from) are decoded with the same formulas as
    pids.MOTOR_SIGNALS."""
    out = []
    for r in rows:
        rpm = num(r.get("motor_rpm"))
        if rpm is None and r.get("did_7A7_FD07"):
            rpm = int(r["did_7A7_FD07"][:4], 16) - 20000
        tq = num(r.get("motor_torque"))
        if tq is None and r.get("did_7A7_FD08"):
            tq = (int(r["did_7A7_FD08"][:4], 16) - 4000) / 10
        v = num(r.get("pack_voltage"))
        if v is None and r.get("did_7A7_FD1A"):
            v = float(int(r["did_7A7_FD1A"][:4], 16))
        i = num(r.get("pack_current"))
        if None in (rpm, tq, v, i):
            continue
        out.append((rpm, tq, v, i))
    return out


def mech_kw(rpm, torque):
    return torque * rpm * 2 * math.pi / 60 / 1000


def envelope(points, step):
    """Highest y in each x bin of width `step` -> [(bin centre, y)]."""
    best = {}
    for x, y in points:
        b = int(x // step)
        if b not in best or y > best[b]:
            best[b] = y
    return [((b + 0.5) * step, best[b]) for b in sorted(best)]


def efficiency_cells(samples, rpm_step=1000, tq_step=25, min_kw=3.0):
    """Mean efficiency per (rpm bin, torque bin): driving = mechanical /
    battery power, regen = battery / mechanical. -> {(rb, tb): (eff, n)}"""
    acc = {}
    for rpm, tq, v, i in samples:
        pm, pe = mech_kw(abs(rpm), tq), v * i / 1000
        if pm > min_kw and pe > min_kw:
            eff = pm / pe
        elif pm < -min_kw and pe < -min_kw:
            eff = pe / pm
        else:
            continue
        if not 0.3 <= eff <= 1.05:  # readings taken at different moments
            continue
        key = (int(abs(rpm) // rpm_step), math.floor(tq / tq_step))
        s, n = acc.get(key, (0.0, 0))
        acc[key] = (s + eff, n + 1)
    return {k: (s / n, n) for k, (s, n) in acc.items()}


def eff_color(eff):
    """Sequential scale from 60 % (amber) to 95 % (teal)."""
    t = min(max((eff - 0.6) / 0.35, 0), 1)
    a, b = (214, 140, 40), (13, 140, 120)
    return "rgb(%d,%d,%d)" % tuple(round(a[k] + (b[k] - a[k]) * t)
                                   for k in range(3))


def efficiency_svg(cells, rpm_step=1000, tq_step=25):
    if not cells:
        return ""
    rb = [k[0] for k in cells]
    tb = [k[1] for k in cells]
    r0, r1 = 0, max(rb) + 1
    t0, t1 = min(min(tb), 0), max(max(tb), 0) + 1
    W, L, R, T, B = 720, 56, 14, 12, 34
    cw = (W - L - R) / (r1 - r0)
    ch = max(14, min(34, 300 / (t1 - t0)))
    H = T + B + ch * (t1 - t0)
    out = ['<svg viewBox="0 0 %d %d" role="img">' % (W, H)]
    for (r, t), (eff, n) in cells.items():
        x = L + (r - r0) * cw
        y = T + (t1 - 1 - t) * ch
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                   'rx="3" fill="%s"><title>%d-%d rpm, %d..%d Nm: %.0f%% '
                   '(%d จุด)</title></rect>' % (
                       x + 1, y + 1, cw - 2, ch - 2, eff_color(eff),
                       r * rpm_step, (r + 1) * rpm_step, t * tq_step,
                       (t + 1) * tq_step, eff * 100, n))
        if cw > 34 and ch >= 14:
            out.append('<text x="%.1f" y="%.1f" text-anchor="middle" '
                       'style="fill:#fff">%.0f</text>' % (
                           x + cw / 2, y + ch / 2 + 4, eff * 100))
    for t in range(t0, t1 + 1):
        y = T + (t1 - t) * ch
        cls = ' style="stroke:var(--muted)"' if t == 0 else ""
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"%s/>'
                   % (L, W - R, y, y, cls))
        if (t - t0) % max(1, round((t1 - t0) / 8)) == 0:
            out.append('<text x="%d" y="%.1f" text-anchor="end">%d</text>'
                       % (L - 6, y + 4, t * tq_step))
    for r in range(r0, r1 + 1):
        if (r - r0) % max(1, round((r1 - r0) / 8)) == 0:
            out.append('<text x="%.1f" y="%.1f" text-anchor="%s">%d</text>'
                       % (L + r * cw, H - 14,
                          "end" if r == r1 else "middle", r * rpm_step))
    out.append('<text x="%d" y="%.1f" text-anchor="end">รอบมอเตอร์ rpm</text>'
               % (W - R, H - 1))
    out.append('<text x="4" y="%d">Nm</text>' % (T + 4))
    out.append("</svg>")
    return "".join(out)


def dyno_html(paths):
    samples = []
    used = []
    for p in paths:
        try:
            s = drive_samples(read_csv(p))
        except (OSError, csv.Error):
            continue
        if s:
            samples += s
            used.append(os.path.basename(p))
    if not samples:
        return page("ไดโนจำลอง", '<section class="card">ยังไม่มีไฟล์บันทึกขับที่มี'
                    'รอบและแรงบิดมอเตอร์ บันทึกด้วย <code>obd rec drive 20</code>'
                    ' (ให้คนอื่นขับ) แล้วเปิดหน้านี้ใหม่</section>')
    speed = [(abs(r) / pids.RPM_PER_KMH, mech_kw(abs(r), t), t, v * i / 1000)
             for r, t, v, i in samples]
    mech = [(s, m) for s, m, _, _ in speed]
    elec = [(s, e) for s, _, _, e in speed]
    torque = [(s, t) for s, _, t, _ in speed]
    peak_kw = max(m for _, m in mech)
    peak_bat = max(e for _, e in elec)
    peak_tq = max(t for _, t in torque)
    peak_regen = min(e for _, e in elec)
    at = max(speed, key=lambda x: x[1])[0]
    stats = "".join('<div class="stat"><b>%s</b>%s</div>' % (v, t) for v, t in (
        ("%.0f kW" % peak_kw, "กำลังมอเตอร์สูงสุด (≈ %.0f แรงม้า)" % (
            peak_kw * 1.341)),
        ("%.0f Nm" % peak_tq, "แรงบิดสูงสุด"),
        ("%.0f kW" % peak_bat, "ดึงจากแบตสูงสุด"),
        ("%.0f kW" % -peak_regen, "ชาร์จกลับ (regen) สูงสุด"),
        ("%.0f km/h" % at, "ความเร็วตอนได้กำลังสูงสุด")))
    c1, c2, c3 = "var(--s1)", "var(--s2)", "var(--s3)"
    power = plot([("", c1, mech, "dots"), ("", c2, elec, "dots"),
                  ("", c1, envelope(mech, 5), "line"),
                  ("", c2, envelope(elec, 5), "line")], "km/h", "kW")
    torq = plot([("", c3, torque, "dots"),
                 ("", c3, envelope(torque, 5), "line")], "km/h", "Nm")
    cells = efficiency_cells(samples)
    eff = efficiency_svg(cells)
    scale = "".join('<span style="display:inline-block;padding:0 8px;'
                    'color:#fff;background:%s">%d%%</span>' % (eff_color(e / 100), e)
                    for e in (60, 70, 80, 90, 95))
    body = (
        '<div class="stats">%s</div><div class="grid">%s%s</div>%s'
        '<p class="muted">จาก %d จุดใน %s · ค่าจากมอเตอร์ 7A7 ยังเป็นสูตรทดลอง'
        ' (CANDIDATE) · ความเร็วคำนวณจากรอบมอเตอร์ ÷ %.1f ตามมาตรวัด</p>' % (
            stats,
            card("กำลัง (kW) เทียบความเร็ว", legend([
                ("ที่มอเตอร์ = แรงบิด × รอบ", c1), ("จากแบต = V × I", c2)])
                + power, "เส้นคือค่าสูงสุดที่เคยทำได้ในแต่ละช่วงความเร็ว"
                " ยิ่งมีบันทึกตอนเร่งสุดมาก เส้นยิ่งใกล้กราฟไดโนจริง"
                " (เร่งเต็มที่เฉพาะที่ปลอดภัยและถูกกฎหมาย)"),
            card("แรงบิด (Nm) เทียบความเร็ว", torq,
                 "แรงบิดติดลบ = เบรกด้วยมอเตอร์ (regen)"),
            card("แผนที่ประสิทธิภาพมอเตอร์ + อินเวอร์เตอร์", eff + (
                '<div class="muted" style="margin-top:6px">%s</div>' % scale),
                "ช่องบน = ตอนขับ (กำลังที่มอเตอร์ ÷ กำลังจากแบต) · ช่องใต้ 0 Nm"
                " = ตอน regen (กำลังเข้าแบต ÷ กำลังที่มอเตอร์) · เป็นค่าประมาณ:"
                " กำลังจากแบตรวมแอร์และไฟในรถ และค่าแต่ละตัวอ่านห่างกันเสี้ยววินาที"
                " จึงตัดจุดที่ได้ต่ำกว่า 30% หรือเกิน 105% ทิ้ง · แตะช่องเพื่อดูจำนวนจุด"),
            len(samples), html.escape(", ".join(used)), pids.RPM_PER_KMH))
    return page("ไดโนจำลอง + แผนที่ประสิทธิภาพ", body)


def drive_files(folder):
    return sorted(glob.glob(os.path.join(folder, "rec_*.csv")) +
                  glob.glob(os.path.join(folder, "drive_*.csv")))


def write_dyno(paths, out):
    with open(out, "w", encoding="utf-8") as f:
        f.write(dyno_html(paths))
    return out


# ---------------------------------------------------------- charge curve

def charge_series(rows):
    out = {k: [] for k in ("power", "temp", "vmax", "vmin", "delta")}
    top_cells = {}
    for r in rows:
        soc = num(r.get("soc"))
        if soc is None:
            continue
        p = num(r.get("pack_power_kw"))
        if p is None and num(r.get("pack_current")) is not None and \
                num(r.get("pack_voltage")) is not None:
            p = num(r["pack_current"]) * num(r["pack_voltage"]) / 1000
        for key, value in (
                ("power", -p if p is not None else None),
                ("temp", num(r.get("batt_temp_max"))),
                ("vmax", num(r.get("cells_max") or r.get("cell_v_max"))),
                ("vmin", num(r.get("cells_min") or r.get("cell_v_min"))),
                ("delta", num(r.get("cells_delta_mv") or
                              r.get("cell_delta_mv")))):
            if value is not None:
                out[key].append((soc, value))
        no = r.get("cells_max_no")
        if soc >= 90 and no:
            top_cells[no] = top_cells.get(no, 0) + 1
    return out, top_cells


def charge_curve_html(paths):
    colors = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]
    runs = []
    for p in paths:
        try:
            s, top = charge_series(read_csv(p))
        except (OSError, csv.Error):
            continue
        if any(s.values()):
            runs.append((os.path.splitext(os.path.basename(p))[0], s, top))
    if not runs:
        return page("กราฟการชาร์จ", '<section class="card">ยังไม่มีไฟล์บันทึกการชาร์จ'
                    ' บันทึกตอนเสียบชาร์จด้วย <code>obd charge</code></section>')
    runs = runs[-len(colors):]
    lg = legend([(name, colors[n]) for n, (name, _, _) in enumerate(runs)])

    def chart(key, title, unit, note=""):
        series = [(name, colors[n], s[key], "line")
                  for n, (name, s, _) in enumerate(runs)]
        return card(title, lg + plot(series, "SOC %", unit), note)
    cells = []
    for n, (name, s, _) in enumerate(runs):
        cells += [(name + " สูงสุด", colors[n], s["vmax"], "line"),
                  (name + " ต่ำสุด", colors[n], s["vmin"], "line")]
    top = {}
    for _, _, t in runs:
        for k, v in t.items():
            top[k] = top.get(k, 0) + v
    top_rows = "".join("<tr><td>#%s</td><td>%d</td></tr>" % (k, v) for k, v in
                       sorted(top.items(), key=lambda kv: -kv[1])[:6])
    body = '<div class="grid">%s%s%s%s%s</div>' % (
        chart("power", "กำลังชาร์จเข้าแบต", "kW",
              "ชาร์จ AC ควรแบนจนใกล้เต็มแล้วค่อยลด ถ้าตกกลางทางอาจเป็น OBC"
              " ลดกำลังเพราะร้อน"),
        chart("temp", "อุณหภูมิแบตสูงสุด", "°C"),
        card("แรงดันเซลล์สูงสุด / ต่ำสุด", lg + plot(cells, "SOC %", "V"),
             "LFP แรงดันแบนช่วงกลาง แล้วพุ่งขึ้นช่วงท้าย เซลล์ที่พุ่งก่อนคือเซลล์ที่"
             "ความจุน้อยกว่าเพื่อน"),
        chart("delta", "ส่วนต่างแรงดันเซลล์", "mV",
              "ช่วงท้ายการชาร์จส่วนต่างกว้างขึ้นเป็นปกติของ LFP เทียบกันที่ SOC"
              " เดียวกันเท่านั้น"),
        card("เซลล์ที่แรงดันสูงสุดบ่อยที่สุด (SOC ≥ 90%)",
             '<div class="table"><table><tr><th>เซลล์</th><th>จำนวนครั้ง</th></tr>'
             '%s</table></div>' % top_rows, "เซลล์ที่ขึ้นนำซ้ำทุกครั้งที่ชาร์จ"
             " คือ \"ลายนิ้วมือ\" ของแพ็กนี้ ถ้าเปลี่ยนไปจากเดิมควรเฝ้าดู")
        if top_rows else "")
    return page("กราฟการชาร์จ (เทียบ SOC)", body)


def charge_files(folder):
    return sorted(glob.glob(os.path.join(folder, "charge_*.csv")),
                  key=os.path.getmtime)


def write_charge_curve(paths, out):
    with open(out, "w", encoding="utf-8") as f:
        f.write(charge_curve_html(paths))
    return out


def freeze_files(folder):
    return sorted(glob.glob(os.path.join(folder, "freeze_*.json")),
                  key=os.path.getmtime)


def freeze_html(path):
    """The newest freeze-frame file as a page."""
    if not path:
        return page("ภาพนิ่งตอนเกิดโค้ด", '<section class="card">ยังไม่ได้อ่าน'
                    ' ใช้คำสั่ง <code>obd freeze</code> ตอนรถ READY</section>')
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    parts = []
    for item in data.get("frames", []):
        if item.get("error"):
            err = item["error"]
            if "0x31" in err:
                err = "ไม่มีภาพนิ่งเก็บไว้กับโค้ดนี้ (%s)" % err
            elif "0x11" in err or "0x12" in err:
                err = "กล่องนี้ไม่รองรับการอ่านภาพนิ่ง (%s)" % err
            inner = '<p class="muted">%s</p>' % html.escape(err)
        else:
            rows = []
            for rec in item.get("records", []):
                for v in rec["values"]:
                    rows.append("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s"
                                "</td><td>%s</td></tr>" % (
                                    rec["record"], v["did"],
                                    html.escape(v.get("label", "")),
                                    html.escape("" if v.get("value") is None
                                                else "%s %s" % (
                                                    round(v["value"], 3),
                                                    v.get("unit", ""))),
                                    v["raw"]))
            inner = ('<div class="table"><table><tr><th>ชุด</th><th>DID</th>'
                     '<th>ชื่อ</th><th>แปลแล้ว</th><th>ค่าดิบ</th></tr>%s'
                     '</table></div>' % "".join(rows)) if rows else \
                '<p class="muted">ไม่มีภาพนิ่งเก็บไว้กับโค้ดนี้</p>'
            if item.get("unparsed"):
                inner += '<p class="muted">แยกไม่ได้: %s</p>' % item["unparsed"]
        parts.append(card("%s  %s" % (item["header"], item["code"]), inner))
    return page("ภาพนิ่งตอนเกิดโค้ด", "".join(parts) + (
        '<p class="muted">จาก %s เวลา %s</p>' % (
            html.escape(os.path.basename(path)),
            html.escape(data.get("time", "")))))
