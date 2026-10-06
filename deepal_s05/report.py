"""HTML report with charts from CSV files written by charge/live/dashboard.
Several files (for example a 32 A and a 16 A charge) are drawn on the same
charts so they can be compared."""

import csv
import datetime
import html
import math
import os
import re

from . import analysis

# (column, title, unit); the first column found in a file is used
CHARTS = [
    (("soc",), "SOC", "%"),
    (("pack_power_kw",), "กำลังแพ็ก", "kW"),
    (("pack_current",), "กระแสแพ็ก", "A"),
    (("batt_temp_max",), "อุณหภูมิแบตสูงสุด", "°C"),
    (("cells_delta_mv", "cell_delta_mv"), "ส่วนต่างแรงดันเซลล์", "mV"),
    (("aux_12v",), "แบต 12V", "V"),
]

KNOWN = {"time", "elapsed_s", "label", "soc", "pack_voltage",
         "pack_current", "pack_power_kw", "cell_v_max", "cell_v_min",
         "batt_temp_max", "batt_temp_min", "soh", "cell_delta_mv",
         "temp_delta_c", "aux_12v", "cells_delta_mv", "charge_temp",
         "soc_alt", "soh_alt", "energy_available", "efc"}
ARRAY_COLUMN = re.compile(r"^[a-z_]+_(\d+|min|max|min_no|max_no|delta)$")

COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)"]


def num(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def load_run(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    label = next((r["label"] for r in rows if r.get("label")), "") or \
        os.path.splitext(os.path.basename(path))[0]
    t0 = None
    for r in rows:
        try:
            t = datetime.datetime.fromisoformat(r["time"])
        except (KeyError, ValueError):
            t = None
        if t0 is None and t is not None:
            t0 = t
        if r.get("elapsed_s") not in (None, ""):
            r["_min"] = float(r["elapsed_s"]) / 60
        elif t is not None and t0 is not None:
            r["_min"] = (t - t0).total_seconds() / 60
        else:
            r["_min"] = None
    start = rows[0].get("time", "").replace("T", " ") if rows else ""
    return {"path": path, "label": label, "rows": rows, "columns": columns,
            "start": start}


def series(run, column):
    return [(r["_min"], num(r.get(column))) for r in run["rows"]
            if r["_min"] is not None and num(r.get(column)) is not None]


def nice_ticks(lo, hi, count=5):
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.001:
        ticks.append(round(v, 10))
        v += step
    if ticks[-1] < hi:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def tick_label(v):
    return ("%.3f" % v).rstrip("0").rstrip(".") if v % 1 else "%d" % v


def svg_chart(all_series, unit, xlabel="นาที"):
    """all_series: [(label, color, [(x, y)])]"""
    pts = [p for _, _, s in all_series for p in s]
    if not pts:
        return ""
    W, H, L, R, T, B = 720, 240, 56, 14, 12, 34
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xt = nice_ticks(0, max(max(xs), 1))
    yt = nice_ticks(min(ys), max(ys))
    x0, x1, y0, y1 = xt[0], xt[-1], yt[0], yt[-1]

    def X(v):
        return L + (v - x0) / (x1 - x0) * (W - L - R)

    def Y(v):
        return T + (1 - (v - y0) / (y1 - y0)) * (H - T - B)

    out = ['<svg viewBox="0 0 %d %d" role="img">' % (W, H)]
    for v in yt:
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>'
                   % (L, W - R, Y(v), Y(v)))
        out.append('<text x="%d" y="%.1f" text-anchor="end">%s</text>'
                   % (L - 6, Y(v) + 4, tick_label(v)))
    for v in xt:
        out.append('<text x="%.1f" y="%d" text-anchor="middle">%s</text>'
                   % (X(v), H - 14, tick_label(v)))
    out.append('<text x="%d" y="%d" text-anchor="end">%s</text>'
               % (W - R, H - 1, html.escape(xlabel)))
    out.append('<text x="4" y="%d">%s</text>' % (T + 4, html.escape(unit)))
    for label, color, s in all_series:
        if not s:
            continue
        path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(x), Y(y))
                        for i, (x, y) in enumerate(s))
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"/>'
                   % (path, color))
        lx, ly = s[-1]
        out.append('<circle cx="%.1f" cy="%.1f" r="3.5" fill="%s"/>'
                   % (X(lx), Y(ly), color))
    out.append("</svg>")
    return "".join(out)


def custom_columns(runs):
    cols = []
    for run in runs:
        for c in run["columns"]:
            if c in KNOWN or c in cols or ARRAY_COLUMN.match(c):
                continue
            if any(num(r.get(c)) is not None for r in run["rows"]):
                cols.append(c)
    return cols


