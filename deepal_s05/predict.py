"""Early warnings: compare the car with its own history and project slow
trends forward, so a part that is drifting shows up before it crosses a
hard limit (roadmap item H).

Everything is read from files the program already writes:

  aux12v_*.csv          12 V while the car sleeps (autopilot)
  drive_*.csv           every round while the car is awake (autopilot):
                        driving and charging sessions, cells, motor
  checkup_history.csv   weekly checkups
  health_history.csv    pack internal resistance (health --ir)
  dtc_history.json      trouble-code scans

Nothing here talks to the car.
"""

import csv
import datetime
import glob
import html
import json
import os
import re
import statistics
from dataclasses import dataclass, field

from .analysis import linear_fit

OK, WATCH, WARN, NODATA = "ok", "watch", "warn", "nodata"
LEVEL_TEXT = {OK: "ปกติ", WATCH: "เฝ้าดู", WARN: "ควรให้ช่างตรวจ",
              NODATA: "ข้อมูลยังไม่พอ"}
LEVEL_ICON = {OK: "🟢", WATCH: "🟡", WARN: "🔴", NODATA: "⚪"}
RANK = {NODATA: 0, OK: 1, WATCH: 2, WARN: 3}

# a forecast needs this much history before it is shown
MIN_POINTS, MIN_SPAN_DAYS = 5, 21
SESSION_GAP_S = 600
CELL_KEY = re.compile(r"^cells_(\d+)$")


@dataclass
class Finding:
    key: str
    part: str
    level: str
    summary: str
    detail: str = ""
    days_left: float = None
    points: list = field(default_factory=list)  # [(datetime, value)]
    unit: str = ""
    limit: float = None


# ---------------------------------------------------------------- helpers

