"""Switch tests: the owner taps a button on the dashboard when doing
something to the car (air-con on, headlights off, a door opened), the Pi
notes the time, and `marks` lists the raw DID bytes that changed at those
moments - the way to work out values that only change when a switch does.

marks.csv in the data folder holds time,event. The recordings searched are
the drive_*.csv files (watch DIDs, 8 per round) and rec_*.csv files from
`obd rec` (every DID every round, the better choice for a test)."""

import csv
import datetime
import glob
import os

from . import snapshot

MARKS_FILE = "marks.csv"
WINDOW_S = 90      # a reading this close before / after a mark counts
QUIET_S = 600      # changes this far from any mark count as noise

# the buttons on the /mark page, in pairs where there is an on and an off
EVENTS = (
    "แอร์ เปิด", "แอร์ ปิด", "แอร์ ลดอุณหภูมิ", "แอร์ เพิ่มอุณหภูมิ",
    "พัดลมแอร์ แรงขึ้น", "พัดลมแอร์ เบาลง",
    "ไฟหน้า เปิด", "ไฟหน้า ปิด", "ไฟเลี้ยวซ้าย", "ไฟเลี้ยวขวา",
    "ที่ปัดน้ำฝน เปิด", "ที่ปัดน้ำฝน ปิด",
    "ประตูคนขับ เปิด", "ประตูคนขับ ปิด", "ฝาท้าย เปิด", "ฝาท้าย ปิด",
    "พวงมาลัย สุดซ้าย", "พวงมาลัย ตรง", "พวงมาลัย สุดขวา",
    "เกียร์ P", "เกียร์ R", "เกียร์ N", "เกียร์ D",
    "โหมด Eco", "โหมด Normal", "โหมด Sport",
    "ชาร์จกลับ น้อย", "ชาร์จกลับ มาก",
    "เหยียบเบรก", "ปล่อยเบรก",
)


def add(data_dir, event, now=None):
    """Note one mark; returns the time written."""
    event = " ".join(str(event).split())[:60]
    t = datetime.datetime.fromtimestamp(now) if now else \
        datetime.datetime.now()
    stamp = t.isoformat(timespec="seconds")
    path = os.path.join(data_dir, MARKS_FILE)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "event"])
        w.writerow([stamp, event])
    return stamp


def read_marks(data_dir):
    path = os.path.join(data_dir, MARKS_FILE)
    out = []
    try:
        with open(path, newline="", encoding="utf-8",
                  errors="replace") as f:
            for r in csv.DictReader(snapshot.no_nul(f)):
                try:
                    t = datetime.datetime.fromisoformat(r["time"])
                except (TypeError, ValueError):
                    continue
                if r.get("event"):
                    out.append((t, r["event"]))
    except OSError:
        pass
    return sorted(out)


def read_did_series(data_dir, start, end):
    """{column: [(time, raw bytes)]} for the did_ columns of drive_ and
    rec_ files, between start and end."""
    series = {}
    paths = glob.glob(os.path.join(data_dir, "drive_*.csv")) + \
        glob.glob(os.path.join(data_dir, "rec_*.csv"))
    for path in sorted(paths):
        try:
            with open(path, newline="", encoding="utf-8",
                      errors="replace") as f:
                for r in csv.DictReader(snapshot.no_nul(f)):
                    try:
                        t = datetime.datetime.fromisoformat(r.get("time"))
                    except (TypeError, ValueError):
                        continue
                    if not start <= t <= end:
                        continue
                    for k, v in r.items():
                        if not (k and k.startswith("did_") and v):
                            continue
                        try:
                            raw = bytes.fromhex(v)
                        except ValueError:
                            continue
                        series.setdefault(k, []).append((t, raw))
        except (OSError, csv.Error):
            continue
    for v in series.values():
        v.sort()
    return series


