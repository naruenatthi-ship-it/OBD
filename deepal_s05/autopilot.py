"""Unattended mode for a Raspberry Pi that stays in the car.

The 12 V voltage measured by the adapter itself (ATRV, no CAN traffic)
decides what to do: above `wake_v` the DC-DC converter is running, so the
car is awake and the BMS is polled, logged and checked against the alert
rules; below it nothing is sent to the car and only the 12 V voltage is
logged now and then, so the Pi never keeps the car awake.
"""

import datetime
import json
import os
import threading
import time
import traceback

from . import alerts, dashboard, identity, pids, snapshot
from .elm327 import ElmError


class State:
    """What the dashboard shows; json() is called from the HTTP thread."""

    def __init__(self, signals, arrays, info=None):
        labels = dict(dashboard.LABELS)
        labels.update({s.key: (s.label, s.unit) for s in signals
                       if s.status == pids.CUSTOM})
        labels.update({a.key: (a.label, a.unit) for a in arrays})
        self.lock = threading.Lock()
        self.data = {"time": None, "values": {}, "arrays": {}, "errors": {},
                     "alerts": [], "error": None, "status": "กำลังเริ่ม...",
                     "labels": labels, "files": True,
                     "custom": [s.key for s in signals
                                if s.status == pids.CUSTOM],
                     "info": info or {}}

    def update(self, **kw):
        with self.lock:
            self.data.update(kw)

    def json(self):
        with self.lock:
            return json.dumps(self.data, ensure_ascii=False)