def num(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def parse_time(text):
    try:
        return datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def read_rows(paths):
    rows = []
    for p in paths:
        try:
            with open(p, newline="", encoding="utf-8") as f:
                rows.extend(csv.DictReader(f))
        except (OSError, csv.Error):
            continue
    return rows


SLIM_KEYS = ("time", "soc", "pack_voltage", "pack_current", "batt_temp_max",
             "motor_rpm", "motor_torque", "motor_temp", "inverter_temp")


def read_drive(folder, patterns=("drive_*.csv",), days=90, cell_every=10):
    """Recordings, kept small enough for a Pi Zero (512 MB): only the
    files of the last `days`, only the columns the checks use, and the
    120 cell voltages only on every `cell_every`-th row and near full
    charge (SOC >= 95, for the fingerprint)."""
    from .datafiles import recorded_time
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    paths = []
    for pattern in patterns:
        for p in files(folder, pattern):
            if recorded_time(os.path.basename(p),
                             os.path.getmtime(p)) >= cutoff:
                paths.append(p)
    rows = []
    for p in sorted(paths):
        try:
            with open(p, newline="", encoding="utf-8") as f:
                for n, r in enumerate(csv.DictReader(f)):
                    slim = {k: r.get(k) for k in SLIM_KEYS if r.get(k)}
                    soc = num(r.get("soc"))
                    if n % cell_every == 0 or (soc is not None and soc >= 95):
                        slim.update((k, v) for k, v in r.items()
                                    if v and CELL_KEY.match(k))
                    rows.append(slim)
        except (OSError, csv.Error):
            continue
    rows.sort(key=lambda r: r.get("time") or "")
    return rows


def files(folder, pattern):
    return sorted(glob.glob(os.path.join(folder, pattern)))


def forecast(points, limit, rising):
    """Days until a straight-line trend through `points` reaches `limit`,
    or None when the trend is flat, moving away, or too uncertain.
    -> (days, slope per day, r2)"""
    if len(points) < MIN_POINTS:
        return None, None, None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    if xs[-1] - xs[0] < MIN_SPAN_DAYS:
        return None, None, None
    fit = linear_fit(xs, [v for _, v in points])
    if fit is None:
        return None, None, None
    a, b, r2 = fit
    if (b <= 0 if rising else b >= 0) or r2 < 0.3:
        return None, b, r2
    days = (limit - (a + b * xs[-1])) / b
    return (max(days, 0.0), b, r2)


def when_text(days):
    if days < 45:
        return "ราว %d วัน" % max(round(days), 1)
    if days < 730:
        return "ราว %d เดือน" % round(days / 30.4)
    return "มากกว่า 2 ปี"


def level_for_days(days):
    if days is None:
        return OK
    return WARN if days < 90 else WATCH if days < 365 else OK


def trend_finding(key, part, points, limit, rising, unit, fmt="%.2f",
                  advice=""):
    """A value with a fixed limit: past it = WARN, a trend reaching it
    within a year = WATCH (within 3 months = WARN)."""
    if not points:
        return Finding(key, part, NODATA, "ยังไม่มีข้อมูล", unit=unit,
                       limit=limit)
    last = points[-1][1]
    over = last >= limit if rising else last <= limit
    days, slope, _ = forecast(points, limit, rising)
    shown = (fmt + " %s") % (last, unit)
    if over:
        return Finding(key, part, WARN, "ล่าสุด %s เลยเกณฑ์ %s แล้ว" % (
            shown, (fmt % limit)), advice, 0, points, unit, limit)
    if days is None and (len(points) < MIN_POINTS or
                         (points[-1][0] - points[0][0]).days < MIN_SPAN_DAYS):
        return Finding(key, part, OK, "ล่าสุด %s · ยังทำนายไม่ได้ (มี %d ครั้ง"
                       " ต้องมี %d ครั้งในเวลา %d วันขึ้นไป)" % (
                           shown, len(points), MIN_POINTS, MIN_SPAN_DAYS),
                       advice, None, points, unit, limit)
    level = level_for_days(days)
    if days is None or level == OK:
        text = "ล่าสุด %s · แนวโน้มไม่น่าห่วง" % shown
    else:
        text = "ล่าสุด %s · ถ้าแนวโน้มเป็นแบบนี้ต่อไป จะถึงเกณฑ์ %s ใน%s" % (
            shown, fmt % limit, when_text(days))
    return Finding(key, part, level, text, advice, days, points, unit, limit)


# --------------------------------------------------------------- sessions

def sessions(rows):
    """Split autopilot drive_*.csv rows into sessions at gaps in time,
    each labelled "drive", "charge" or "idle"."""
    out, cur, prev = [], None, None
    for r in rows:
        t = parse_time(r.get("time"))
        if t is None:
            continue
        if prev is None or (t - prev).total_seconds() > SESSION_GAP_S:
            cur = []
            out.append(cur)
        r["_t"] = t  # in place: a copy per row is too much for a Pi Zero
        cur.append(r)
        prev = t
    result = []
    for s in out:
        if len(s) < 5:
            continue
        rpm = [abs(num(r.get("motor_rpm")) or 0) for r in s]
        cur_a = [num(r.get("pack_current")) for r in s]
        cur_a = [c for c in cur_a if c is not None]
        kind = "idle"
        if sum(1 for x in rpm if x > 500) >= 3:
            kind = "drive"
        elif cur_a and statistics.median(cur_a) < -2:
            kind = "charge"
        result.append((kind, s))
    return result


def energy_kwh(rows, sign=None):
    """Battery energy through the pack, kWh. sign=+1 counts discharge
    only, -1 charge only (as a positive number), None both as |E|."""
    total = 0.0
    for a, b in zip(rows, rows[1:]):
        dt = min((b["_t"] - a["_t"]).total_seconds(), 60)
        v, i = num(a.get("pack_voltage")), num(a.get("pack_current"))
        if v is None or i is None:
            continue
        p = v * i / 1000
        if sign is None:
            total += abs(p) * dt / 3600
        elif p * sign > 0:
            total += abs(p) * dt / 3600
    return total


def temp_rise(rows, key):
    temps = [num(r.get(key)) for r in rows]
    temps = [t for t in temps if t is not None]
    if len(temps) < 3:
        return None
    return max(temps) - temps[0]


def compare_to_own(key, part, values, unit, worse_up, watch, warn,
                   fmt="%.1f", advice="", what=""):
    """Latest session against the median of the earlier ones.
    `watch`/`warn` are relative changes (0.5 = 50 % worse)."""
    if len(values) < 4:
        return Finding(key, part, NODATA,
                       "มีข้อมูล %d ครั้ง ต้องมีอย่างน้อย 4 ครั้งถึงจะรู้ว่า"
                       "ปกติของรถคันนี้เป็นเท่าไร" % len(values), advice,
                       points=values, unit=unit)
    usual = statistics.median(v for _, v in values[:-1])
    last = values[-1][1]
    if not usual:  # no usual value to compare a change against
        return Finding(key, part, OK, "%sล่าสุด %s %s" % (
            what, fmt % last, unit), advice, points=values, unit=unit)
    change = (last - usual) / abs(usual) * (1 if worse_up else -1)
    level = WARN if change >= warn else WATCH if change >= watch else OK
    text = "%sล่าสุด %s %s · ปกติของรถคันนี้ %s %s" % (
        what, fmt % last, unit, fmt % usual, unit)
    if level != OK:
        text += " (%s %.0f%%)" % ("แย่ลง", change * 100)
    return Finding(key, part, level, text, advice, points=values, unit=unit)


# ----------------------------------------------------------------- checks

def check_12v_rest(folder):
    """Median 12 V per night while the car sleeps."""
    by_day = {}
    for r in read_rows(files(folder, "aux12v_*.csv")):
        t, v = parse_time(r.get("time")), num(r.get("aux_12v"))
        # small hours only: hours after the last drive or charge, so the
        # battery has settled and nights compare with each other
        if t is None or v is None or t.hour >= 6:
            continue
        by_day.setdefault(t.date(), []).append(v)
    points = [(datetime.datetime.combine(d, datetime.time(3)),
               statistics.median(v)) for d, v in sorted(by_day.items())
              if len(v) >= 3]
    return trend_finding(
        "aux12v_rest", "แบต 12V (ตอนจอดกลางคืน)", points, 12.2, False, "V",
        advice="แบต 12V คือสาเหตุอันดับหนึ่งที่ทำให้ EV สตาร์ทไม่ติด ถ้าแรงดันตอนจอด"
               "ลดลงเรื่อยๆ ให้เตรียมเปลี่ยนก่อนหมดกลางทาง (12.2 V ≈ แบตเหลือครึ่งเดียว"
               " สำหรับแบตตะกั่ว)")


def checkup_points(folder, key, zone=None):
    rows = read_rows([os.path.join(folder, "checkup_history.csv")])
    out = []
    for r in rows:
        t, v = parse_time(r.get("time")), num(r.get(key))
        if t is None or v is None or (zone and r.get("zone") != zone):
            continue
        out.append((t, v))
    return out


def check_12v_ready(folder):
    return trend_finding(
        "aux12v_ready", "ระบบชาร์จแบต 12V (DC-DC)",
        checkup_points(folder, "aux_12v"), 13.0, False, "V",
        advice="ตอนรถ READY ตัวแปลงไฟ DC-DC ต้องชาร์จแบต 12V ที่ 13.2 V ขึ้นไป"
               " ถ้าค่อยๆ ต่ำลงอาจเป็นที่ DC-DC หรือแบต 12V")


def check_pack_temp_spread(folder):
    return trend_finding(
        "temp_spread", "ระบบหล่อเย็นแบต (ความร้อนในแพ็ก)",
        checkup_points(folder, "temp_delta_c"), 8, True, "°C", "%.1f",
        advice="อุณหภูมิจุดต่างๆ ในแพ็กควรใกล้กัน ถ้าต่างกันมากขึ้นเรื่อยๆ อาจเป็นปั๊ม"
               "น้ำหล่อเย็น น้ำยาพร่อง หรือท่ออุดตัน")


def check_insulation(folder):
    pts = checkup_points(folder, "insulation_ohm_per_v")
    f = trend_finding(
        "insulation", "ฉนวนไฟแรงสูง", pts, 500, False, "Ω/V", "%.0f",
        advice="ค่าฉนวนลดลง = เริ่มมีไฟรั่วจากระบบไฟแรงสูง อันตรายที่สุดในรายการนี้"
               " ถ้าเหลืองหรือแดงให้ศูนย์ตรวจ")
    if not pts:
        f.summary = "ยังอ่านค่าไม่ได้ (ยังหา DID ค่าฉนวนของ Deepal ไม่เจอ)"
    return f


def check_ir(folder):
    pts = []
    for r in read_rows([os.path.join(folder, "health_history.csv")]):
        t, v = parse_time(r.get("time")), num(r.get("ir_mohm"))
        if t and v:
            pts.append((t, v))
    return compare_to_own(
        "pack_ir", "ความต้านทานภายในแบต (IR)", pts, "mΩ", True, 0.3, 0.6,
        advice="IR สูงขึ้น = แบตเสื่อม จ่ายไฟได้น้อยลงและร้อนขึ้น ขึ้นกับอุณหภูมิมาก"
               " เทียบกันเฉพาะวันที่อากาศใกล้กัน (วัดด้วย obd ir)", what="IR ")


def check_cell_spread(folder):
    """Cell spread at the same SOC zone, against this car's own past."""
    worst = None
    for zone, name in (("mid", "SOC กลาง"), ("top", "SOC ≥ 95%"),
                       ("low", "SOC ≤ 25%")):
        pts = checkup_points(folder, "delta_mv", zone)
        f = compare_to_own("cell_spread", "ความสมดุลของเซลล์", pts, "mV",
                           True, 0.5, 1.0, "%.0f", what="ส่วนต่างเซลล์ (%s) " % name,
                           advice="เทียบเฉพาะที่ SOC ช่วงเดียวกัน เพราะ LFP ที่เกือบเต็ม"
                                  "ส่วนต่างกว้างได้ตามปกติ ถ้ากว้างขึ้นเรื่อยๆ ลองชาร์จ AC"
                                  " ให้เต็มแล้วเสียบทิ้งไว้ให้บาลานซ์ (obd balance)")
        if worst is None or RANK[f.level] > RANK[worst.level] or (
                worst.level == NODATA and pts):
            worst = f
    return worst


def cell_fingerprint(drive_sessions):
    """Per session: each cell's deviation from the pack mean (mV) at the
    highest SOC of the session, when SOC >= 95. -> [(time, {cell: mV})]"""
    out = []
    for _, s in drive_sessions:
        best = None
        for r in s:
            soc = num(r.get("soc"))
            if soc is not None and soc >= 95 and (
                    best is None or soc > num(best.get("soc"))):
                best = r
        if best is None:
            continue
        cells = {}
        for k, v in best.items():
            m = CELL_KEY.match(k)
            if m and num(v) is not None:
                cells[int(m.group(1))] = num(v)
        if len(cells) < 8:
            continue
        mean = statistics.mean(cells.values())
        out.append((best["_t"], {c: (v - mean) * 1000
                                 for c, v in cells.items()}))
    return out


def check_weak_cell(drive_sessions):
    prints = cell_fingerprint(drive_sessions)
    part = "เซลล์ที่อ่อนกว่าเพื่อน (ลายนิ้วมือแพ็ก)"
    advice = ("ตอนชาร์จเกือบเต็ม เซลล์ที่ความจุน้อยกว่าเพื่อนจะแรงดันขึ้นก่อน ถ้าเป็น"
              "เซลล์เดิมทุกครั้งและห่างจากค่าเฉลี่ยมากขึ้นเรื่อยๆ = เซลล์/โมดูลนั้นเริ่มอ่อน")
    if not prints:
        return Finding("weak_cell", part, NODATA, "ยังไม่มีข้อมูลตอนชาร์จเกือบเต็ม"
                       " (ต้องให้ autopilot บันทึกตอนชาร์จถึง 95% ขึ้นไป)", advice)
    leaders = {}
    for _, dev in prints:
        c = max(dev, key=dev.get)
        leaders[c] = leaders.get(c, 0) + 1
    cell = max(leaders, key=leaders.get)
    pts = [(t, dev[cell]) for t, dev in prints if cell in dev]
    f = compare_to_own("weak_cell", part, pts, "mV", True, 0.3, 0.6, "%.0f",
                       advice, what="เซลล์ #%d (นำ %d จาก %d ครั้ง) ห่างค่าเฉลี่ย "
                       % (cell, leaders[cell], len(prints)))
    if f.level == NODATA:
        f.summary = "เซลล์ที่ขึ้นนำบ่อยสุด #%d (%d จาก %d ครั้ง) · %s" % (
            cell, leaders[cell], len(prints), f.summary)
    return f


def _corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else 0.0


def cell_deviations(rows):
    """Per row with cell voltages: {cell: mV from the pack mean}."""
    out = []
    for r in rows:
        cells = {}
        for k, v in r.items():
            m = CELL_KEY.match(k)
            if m and num(v) is not None:
                cells[int(m.group(1))] = num(v)
        if len(cells) >= 8:
            mean = statistics.mean(cells.values())
            out.append({c: (v - mean) * 1000 for c, v in cells.items()})
    return out


def module_groups(devs, min_rows=30):
    """Split the pack into modules from behaviour: neighbouring cells in
    one module move together (high correlation of their deviations);
    across a module joint the correlation drops. Returns [(first, last)]
    cell numbers, or None when no such structure shows."""
    if len(devs) < min_rows:
        return None
    cells = sorted(set.intersection(*(set(d) for d in devs)))
    if len(cells) < 8:
        return None
    series = {c: [d[c] for d in devs] for c in cells}
    corr = [_corr(series[a], series[b]) for a, b in zip(cells, cells[1:])]
    med = statistics.median(corr)
    mad = statistics.median(abs(c - med) for c in corr)
    if mad == 0:
        return None
    cuts = [k for k, c in enumerate(corr) if c < med - 4 * mad]
    if not cuts or len(cuts) > len(cells) // 3:
        return None
    groups, start = [], 0
    for k in cuts:
        groups.append((cells[start], cells[k]))
        start = k + 1
    groups.append((cells[start], cells[-1]))
    return groups


def check_modules(drive_sessions):
    part = "โมดูลแบต (กลุ่มเซลล์)"
    advice = ("แบ่งโมดูลจากพฤติกรรม: เซลล์ในโมดูลเดียวกันแรงดันขึ้นลงพร้อมกัน"
              " แล้วดูว่าโมดูลไหนขึ้นนำตอนชาร์จเกือบเต็ม (ความจุน้อยกว่าเพื่อน)"
              " และนำห่างขึ้นเรื่อยๆ ไหม")
    rows = [r for _, s in drive_sessions for r in s]
    groups = module_groups(cell_deviations(rows))
    if not groups:
        return Finding("modules", part, NODATA,
                       "ยังแยกโมดูลไม่ได้ (ต้องมีข้อมูลรายเซลล์ตอนขับหลายร้อยจุด)",
                       advice)
    prints = cell_fingerprint(drive_sessions)

    def module_dev(dev, g):
        vals = [dev[c] for c in range(g[0], g[1] + 1) if c in dev]
        return statistics.mean(vals) if vals else 0.0
    text = "แยกได้ %d โมดูล" % len(groups)
    if not prints:
        return Finding("modules", part, OK, text + " · ยังไม่มีข้อมูลตอนชาร์จ"
                       "เกือบเต็มไว้เทียบ", advice)
    worst = max(groups, key=lambda g: module_dev(prints[-1][1], g))
    pts = [(t, module_dev(dev, worst)) for t, dev in prints]
    f = compare_to_own("modules", part, pts, "mV", True, 0.3, 0.6, "%.0f",
                       advice, what="%s · โมดูลเซลล์ %d–%d นำค่าเฉลี่ย " % (
                           text, worst[0], worst[1]))
    if f.level == NODATA:
        f.summary = "%s · โมดูลที่นำตอนเต็ม: เซลล์ %d–%d (+%.0f mV)" % (
            text, worst[0], worst[1], pts[-1][1])
    return f


def check_battery_heating(drive_sessions):
    vals = []
    for kind, s in drive_sessions:
        if kind not in ("drive", "charge"):
            continue
        e = energy_kwh(s)
        rise = temp_rise(s, "batt_temp_max")
        if e >= 3 and rise is not None:
            vals.append((s[-1]["_t"], rise / e))
    return compare_to_own(
        "batt_heating", "ระบบหล่อเย็นแบต (ร้อนเร็วแค่ไหน)", vals, "°C/kWh", True,
        0.5, 1.0, "%.2f", what="แบตร้อนขึ้นต่อพลังงานที่ใช้ ",
        advice="ขับหรือชาร์จเท่าเดิมแต่แบตร้อนเร็วขึ้น อาจเป็นระบบหล่อเย็น"
               " (ขึ้นกับอุณหภูมิอากาศด้วย ให้ดูแนวโน้มหลายครั้ง)")


def check_motor_heating(drive_sessions):
    vals = []
    for kind, s in drive_sessions:
        if kind != "drive":
            continue
        e = energy_kwh(s, sign=1)
        rise = temp_rise(s, "motor_temp")
        if e >= 2 and rise is not None:
            vals.append((s[-1]["_t"], rise / e))
    return compare_to_own(
        "motor_heating", "มอเตอร์ (ร้อนเร็วแค่ไหน)", vals, "°C/kWh", True, 0.5,
        1.0, "%.2f", what="มอเตอร์ร้อนขึ้นต่อพลังงานที่ใช้ ",
        advice="มอเตอร์ร้อนเร็วขึ้นที่ภาระเท่าเดิม อาจเป็นน้ำหล่อเย็นมอเตอร์หรือ"
               "ตลับลูกปืน (สูตรอุณหภูมิยังรอยืนยัน)")


def check_drive_efficiency(drive_sessions):
    import math
    vals = []
    for kind, s in drive_sessions:
        if kind != "drive":
            continue
        mech = elec = 0.0
        for r in s:
            rpm, tq = num(r.get("motor_rpm")), num(r.get("motor_torque"))
            v, i = num(r.get("pack_voltage")), num(r.get("pack_current"))
            if None in (rpm, tq, v, i):
                continue
            pm = tq * abs(rpm) * 2 * math.pi / 60 / 1000
            pe = v * i / 1000
            if pm > 3 and pe > 3 and 0.3 <= pm / pe <= 1.05:
                mech, elec = mech + pm, elec + pe
        if elec > 30:  # enough driving samples
            vals.append((s[-1]["_t"], mech / elec * 100))
    return compare_to_own(
        "drive_eff", "มอเตอร์ + อินเวอร์เตอร์ (ประสิทธิภาพ)", vals, "%", False,
        0.05, 0.10, "%.0f", what="ประสิทธิภาพเฉลี่ยตอนขับ ",
        advice="ประสิทธิภาพตกลงที่การขับแบบเดิม อาจเป็นอินเวอร์เตอร์/มอเตอร์ หรือ"
               "แค่เปิดแอร์หนักขึ้น (กำลังจากแบตรวมแอร์) ให้ดูแนวโน้มหลายครั้ง")


def check_codes(folder):
    """Codes that keep coming back, pending codes, and lost
    communication (U-codes) at the same ECU."""
    hist = []
    try:
        with open(os.path.join(folder, "dtc_history.json"),
                  encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, ValueError):
        pass
    part = "โค้ดปัญหาที่กลับมาซ้ำ / ขั้วต่อ"
    advice = ("โค้ดที่โผล่ซ้ำหลายครั้งหรือค้างสถานะ \"รอยืนยัน\" คืออาการเริ่มต้นก่อน"
              "ไฟเตือนขึ้น U-code ที่กล่องเดิมซ้ำๆ มักเป็นขั้วต่อหลวม/สายสื่อสาร")
    if not hist:
        return Finding("codes", part, NODATA, "ยังไม่มีประวัติการอ่านโค้ด (obd dtc หรือ"
                       " checkup)", advice)
    recent = hist[-10:]
    seen = {}
    for scan in recent:
        for c in scan.get("codes", []):
            seen.setdefault((c["header"], c["code"]), []).append(c["status"])
    repeat = sorted(k for k, v in seen.items() if len(v) >= 3)
    pending = sorted((c["header"], c["code"]) for c in recent[-1].get(
        "codes", []) if c["status"] & 0x04)
    u_ecus = {}
    for (h, code), v in seen.items():
        if code.startswith("U") and len(v) >= 2:
            u_ecus[h] = u_ecus.get(h, 0) + 1
    notes = []
    if pending:
        notes.append("รอยืนยัน: " + ", ".join("%s %s" % k for k in pending))
    if repeat:
        notes.append("กลับมาซ้ำ ≥3 ครั้ง: " + ", ".join(
            "%s %s" % k for k in repeat[:6]))
    if u_ecus:
        notes.append("ขาดการสื่อสารซ้ำที่กล่อง: " + ", ".join(sorted(u_ecus)))
    level = WATCH if (pending or repeat or u_ecus) else OK
    return Finding("codes", part, level, " · ".join(notes) or
                   "ไม่มีโค้ดที่กลับมาซ้ำ (จาก %d ครั้งล่าสุด)" % len(recent),
                   advice)


def assess(folder, capacity_kwh=56.1):
    """All checks, worst first."""
    from . import soh
    sess = sessions(read_drive(folder))
    found = [
        soh.check_soh(folder, capacity_kwh),
        check_12v_rest(folder), check_12v_ready(folder),
        check_weak_cell(sess), check_modules(sess),
        check_cell_spread(folder), check_ir(folder),
        check_battery_heating(sess), check_pack_temp_spread(folder),
        check_motor_heating(sess), check_drive_efficiency(sess),
        check_codes(folder), check_insulation(folder),
        Finding("obc", "ตัวชาร์จ AC (OBC)", NODATA,
                "ยังอ่านค่าไม่ได้ (ยังหากล่อง OBC ไม่เจอ)",
                "เมื่อเจอ OBC จะเฝ้ากำลังชาร์จที่ตก และอุณหภูมิ OBC เทียบแบต"),
    ]
    found.sort(key=lambda f: -RANK[f.level])
    return found


# ------------------------------------------------------------ notification

def notify_changes(findings, state_path, send):
    """Send findings that became WATCH/WARN or got worse since the last
    run (remembered in `state_path`, so restarts do not resend)."""
    try:
        with open(state_path, encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    due = [f for f in findings if f.level in (WATCH, WARN) and
           RANK[f.level] > RANK.get(last.get(f.key), 0)]
    if due:
        send("เตือนล่วงหน้า", "\n".join(
            "%s %s: %s" % (LEVEL_ICON[f.level], f.part, f.summary)
            for f in due), "high" if any(f.level == WARN for f in due)
            else "default")
    state = {f.key: f.level for f in findings}
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return due


# -------------------------------------------------------------------- page

def sparkline(points, limit=None, W=220, H=44):
    if len(points) < 2:
        return ""
    ys = [v for _, v in points] + ([limit] if limit is not None else [])
    lo, hi = min(ys), max(ys)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    t0, t1 = points[0][0], points[-1][0]
    span = (t1 - t0).total_seconds() or 1

    def X(t):
        return 2 + (t - t0).total_seconds() / span * (W - 4)

    def Y(v):
        return 4 + (1 - (v - lo) / (hi - lo)) * (H - 8)
    d = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(t), Y(v))
                 for i, (t, v) in enumerate(points))
    lim = ('<line x1="0" x2="%d" y1="%.1f" y2="%.1f" stroke="var(--bad)" '
           'stroke-dasharray="4 3"/>' % (W, Y(limit), Y(limit))
           if limit is not None else "")
    return ('<svg class="spark" viewBox="0 0 %d %d">%s<path d="%s" fill="none"'
            ' stroke="var(--s1)" stroke-width="2"/></svg>' % (W, H, lim, d))


