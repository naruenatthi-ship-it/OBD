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

FOCUS_S = 900          # after a mark, autopilot reads the watch list fast
FOCUS_PER_ROUND = 120  # watch DIDs per round meanwhile (normally 8)
READ_S = 0.45          # one DID over Bluetooth (18 Nov: 28 reads in 19 s)
ROUND_S = 5.0          # the rest of an autopilot round
ROUNDS = 2             # times each guided test goes through its steps
BACKUP = "watch_live.before_test.csv"

# guided tests on the /mark page: the steps are done in turn, and the ECUs
# whose survey DIDs are read meanwhile (the everyday watch list holds only
# DIDs that moved while driving, so a switch never touched is not in it)
BODY = ("700",)
TESTS = (
    ("แอร์ เปิด/ปิด", ("แอร์ เปิด", "แอร์ ปิด"), BODY),
    ("อุณหภูมิแอร์", ("แอร์ ลดอุณหภูมิ", "แอร์ เพิ่มอุณหภูมิ"), BODY),
    ("พัดลมแอร์", ("พัดลมแอร์ แรงขึ้น", "พัดลมแอร์ เบาลง"), BODY),
    ("ไฟหน้า", ("ไฟหน้า เปิด", "ไฟหน้า ปิด"), BODY),
    ("ที่ปัดน้ำฝน", ("ที่ปัดน้ำฝน เปิด", "ที่ปัดน้ำฝน ปิด"), BODY),
    ("ประตูคนขับ", ("ประตูคนขับ เปิด", "ประตูคนขับ ปิด"), BODY),
    ("ฝาท้าย", ("ฝาท้าย เปิด", "ฝาท้าย ปิด"), BODY),
    ("พวงมาลัย", ("พวงมาลัย สุดซ้าย", "พวงมาลัย สุดขวา"), ("780",)),
    ("โหมดขับ", ("โหมด Sport", "โหมด Eco"), ("7A7", "700")),
    ("ระดับชาร์จกลับ", ("ชาร์จกลับ มาก", "ชาร์จกลับ น้อย"), ("7A7", "700")),
    ("เบรก (จอดอยู่)", ("เหยียบเบรก", "ปล่อยเบรก"), ("780",)),
)


def cycle_s(n):
    """About how long autopilot takes to read n watch DIDs at the fast pace."""
    return n * READ_S + -(-n // FOCUS_PER_ROUND) * ROUND_S


def step_wait(data_dir, lap_s=None):
    """Seconds to hold each step: every watch DID read at least once. Uses
    the seconds per DID autopilot last measured going round its list
    (.watch_lap) when that is fresh, else an estimate for one DID a request
    (the slow case: ECUs that refuse multi-DID requests)."""
    from . import autopilot
    n = len(autopilot.load_watch(data_dir))
    if lap_s is None:
        path = os.path.join(data_dir, ".watch_lap")
        try:
            import time
            if time.time() - os.path.getmtime(path) < 4 * 3600:
                with open(path) as f:
                    lap_s = float(f.read()) * n
        except (OSError, ValueError):
            pass
    if lap_s is None:
        lap_s = cycle_s(n)
    return int(max(20, lap_s * 1.3 + 5))


def prepare(data_dir, headers):
    """Swap the watch list for the survey DIDs of `headers` for a guided
    test (autopilot picks it up within a round); the everyday list is
    kept in BACKUP. Returns the number of DIDs."""
    import shutil
    from . import autopilot, pids, reverse, vehicles
    from .cli import PRIVATE_DIDS
    source = os.path.join(data_dir, "survey_results.csv")
    if not os.path.exists(source):
        return 0
    known = {(s.header.upper(), "%04X" % s.did)
             for s in vehicles.deepal_s05().signals + pids.WHEEL_SIGNALS}
    wanted = {h.upper() for h in headers}
    keys = [k for k in sorted(reverse.read_scan(source))
            if k[0] in wanted and k not in known and
            int(k[1], 16) not in PRIVATE_DIDS]
    if not keys:
        return 0
    path = os.path.join(data_dir, autopilot.WATCH_FILE)
    backup = os.path.join(data_dir, BACKUP)
    if not os.path.exists(backup):
        if os.path.exists(path):
            shutil.copyfile(path, backup)
        else:
            open(backup, "w").close()   # empty: the default list
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["header", "did"])
        w.writerows(keys)
    return len(keys)


