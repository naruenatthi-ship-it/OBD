"""The data folder: when each file was recorded, which ones must be kept,
and which can be cleaned up.

Most files carry their recording time in the name (scan_7A4_20261016-1421,
drive_20261016, aux12v_202610); that time is used for sorting so a file
that is still being appended to (or a page rebuilt on request) does not
jump to the top of the list.
"""

import datetime
import os
import re

SHOWN = (".csv", ".json", ".log", ".html")
# text files the program writes for people (other .txt files stay hidden)
SHOWN_NAMES = {"survey_digest.txt"}
STAMP = re.compile(r"(20\d{2})(\d{2})(\d{2})?(?:[-_](\d{2})(\d{2}))?")

# history the warnings and trends are built from: never cleaned up
KEEP = {"checkup_history.csv", "health_history.csv", "dtc_history.json",
        "predict_state.json", "cell_ir.json"}
KEEP_PREFIX = ("drive_", "charge_", "aux12v_", "rec_", "scan_")
# results of one run that later runs replace: the newest few are kept
SNAPSHOT_PREFIX = ("check_", "ecus_", "dtc_", "freeze_")


def recorded_time(name, mtime):
    """The time in the file name, or the modification time."""
    m = STAMP.search(name)
    if m:
        y, mo, d, h, mi = m.groups()
        try:
            return datetime.datetime(int(y), int(mo), int(d or 1),
                                     int(h or 0), int(mi or 0))
        except ValueError:
            pass
    return datetime.datetime.fromtimestamp(mtime)


def kind(name):
    if name in KEEP:
        return "history"
    if name.endswith(".html"):
        return "page"
    if name.endswith(".log"):
        return "log"
    if name.startswith(SNAPSHOT_PREFIX):
        return "snapshot"
    return "data"


def entries(data_dir):
    """[{name, size, time, kind}] newest recording first."""
    out = []
    for name in os.listdir(data_dir):
        path = os.path.join(data_dir, name)
        if not (os.path.isfile(path) and (name.endswith(SHOWN) or
                                          name in SHOWN_NAMES)):
            continue
        st = os.stat(path)
        out.append({"name": name, "size": st.st_size, "kind": kind(name),
                    "time": recorded_time(name, st.st_mtime)})
    out.sort(key=lambda e: (e["time"], e["name"]), reverse=True)
    return out


def cleanup_plan(data_dir, now=None, log_days=7, snapshot_days=90,
                 keep_snapshots=3):
    """Files that can go: [(name, size, reason)].

    - background-job logs (bg_*.log, rec_*.log) older than `log_days`
    - pages (*.html), rebuilt every time they are opened
    - one-off results (check_, ecus_, dtc_, freeze_) older than
      `snapshot_days`, keeping the newest `keep_snapshots` of each kind

    Recordings (drive_, charge_, rec_*.csv, scan_, aux12v_) and history
    files are never listed: the warnings and trends are built from them.
    """
    now = now or datetime.datetime.now()
    plan, seen = [], {}
    for e in entries(data_dir):
        name, age = e["name"], (now - e["time"]).days
        if e["kind"] == "history" or name.startswith(KEEP_PREFIX) and \
                not name.endswith(".log"):
            continue
        if e["kind"] == "log" and age > log_days:
            plan.append((name, e["size"], "log งานเบื้องหลังเก่ากว่า %d วัน"
                         % log_days))
        elif e["kind"] == "page":
            plan.append((name, e["size"], "หน้ารายงาน สร้างใหม่ได้ทุกเมื่อ"))
        elif e["kind"] == "snapshot":
            prefix = name.split("_", 1)[0]
            seen[prefix] = seen.get(prefix, 0) + 1
            if seen[prefix] > keep_snapshots and age > snapshot_days:
                plan.append((name, e["size"], "ผลครั้งเดียวเก่ากว่า %d วัน"
                             " (เก็บล่าสุดไว้ %d ไฟล์)" % (snapshot_days,
                                                         keep_snapshots)))
    return plan


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0