PAGE_CSS = """
.part { background:var(--surface); border:1px solid var(--line);
  border-left:6px solid var(--c); border-radius:12px; padding:12px 14px;
  display:grid; gap:4px; }
.part.ok { --c:var(--ok); } .part.watch { --c:var(--warn); background:var(--warnbg); }
.part.warn { --c:var(--bad); background:var(--badbg); } .part.nodata { --c:var(--none); }
.part h2 { display:flex; justify-content:space-between; gap:8px; margin:0; }
.part h2 small { color:var(--c); font-size:.85rem; white-space:nowrap; }
.spark { width:220px; max-width:100%; height:44px; }
.list { display:grid; gap:10px;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr)); }
"""


def predict_html(findings):
    from . import visual
    counts = {lv: sum(1 for f in findings if f.level == lv)
              for lv in (WARN, WATCH, OK, NODATA)}
    stats = "".join(
        '<div class="stat" style="color:var(--%s)"><b>%d</b>%s</div>' % (
            {WARN: "bad", WATCH: "warn", OK: "ok", NODATA: "none"}[lv],
            counts[lv], LEVEL_TEXT[lv]) for lv in (WARN, WATCH, OK, NODATA))
    parts = "".join(
        '<article class="part %s"><h2>%s<small>%s %s</small></h2><div>%s</div>'
        '%s<div class="muted">%s</div></article>' % (
            f.level, html.escape(f.part), LEVEL_ICON[f.level],
            LEVEL_TEXT[f.level], html.escape(f.summary),
            sparkline(f.points, f.limit), html.escape(f.detail))
        for f in findings)
    body = ('<style>%s</style><div class="stats">%s</div><div class="list">%s'
            '</div><p class="muted">เทียบกับ "ปกติของรถคันนี้" และลากเส้นแนวโน้มต่อ'
            'ไปข้างหน้า เส้นประสีแดงคือเกณฑ์ · ระบบต้องมีข้อมูลสะสม 3–4 สัปดาห์ถึงจะรู้'
            'ว่าปกติเป็นเท่าไร และ 2–3 เดือนถึงจะเห็นแนวโน้มชัด · เป็นการเตือนให้เฝ้าดู'
            ' ไม่ใช่การวินิจฉัย ถ้าแดงให้ศูนย์ตรวจ</p>' % (PAGE_CSS, stats, parts))
    return visual.page("เตือนล่วงหน้า: สุขภาพรถ", body)


def write_predict(folder, out):
    with open(out, "w", encoding="utf-8") as f:
        f.write(predict_html(assess(folder)))
    return out