def restore(data_dir):
    """Put the everyday watch list back after a guided test."""
    from . import autopilot
    path = os.path.join(data_dir, autopilot.WATCH_FILE)
    backup = os.path.join(data_dir, BACKUP)
    if not os.path.exists(backup):
        return
    if os.path.getsize(backup):
        os.replace(backup, path)
    else:
        os.remove(backup)
        if os.path.exists(path):
            os.remove(path)


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
    days = set()
    d = start.date()
    while d <= end.date():
        days.add(d.strftime("%Y%m%d"))
        d += datetime.timedelta(days=1)
    # only the days of the marks: a Pi Zero reading every drive file of
    # the month for one page would take too long
    paths = [p for p in glob.glob(os.path.join(data_dir, "drive_*.csv"))
             if os.path.basename(p)[6:14] in days]
    paths += [p for p in glob.glob(os.path.join(data_dir, "rec_*.csv"))
              if any(day in os.path.basename(p) for day in days)]
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


def analyse_states(data_dir, settle_s=15):
    """For the guided tests: bytes whose value stayed the same while a step
    held and differed between its steps (e.g. one value in Sport, another
    in Eco), each step seen at least twice. Works even when a DID is read
    only once per step, unlike the change-after-a-mark search."""
    marks = read_marks(data_dir)
    out = {}
    if not marks:
        return out
    pad = datetime.timedelta(seconds=QUIET_S)
    series = read_did_series(data_dir, marks[0][0], marks[-1][0] + pad)
    for name, steps, _ in TESTS:
        ms = [(t, e) for t, e in marks if e in steps]
        if len({e for _, e in ms}) < 2:
            continue
        ends = [t for t, _ in marks]

        def state(t):
            # unknown right after any mark: the car may not have switched yet
            if any(0 <= (t - o).total_seconds() < settle_s for o in ends):
                return None
            last = max(((m, e) for m, e in ms if m <= t), default=None)
            if last is None:
                return None
            # a mark of another kind since then ends the state
            if any(last[0] < o <= t for o, e in marks if e not in steps):
                return None
            return last[1]
        found = []
        for col, pts in series.items():
            seen = [(state(t), raw) for t, raw in pts]
            seen = [(st, raw) for st, raw in seen if st is not None]
            if not seen:
                continue
            width = min(len(raw) for _, raw in seen)
            for pos in range(width):
                vals = {}
                count = {}
                for st, raw in seen:
                    vals.setdefault(st, set()).add(raw[pos])
                    count[st] = count.get(st, 0) + 1
                if len(vals) < 2 or any(len(v) != 1 for v in vals.values()):
                    continue
                if min(count.values()) < 2:
                    continue
                values = {st: next(iter(v)) for st, v in vals.items()}
                if len(set(values.values())) == len(values):
                    found.append((col, pos, values, min(count.values())))
        found.sort(key=lambda f: -f[3])
        out[name] = found
    return out


def report(data_dir, top=5):
    by_event, marks = analyse(data_dir)
    if not marks:
        return ("ยังไม่มีการกดปุ่ม: เปิดหน้า /mark บน dashboard แล้วกดปุ่มตอนทำ"
                "อะไรกับรถ (เปิดแอร์ ไฟ ประตู)")
    states = analyse_states(data_dir)
    lines = []
    for name, found in states.items():
        lines.append("★ %s: ค่าที่ตรงกับสถานะ" % name)
        if not found:
            lines.append("  ยังไม่เจอ (ค่านี้อาจไม่อยู่ในกล่องที่อ่าน หรืออ่านไม่ครบทุกขั้น)")
        for col, pos, values, n in found[:top]:
            _, header, did = col.split("_", 2)
            lines.append("  %s %s ไบต์ %d · %s (อ่าน ≥%d ครั้ง/ขั้น)" % (
                header, did, pos, " · ".join(
                    "%s = %02X" % (st, v) for st, v in sorted(values.items())),
                n))
        lines.append("")
    lines += ["กดปุ่มไปแล้ว %d ครั้ง ตั้งแต่ %s ถึง %s" % (
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