def changes(series):
    """[(time, column, byte position, old, new)] where a byte changed
    between two readings at most WINDOW_S apart."""
    out = []
    for col, pts in series.items():
        for (t0, a), (t1, b) in zip(pts, pts[1:]):
            if (t1 - t0).total_seconds() > WINDOW_S or len(a) != len(b):
                continue
            for i, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    out.append((t1, col, i, x, y))
    return out


def analyse(data_dir):
    """{event: [(score, column, position, hits, marks, examples)]}: per
    event, the bytes that changed within WINDOW_S after its marks, most
    consistent first. Bytes that also change often far from any mark
    (counters, live values) are left out."""
    marks = read_marks(data_dir)
    if not marks:
        return {}, marks
    pad = datetime.timedelta(seconds=QUIET_S * 2)
    series = read_did_series(data_dir, marks[0][0] - pad, marks[-1][0] + pad)
    found = changes(series)
    times = [t for t, _ in marks]

    def near_mark(t):
        return any(abs((t - m).total_seconds()) <= QUIET_S for m in times)

    when = {}
    for t, col, pos, _, _ in found:
        when.setdefault((col, pos), []).append(t)
    noisy = {}
    for t, col, pos, _, _ in found:
        if not near_mark(t):
            noisy[(col, pos)] = noisy.get((col, pos), 0) + 1
    by_event = {}
    for event in sorted({e for _, e in marks}):
        ts = [t for t, e in marks if e == event]
        hits = {}
        for t, col, pos, old, new in found:
            if (col, pos) in noisy:
                continue
            for m in ts:
                if 0 <= (t - m).total_seconds() <= WINDOW_S:
                    h = hits.setdefault((col, pos), {"marks": set(), "ex": []})
                    h["marks"].add(m)
                    if len(h["ex"]) < 3:
                        h["ex"].append("%02X→%02X" % (old, new))
        # bytes that change after every kind of mark are the car reacting
        # to anything (or a slow counter), not to this switch
        rows = []
        for (col, pos), h in hits.items():
            others = sum(1 for t in when[(col, pos)]
                         if not any(0 <= (t - m).total_seconds() <= WINDOW_S
                                    for m in ts))
            score = len(h["marks"]) / len(ts) - 0.2 * others
            rows.append((score, col, pos, len(h["marks"]), len(ts), h["ex"]))
        rows.sort(key=lambda r: (-r[0], r[1], r[2]))
        by_event[event] = rows
    return by_event, marks


def report(data_dir, top=5):
    by_event, marks = analyse(data_dir)
    if not marks:
        return ("ยังไม่มีการกดปุ่ม: เปิดหน้า /mark บน dashboard แล้วกดปุ่มตอนทำ"
                "อะไรกับรถ (เปิดแอร์ ไฟ ประตู)")
    lines = ["กดปุ่มไปแล้ว %d ครั้ง ตั้งแต่ %s ถึง %s" % (
        len(marks), marks[0][0].strftime("%d/%m %H:%M"),
        marks[-1][0].strftime("%d/%m %H:%M"))]
    for event, rows in by_event.items():
        n = sum(1 for _, e in marks if e == event)
        lines.append("")
        lines.append("■ %s (กด %d ครั้ง)" % (event, n))
        good = [r for r in rows if r[0] > 0][:top]
        if not good:
            lines.append("  ยังไม่เจอค่าที่เปลี่ยนตาม (ค่าที่เก็บอาจไม่มีตัวนี้"
                         " หรืออ่านไม่ทัน ลองใช้ obd rec ... moving)")
        for score, col, pos, hit, total, ex in good:
            _, header, did = col.split("_", 2)
            lines.append("  %s %s ไบต์ %d · เปลี่ยน %d จาก %d ครั้ง · %s" % (
                header, did, pos, hit, total, " ".join(ex)))
    lines.append("")
    lines.append("ทุกค่ายัง CANDIDATE: กดซ้ำหลายรอบ ถ้าเปลี่ยนทุกครั้งค่อยเชื่อ")
    return "\n".join(lines)
