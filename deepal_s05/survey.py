"""A DID survey that runs a little at a time inside the autopilot and picks
up where it stopped, over as many car sessions as it takes: every ECU,
every DID 0000-FFFF, read-only (UDS 22, default session).

It only runs while the car is awake and standing still (parked with the
car on, or charging): reading thousands of unknown DIDs from the brake,
steering and radar ECUs is not something to do while driving.

State is kept in survey.json, results in survey_results.csv, both in the
data folder, so a Pi that loses power loses at most the last minute.
"""

import csv
import datetime
import json
import os
import time

from .elm327 import ElmError, NegativeResponse, NoData

STATE_FILE = "survey.json"
RESULTS_FILE = "survey_results.csv"
# where live data usually is first, the rest of the DID space after
DEFAULT_RANGES = [(0xF000, 0xFFFF), (0xD000, 0xEFFF), (0x0000, 0xCFFF)]
SAVE_EVERY_S = 60.0
# this many timeouts in a row means the adapter, not the DID: stop and
# read them again later
MAX_TIMEOUTS = 5
# Most DIDs get no answer at all, and the adapter waits its full answer
# timer for each (ATST32 = 200 ms): about 250 DIDs a minute on silent ECUs
# against 500 on ECUs that refuse quickly. ECUs answer within 50 ms
# (ISO 14229 P2), so the survey asks the adapter to wait 100 ms.
QUICK_ST, NORMAL_ST = "ATST19", "ATST32"


def plan(headers, ranges=DEFAULT_RANGES):
    """Tasks in order: each range for every header before the next range,
    so the likely places on every ECU come first."""
    return [{"header": h.upper(), "start": lo, "end": hi, "next": lo}
            for lo, hi in ranges for h in headers]


class Survey:
    def __init__(self, data_dir, private=(), clock=time.time):
        self.path = os.path.join(data_dir, STATE_FILE)
        self.results = os.path.join(data_dir, RESULTS_FILE)
        self.private = set(private)
        self.clock = clock
        self.state = None
        self.mtime = None
        self.last_save = 0.0

    # -- state -----------------------------------------------------------
    def load(self):
        """Read the state when the file changed (obd survey commands
        edit it while the autopilot runs). False when there is none."""
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self.state = None
            return False
        if mtime != self.mtime:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.state = json.load(f)
                self.mtime = mtime
            except (OSError, ValueError):
                self.state = None
        return self.state is not None

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
            f.flush()
            os.fsync(f.fileno())  # the car may cut the power any moment
        os.replace(tmp, self.path)  # never a half-written state
        self.mtime = os.path.getmtime(self.path)
        self.last_save = self.clock()

    def start(self, headers, ranges=DEFAULT_RANGES):
        self.state = {"tasks": plan(headers, ranges), "paused": False,
                      "pause_until_wake": False, "checked": 0, "found": 0,
                      "busy_s": 0.0, "started": _now()}
        self.save()

    def set(self, **kw):
        if self.load():
            self.state.update(kw)
            self.save()

    # -- progress --------------------------------------------------------
    def total(self):
        return sum(t["end"] - t["start"] + 1 for t in self.state["tasks"])

    def done(self):
        return sum(t["next"] - t["start"] for t in self.state["tasks"])

    def current(self):
        return next((t for t in self.state["tasks"] if t["next"] <= t["end"]),
                    None)

    def status(self):
        """One line for people."""
        if not self.load():
            return "ยังไม่ได้เริ่มสำรวจ (obd survey start)"
        total, done = self.total(), self.done()
        cur = self.current()
        if cur is None:
            return "สำรวจครบแล้ว %d DID เจอ %d ค่า" % (total,
                                                    self.state["found"])
        rate = self.state["checked"] / self.state["busy_s"] \
            if self.state["busy_s"] else 0
        left = (total - done) / rate / 3600 if rate else None
        state = "หยุดชั่วคราว" if self.state["paused"] else \
            "รอสตาร์ทรถรอบใหม่" if self.state["pause_until_wake"] else \
            "ทำงานเมื่อรถเปิดและจอดนิ่ง"
        return ("สำรวจ %.1f%% (%d/%d) เจอ %d ค่า · ตอนนี้ %s:%04X · %s%s" % (
            done / total * 100, done, total, self.state["found"],
            cur["header"], cur["next"], state,
            " · เหลือราว %.0f ชม.ที่รถเปิดจอด" % left if left else ""))

    def active(self):
        return self.load() and not self.state["paused"] and \
            not self.state["pause_until_wake"] and self.current() is not None

    # -- work ------------------------------------------------------------
    def run(self, elm, budget_s):
        """Read DIDs for up to `budget_s` seconds; returns how many. A DID
        that is refused or silent is passed over; a run of timeouts is
        treated as a lost adapter: those DIDs are put back and the error
        raised."""
        if not self.active():
            return 0
        t0 = self.clock()
        n = timeouts = 0
        self._timer(elm, QUICK_ST)
        try:
            while self.clock() - t0 < budget_s:
                task = self.current()
                if task is None:
                    break
                did = task["next"]
                raw = None
                try:
                    raw = elm.read_did(task["header"], did)
                    timeouts = 0
                except (NoData, NegativeResponse):
                    timeouts = 0
                except ElmError:
                    timeouts += 1
                    if timeouts >= MAX_TIMEOUTS:
                        back = min(MAX_TIMEOUTS - 1, did - task["start"])
                        task["next"] = did - back
                        n -= back
                        raise
                task["next"] = did + 1
                n += 1
                if raw is not None:
                    self.state["found"] += 1
                    self._write(task["header"], did, raw)
                    # a power cut must not send the survey back over
                    # DIDs already written down (rows came out twice)
                    self.save()
        finally:
            self._timer(elm, NORMAL_ST)
            self.state["checked"] += max(n, 0)
            self.state["busy_s"] += self.clock() - t0
            if self.clock() - self.last_save >= SAVE_EVERY_S or \
                    self.current() is None:
                self.save()
        return n

    @staticmethod
    def _timer(elm, cmd):
        """Set the adapter's answer timer; not every adapter (or test
        stand-in) takes it, and the survey works either way."""
        setter = getattr(elm, "_ok", None)
        if setter is None:
            return
        try:
            setter(cmd)
        except ElmError:
            pass

    def flush(self):
        if self.state is not None:
            self.save()

    def _write(self, header, did, raw):
        new = not os.path.exists(self.results)
        with open(self.results, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "header", "did", "length", "raw"])
            w.writerow([_now(), header, "%04X" % did, len(raw),
                        "(ซ่อน)" if did in self.private else
                        raw.hex().upper()])
            f.flush()
            os.fsync(f.fileno())  # hits are rare; keep each one


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")
