"""Soft tyre warning from the four wheel speeds (indirect TPMS).

A tyre that loses pressure rolls on a slightly smaller radius, so on a
straight road its wheel turns a little faster than the others. Each sample
taken while cruising straight gives every wheel's speed as a share of the
mean of the four; those shares are averaged and compared with the shares
learned while the tyres were known to be right (after `obd tyres reset`).
Many cars without pressure sensors in the wheels work this way.

The wheel speeds come from the brake ECU 780 (FD0C-FD0F, 0.1 km/h, found on
a Thai S05 on 2026-10-17). They are read only above MIN_KMH, so the slower
rounds cost nothing while parked or charging. EXPERIMENTAL: the warning
limit is a guess until a real soft tyre (or one let down on purpose by a
tyre shop) has been seen."""

import json
import os
import time

from . import pids, snapshot

FILE = "tyres.json"
WHEELS = ("หน้าซ้าย", "หน้าขวา", "หลังซ้าย", "หลังขวา")
MIN_KMH = 30          # wheel speeds are 0.1 km/h steps: too coarse below
STRAIGHT_PCT = 1.0    # every wheel this close to the mean: no bend
MAX_TORQUE_NM = 100   # hard acceleration or regen makes the driven wheels slip
BASELINE_N = 300      # 25 minutes of straight driving at 5 s per round
RECENT_N = 200        # running mean over about this many samples
MIN_RECENT = 100      # before any verdict
SAVE_EVERY = 12       # samples between writes (about a minute)
WARN_PCT = 0.5        # also the alert rule in vehicles.DEEPAL_RULES


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def read_wheels(elm):
    """Read the four wheel speeds; {key: km/h} for those that answered."""
    results = snapshot.read_signals(elm, pids.WHEEL_SIGNALS, None)
    return {k: v for k, (_, v, _) in results.items() if v is not None}


def shares(speeds):
    """Each wheel's speed / mean of the four, or None if the sample does
    not count (too slow, cornering, missing wheel)."""
    if len(speeds) != 4 or not all(_is_num(s) for s in speeds):
        return None
    mean = sum(speeds) / 4
    if mean < MIN_KMH:
        return None
    out = [s / mean for s in speeds]
    if any(abs(r - 1) * 100 > STRAIGHT_PCT for r in out):
        return None
    return out


class Monitor:
    def __init__(self, data_dir=None, clock=time.time):
        self.path = os.path.join(data_dir, FILE) if data_dir else None
        self.clock = clock
        self.unsaved = 0
        self.mtime = None
        self.state = self._fresh()
        self._load()

    @staticmethod
    def _fresh():
        return {"baseline": None, "learn_sum": [0.0] * 4, "learn_n": 0,
                "recent": None, "recent_n": 0, "reset": None, "updated": None}

    def _load(self):
        if not self.path:
            return
        try:
            self.mtime = os.path.getmtime(self.path)
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        state = self._fresh()
        state.update({k: data[k] for k in state if k in data})
        self.state = state

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.path)
        self.mtime = os.path.getmtime(self.path)
        self.unsaved = 0

    def reset(self):
        """Forget everything and learn the tyres as they are now."""
        self.state = self._fresh()
        self.state["reset"] = self.clock()
        self.save()

    def add(self, speeds, torque=None):
        """One sample of four wheel speeds; True if it counted."""
        if self.path and os.path.exists(self.path) and \
                os.path.getmtime(self.path) != self.mtime:
            self._load()  # `obd tyres reset` while the service runs
        if _is_num(torque) and abs(torque) > MAX_TORQUE_NM:
            return False
        r = shares(speeds)
        if r is None:
            return False
        st = self.state
        if st["baseline"] is None:
            st["learn_sum"] = [a + b for a, b in zip(st["learn_sum"], r)]
            st["learn_n"] += 1
            if st["learn_n"] >= BASELINE_N:
                st["baseline"] = [a / st["learn_n"] for a in st["learn_sum"]]
                self.unsaved = SAVE_EVERY  # keep what was learned at once
        else:
            n = min(st["recent_n"], RECENT_N - 1)
            old = st["recent"] or r
            st["recent"] = [(o * n + x) / (n + 1) for o, x in zip(old, r)]
            st["recent_n"] += 1
        st["updated"] = self.clock()
        self.unsaved += 1
        if self.unsaved >= SAVE_EVERY:
            self.save()
        return True

    def deviations(self):
        """% each wheel turns faster than when learned, or None."""
        st = self.state
        if st["baseline"] is None or st["recent_n"] < MIN_RECENT:
            return None
        return [(r / b - 1) * 100 for r, b in zip(st["recent"],
                                                   st["baseline"])]

    def summary(self):
        st = self.state
        dev = self.deviations()
        out = {"learn_n": st["learn_n"], "baseline_n": BASELINE_N,
               "recent_n": st["recent_n"], "min_recent": MIN_RECENT,
               "warn_pct": WARN_PCT, "updated": st["updated"],
               "wheels": list(WHEELS), "dev": dev}
        if st["baseline"] is None:
            out["state"] = "learning"
            out["text"] = "กำลังเรียนรู้ยางชุดนี้ (%d/%d ครั้ง ขับทางตรง > %d" \
                " km/h)" % (st["learn_n"], BASELINE_N, MIN_KMH)
        elif dev is None:
            out["state"] = "collecting"
            out["text"] = "เรียนรู้เสร็จแล้ว กำลังเก็บค่าใหม่ (%d/%d)" % (
                st["recent_n"], MIN_RECENT)
        else:
            i = max(range(4), key=lambda k: dev[k])
            out["worst"] = i
            if dev[i] > WARN_PCT:
                out["state"] = "soft"
                out["text"] = "ล้อ%sหมุนเร็วกว่าตอนเรียนรู้ %.2f%% ลมยางอาจอ่อน" \
                    " เช็กลมยางทั้ง 4 ล้อ" % (WHEELS[i], dev[i])
            else:
                out["state"] = "ok"
                out["text"] = "ยางปกติ (ต่างจากตอนเรียนรู้ไม่เกิน %.2f%%)" % \
                    max(abs(d) for d in dev)
        return out

    def values(self):
        """For the alert rules: how much the fastest wheel is up."""
        dev = self.deviations()
        if dev is None:
            return {}
        i = max(range(4), key=lambda k: dev[k])
        return {"tyre_soft_pct": dev[i], "tyre_soft_wheel": WHEELS[i]}

    def round(self, elm, values):
        """Called once per live round: reads the wheels only when moving
        fast enough, adds them to `values` and returns the summary."""
        speed = values.get("speed_kmh")
        if _is_num(speed) and speed >= MIN_KMH:
            wheels = read_wheels(elm)
            values.update(wheels)
            self.add([wheels.get(s.key) for s in pids.WHEEL_SIGNALS],
                     values.get("motor_torque"))
        values.update(self.values())
        return self.summary()


def report(data_dir):
    m = Monitor(data_dir)
    s = m.summary()
    lines = ["เตือนยางอ่อน (เทียบความเร็ว 4 ล้อ, EXPERIMENTAL)", "", s["text"]]
    if s["dev"]:
        lines.append("")
        for name, d in zip(WHEELS, s["dev"]):
            lines.append("  %-10s %+.2f%%" % (name, d))
        lines.append("\n+ = หมุนเร็วขึ้น (ยางเล็กลง/ลมอ่อน) · เตือนเมื่อเกิน +%.1f%%"
                     % WARN_PCT)
    if m.state["reset"]:
        lines.append("เริ่มเรียนรู้เมื่อ " + time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(m.state["reset"])))
    return "\n".join(lines)