def summary_rows(runs, capacity):
    rows = []
    for run in runs:
        soc = series(run, "soc")
        temp = series(run, "batt_temp_max")
        delta = series(run, "cells_delta_mv") or series(run, "cell_delta_mv")
        power = series(run, "pack_power_kw")
        minutes = max((r["_min"] or 0) for r in run["rows"]) \
            if run["rows"] else 0
        socs = {r["_min"]: num(r.get("soc")) for r in run["rows"]}
        drop = analysis.power_drop([(socs.get(t), p) for t, p in power])
        rows.append([
            run["label"], run["start"], "%.0f" % minutes,
            "%s → %s" % (tick_label(soc[0][1]), tick_label(soc[-1][1]))
            if soc else "-",
            "%.1f" % ((soc[-1][1] - soc[0][1]) / 100 * capacity)
            if soc else "-",
            "%.1f" % (sum(abs(p) for _, p in power) / len(power))
            if power else "-",
            "%.0f" % max(t for _, t in temp) if temp else "-",
            "%.1f → %.1f" % (delta[0][1], delta[-1][1]) if delta else "-",
            "⚠️ ลดลง %.0f%%" % (drop * 100) if drop >= 0.2 else "-",
        ])
    return rows


PAGE = """<!doctype html>
<html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deepal S05 Charge Report</title>
<style>
:root { --bg:#f2f5f4; --surface:#fff; --ink:#16211f; --muted:#5c6b68;
  --line:#d5dddb; --s1:#0d7a70; --s2:#c2601b; --s3:#2c68b0; --s4:#8a4fb5;
  --s5:#a8892a; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1514; --surface:#17201e;
  --ink:#e2eae8; --muted:#94a4a0; --line:#2b3835; --s1:#3fc0b2;
  --s2:#f0954f; --s3:#6ea5ec; --s4:#c193e6; --s5:#d9bd5c;
  color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans Thai","Noto Sans Thai",Tahoma,sans-serif;
  padding:20px 16px 40px; line-height:1.5; }
main { max-width:1100px; margin:0 auto; display:grid; gap:16px; }
h1 { margin:0; font-size:1.4rem; } h2 { margin:0 0 6px; font-size:1rem; }
.muted { color:var(--muted); font-size:.88rem; }
.card { background:var(--surface); border:1px solid var(--line);
  border-radius:12px; padding:14px; min-width:0; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%%,460px),1fr));
  gap:16px; }
svg { width:100%%; height:auto; display:block; }
svg text { fill:var(--muted); font:11px ui-monospace,Menlo,Consolas,monospace; }
svg .grid { stroke:var(--line); stroke-width:1; }
.legend { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:.88rem; }
.legend span::before { content:""; display:inline-block; width:12px;
  height:3px; margin-right:6px; vertical-align:middle; background:var(--c); }
.table { overflow-x:auto; }
table { border-collapse:collapse; width:100%%; font-size:.88rem;
  font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);
  white-space:nowrap; }
th { color:var(--muted); font-weight:500; }
</style></head><body><main>
<header><h1>รายงานการชาร์จ Deepal S05</h1>
<div class="muted">สร้างเมื่อ %(now)s · ความจุแบตที่ใช้คำนวณ %(cap)s kWh</div></header>
<div class="legend">%(legend)s</div>
<section class="card"><h2>สรุปแต่ละรอบ</h2><div class="table"><table>
<tr><th>รอบ</th><th>เริ่ม</th><th>นาที</th><th>SOC</th><th>พลังงานเข้า (ประมาณ จาก SOC)</th>
<th>กำลังเฉลี่ย kW</th><th>แบตร้อนสุด °C</th><th>ส่วนต่างเซลล์ mV (เริ่ม→จบ)</th>
<th>กำลังลดลงกลางทาง (SOC &lt; 95%%)</th></tr>
%(summary)s</table></div></section>
<div class="grid">%(charts)s</div>
<p class="muted">กำลังและกระแสคำนวณจากค่าที่อ่านจาก BMS ทิศทาง +/- ของกระแสยังไม่ยืนยัน
พลังงานเข้าประมาณจาก SOC × ความจุแบต "กำลังลดลงกลางทาง" คือกำลังที่ตกจากจุดสูงสุดเกิน 20%%
ก่อนแบตใกล้เต็ม ถ้าเป็นการชาร์จ AC อาจเป็นสัญญาณว่า OBC ลดกำลังเพราะร้อน</p>
</main></body></html>
"""


def write_report(paths, out, capacity=56.1):
    runs = [load_run(p) for p in paths]
    colors = {run["label"]: COLORS[i % len(COLORS)]
              for i, run in enumerate(runs)}
    charts = []
    specs = list(CHARTS) + [((c,), c, "") for c in custom_columns(runs)]
    for columns, title, unit in specs:
        lines = []
        for run in runs:
            col = next((c for c in columns if series(run, c)), None)
            lines.append((run["label"], colors[run["label"]],
                          series(run, col) if col else []))
        svg = svg_chart(lines, unit)
        if svg:
            charts.append('<section class="card"><h2>%s</h2>%s</section>'
                          % (html.escape(title), svg))
    legend = "".join('<span style="--c:%s">%s</span>' % (
        colors[r["label"]], html.escape(r["label"])) for r in runs)
    summary = "".join("<tr>%s</tr>" % "".join(
        "<td>%s</td>" % html.escape(str(c)) for c in row)
        for row in summary_rows(runs, capacity))
    page = PAGE % {
        "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cap": capacity, "legend": legend, "summary": summary,
        "charts": "".join(charts)}
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out