def last_checkup_time(path):
    """Time of the newest row in checkup_history.csv, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            rows = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(rows[1:]):
        try:
            return datetime.datetime.fromisoformat(line.split(",", 1)[0])
        except ValueError:
            continue
    return None


class Autopilot:
    def __init__(self, open_elm, signals, arrays, data_dir,
                 capacity=pids.DEFAULT_CAPACITY_KWH, rules=None,
                 notifier=None, interval=5.0, wake_v=13.0,
                 asleep_poll=60.0, rest_log_every=300.0, retry=30.0,
                 checkup_days=7.0, run_checkup=None, shutdown_below=None,
                 shutdown_after=600.0, shutdown=None, clock=time.time,
                 info=None, vehicle=None, baselines=None):
        self.open_elm = open_elm
        self.signals, self.arrays = signals, arrays
        self.data_dir = data_dir
        self.capacity = capacity
        self.rules = alerts.DEFAULT_RULES if rules is None else rules
        self.notifier = notifier
        self.interval, self.wake_v = interval, wake_v
        self.asleep_poll, self.rest_log_every = asleep_poll, rest_log_every
        self.retry = retry
        self.checkup_days = checkup_days
        self.run_checkup = run_checkup
        self.shutdown_below, self.shutdown_after = shutdown_below, \
            shutdown_after
        self.shutdown = shutdown
        self.clock = clock
        self.state = State(signals, arrays, info)
        self.baselines = baselines or {}  # usual values, from checkups
        self.vehicle = vehicle  # BMS identification is read once when awake
        self.bms_read = vehicle is None
        self.elm = None
        self.awake_since = None
        self.low_since = None
        self.last_rest_log = None
        self.drive_log = None
        self.drive_log_day = None
        os.makedirs(data_dir, exist_ok=True)

    # -- helpers ---------------------------------------------------------
    def path(self, name):
        return os.path.join(self.data_dir, name)

    def disconnect(self):
        if self.elm is not None:
            try:
                self.elm.close()
            except OSError:
                pass
        self.elm = None

    def close_drive_log(self):
        if self.drive_log:
            self.drive_log.__exit__()
        self.drive_log = None
        self.drive_log_day = None

    def log_drive(self, snap):
        day = datetime.datetime.fromtimestamp(snap.time).strftime("%Y%m%d")
        if day != self.drive_log_day:
            self.close_drive_log()
            self.drive_log = snapshot.CsvLog(self.path("drive_%s.csv" % day),
                                             "drive")
            self.drive_log_day = day
        self.drive_log.write(snap)

    def checkup_due(self, now):
        if not self.run_checkup or not self.checkup_days:
            return False
        last = last_checkup_time(self.path("checkup_history.csv"))
        if last is None:
            return True
        age = datetime.datetime.fromtimestamp(now) - last
        return age.total_seconds() >= self.checkup_days * 86400

    # -- one round ---------------------------------------------------------
    def step(self):
        """Do one round of work; returns the seconds to wait before the
        next one."""
        now = self.clock()
        if self.elm is None:
            try:
                self.elm = self.open_elm()
                self.elm.initialize()
            except (ElmError, OSError, ValueError) as e:
                self.disconnect()
                self.state.update(status="เชื่อมต่อกล่อง OBD ไม่ได้ (%s)"
                                  " จะลองใหม่" % e)
                return self.retry
        try:
            v12 = self.elm.battery_voltage()
        except (ElmError, OSError) as e:
            self.disconnect()
            self.state.update(status="กล่อง OBD ไม่ตอบ (%s)" % e)
            return self.retry

        if v12 >= self.wake_v:
            return self.awake_round(now, v12)
        return self.asleep_round(now, v12)

    def awake_round(self, now, v12):
        self.low_since = None
        if self.awake_since is None:
            self.awake_since = now
        try:
            snap = snapshot.take(self.elm, self.signals, self.arrays,
                                 self.capacity)
        except (ElmError, OSError) as e:
            self.disconnect()
            self.state.update(status="อ่านค่าไม่ได้ (%s)" % e)
            return self.retry
        values = snap.values
        if values.get("soc") is None:
            self.state.update(time=snap.time, values=values, arrays={},
                              alerts=[], status="รถตื่นแต่ BMS ยังไม่ตอบ")
            return self.interval
        self.log_drive(snap)
        if not self.bms_read:
            self.bms_read = True
            try:
                _, bms = identity.bms_identity(self.elm, self.vehicle)
                with self.state.lock:
                    self.state.data["info"]["bms"] = bms
            except (ElmError, OSError):
                pass
        checked = dict(values)
        checked["delta_mv"] = values.get("cells_delta_mv",
                                         values.get("cell_delta_mv"))
        found = alerts.evaluate(alerts.add_baselines(checked, self.baselines),
                                self.rules)
        if self.notifier and found:
            self.notifier.notify(found)
        self.state.update(time=snap.time, values=values, arrays=snap.arrays,
                          errors=snap.errors, error=None,
                          status="รถตื่น กำลังบันทึก",
                          alerts=[{"level": a.level, "text": a.text()}
                                  for a in found])
        parked = abs(values.get("pack_current") or 0) < 5
        if parked and now - self.awake_since >= 60 and self.checkup_due(now):
            self.state.update(status="กำลังตรวจรถประจำสัปดาห์ (checkup)")
            self.disconnect()  # checkup opens its own connection
            try:
                self.run_checkup()
            finally:
                self.state.update(status="ตรวจรถเสร็จแล้ว")
                self.baselines = alerts.load_baselines(
                    self.path("checkup_history.csv"))
            return 1.0
        return self.interval

    def asleep_round(self, now, v12):
        if self.awake_since is not None:
            self.awake_since = None
            self.close_drive_log()
        if self.last_rest_log is None or \
                now - self.last_rest_log >= self.rest_log_every:
            month = datetime.datetime.fromtimestamp(now).strftime("%Y%m")
            with snapshot.CsvLog(self.path("aux12v_%s.csv" % month),
                                 "rest") as log:
                log.write(snapshot.Snapshot(now, {"aux_12v": v12}))
            self.last_rest_log = now
        status = "รถหลับ ไม่ส่งข้อความเข้ารถ · แบต 12V %.2f V" % v12
        if self.shutdown_below is not None and v12 < self.shutdown_below:
            if self.low_since is None:
                self.low_since = now
            left = self.shutdown_after - (now - self.low_since)
            status += " (ต่ำกว่า %.1f V จะปิด Pi ใน %d วินาที)" % (
                self.shutdown_below, max(left, 0))
            if left <= 0 and self.shutdown:
                if self.notifier:
                    self.notifier.notify([alerts.Alert(
                        alerts.CRIT, "pi_shutdown", "แบต 12V", v12, "V",
                        "แบต 12V %.2f V ต่ำ ปิด Raspberry Pi เพื่อถนอมแบต"
                        % v12)])
                self.state.update(status="ปิดเครื่องเพราะแบต 12V ต่ำ")
                self.shutdown()
        else:
            self.low_since = None
        self.state.update(time=now, values={"aux_12v": v12}, arrays={},
                          alerts=[], errors={}, status=status)
        return self.asleep_poll

    def run(self, stop):
        while not stop.is_set():
            try:
                wait = self.step()
            except Exception:  # keep running unattended; show the problem
                self.disconnect()
                self.state.update(status="ผิดพลาด: %s" %
                                  traceback.format_exc(limit=1))
                traceback.print_exc()
                wait = self.retry
            stop.wait(wait)
        self.close_drive_log()
        self.disconnect()