TRENDS = [
    ("soh", "SOH", "%"),
    ("delta_mv", "ส่วนต่างแรงดันเซลล์ (แยกตามช่วง SOC)", "mV"),
    ("aux_12v", "แบต 12V ตอนตรวจ", "V"),
    ("batt_temp_max", "อุณหภูมิแบตสูงสุด", "°C"),
    ("temp_delta_c", "อุณหภูมิในแพ็กต่างกัน", "°C"),
    ("dtc_active", "จำนวนโค้ดปัญหาที่ใช้งานอยู่", "โค้ด"),
    ("ir_mohm", "IR แพ็ก (จาก health --ir)", "mΩ"),
]
ZONE_LABEL = {"top": "SOC ≥ 95%", "low": "SOC ≤ 25%", "mid": "SOC กลาง",
              "unknown": "ไม่ทราบ SOC"}


def _read_rows(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    for r in rows:
        try:
            r["_t"] = datetime.datetime.fromisoformat(r["time"])
        except (KeyError, ValueError):
            r["_t"] = None
    return [r for r in rows if r["_t"] is not None]


def write_trends(checkup_path, health_path, out):
    """Charts over days from checkup_history.csv and health_history.csv."""
    checkups = _read_rows(checkup_path)
    health = _read_rows(health_path)
    all_rows = checkups + health
    if not all_rows:
        raise SystemExit("ยังไม่มีประวัติใน %s หรือ %s ให้ทำกราฟ"
                         % (checkup_path, health_path))
    t0 = min(r["_t"] for r in all_rows)

    def pts(rows, key, zone=None):
        return [((r["_t"] - t0).total_seconds() / 86400, num(r.get(key)))
                for r in rows if num(r.get(key)) is not None and
                (zone is None or r.get("zone") == zone)]

    charts = []
    for key, title, unit in TRENDS:
        lines = []
        if key == "delta_mv":
            for i, zone in enumerate(("top", "low", "mid")):
                s = pts(checkups, key, zone) + pts(health, key, zone)
                lines.append((ZONE_LABEL[zone], COLORS[i], sorted(s)))
        else:
            lines.append(("checkup", COLORS[0], pts(checkups, key)))
            lines.append(("health", COLORS[1], pts(health, key)))
        lines = [ln for ln in lines if ln[2]]
        svg = svg_chart(lines, unit, "วัน")
        if svg:
            legend = "".join('<span style="--c:%s">%s</span>' % (c, html.escape(n))
                             for n, c, _ in lines)
            charts.append('<section class="card"><h2>%s</h2>'
                          '<div class="legend">%s</div>%s</section>'
                          % (html.escape(title), legend, svg))
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
        "<td>%s</td><td>%s</td></tr>" % tuple(html.escape(str(x)) for x in (
            r["time"].replace("T", " "), r.get("level", ""),
            ZONE_LABEL.get(r.get("zone"), r.get("zone", "")),
            r.get("soh", ""), r.get("delta_mv", ""), r.get("aux_12v", ""),
            r.get("dtc_active", "")))
        for r in reversed(checkups[-10:]))
    page = PAGE % {
        "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cap": "-", "legend": "",
        "summary": "",
        "charts": "".join(charts)}
    page = page.replace("<title>Deepal S05 Charge Report</title>",
                        "<title>Deepal S05 Trends</title>")
    page = page.replace("<h1>รายงานการชาร์จ Deepal S05</h1>",
                        "<h1>แนวโน้มสุขภาพรถ Deepal S05</h1>")
    page = page.replace(" · ความจุแบตที่ใช้คำนวณ - kWh", "")
    start = page.index('<section class="card"><h2>สรุปแต่ละรอบ</h2>')
    end = page.index("</section>", start) + len("</section>")
    table = ('<section class="card"><h2>ผลตรวจล่าสุด (checkup)</h2>'
             '<div class="table"><table><tr><th>เวลา</th><th>ผลรวม</th>'
             '<th>ช่วง SOC</th><th>SOH %%</th><th>ส่วนต่างเซลล์ mV</th>'
             '<th>แบต 12V</th><th>โค้ด</th></tr>%s</table></div></section>'
             % rows) if checkups else ""
    page = page[:start] + page[end:]
    tail_start = page.index('<p class="muted">กำลังและกระแส')
    tail_end = page.index("</p>", tail_start) + 4
    page = page[:tail_start] + table.replace("%%", "%") + (
        '<p class="muted">ดูการเปลี่ยนแปลงเทียบกับตัวเองในอดีต'
        ' ส่วนต่างเซลล์ให้เทียบเฉพาะช่วง SOC เดียวกัน</p>') + page[tail_end:]
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out
