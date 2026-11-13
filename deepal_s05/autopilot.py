"""Unattended mode for a Raspberry Pi that stays in the car.

The 12 V voltage measured by the adapter itself (ATRV, no CAN traffic)
decides what to do: above `wake_v` the DC-DC converter is running, so the
car is awake and the BMS is polled, logged and checked against the alert
rules; below it nothing is sent to the car and only the 12 V voltage is
logged now and then, so the Pi never keeps the car awake.

The one exception is charging: with the car off the DC-DC converter does
not run, the 12 V battery rests near 12.6 V and the car looks asleep while
the BMS is busy charging. For CHARGE_PROBE_WINDOW after the car was last
awake (or the Pi started), the pack current is asked once every
CHARGE_PROBE_EVERY; if it shows charging the round is logged like an awake
one until the charge ends. The first time the BMS does not answer, the
probing stops until the car is next awake.
"""

import datetime
import json
import os
import threading
import time
import traceback

from . import (alerts, analysis, cost, dashboard, identity, pids, snapshot,
               survey, tyres)
from .elm327 import ElmError, NegativeResponse, NoData

WATCH_FILE = "watch_live.csv"  # DIDs still being worked out (header,did)
CHARGE_PROBE_EVERY = 300.0     # seconds between "are you charging?" asks
CHARGE_PROBE_WINDOW = 3 * 3600  # only this long after the car was awake
WATCH_PER_ROUND = 8            # 32 DIDs: each one about every 4 rounds


def load_watch(data_dir, private=()):
    """(header, did) pairs to log raw in every drive file: a
    watch_live.csv in the data folder, else the one in examples/."""
    import csv
    path = os.path.join(data_dir, WATCH_FILE)
    if not os.path.exists(path):
        path = os.path.join(os.path.dirname(__file__), "..", "examples",
                            WATCH_FILE)
    keys = []
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(snapshot.no_nul(f)):
                header = (row.get("header") or "").strip().upper()
                did = (row.get("did") or "").strip().upper()
                try:
                    num = int(did, 16)
                except ValueError:
                    continue
                if header and num not in private and \
                        (header, did) not in keys:
                    keys.append((header, did))
    except OSError:
        return []
    return keys


class WatchReader:
    """Reads a few of the watch DIDs each round, in turn, so the BMS values
    keep their pace and every watch DID still comes round every ~30 s.
    Raw hex goes into columns did_<header>_<did> (the `obd rec` names);
    a DID the ECU refuses is skipped until the car wakes up again."""

    def __init__(self, keys, per_round=WATCH_PER_ROUND):
        self.keys, self.per_round = list(keys), per_round
        self.pos = 0
        self.refused = set()

    def wake(self):
        self.refused.clear()

    def round(self, elm):
        live = [k for k in self.keys if k not in self.refused]
        # every column from the first row: the day's file is rewritten
        # once for new columns, not once per DID
        out = {"did_%s_%s" % k: "" for k in live}
        for _ in range(min(self.per_round, len(live))):
            key = live[self.pos % len(live)]
            self.pos += 1
            try:
                out["did_%s_%s" % key] = elm.read_did(
                    key[0], int(key[1], 16)).hex().upper()
            except NegativeResponse:
                self.refused.add(key)
            except NoData:
                pass
        return out


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


def boot_time():
    """When the Pi started (seconds since the epoch), 0 if unknown."""
    try:
        with open("/proc/uptime") as f:
            return time.time() - float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


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


SURVEY_STILL_S = 120.0


class Autopilot:
    def __init__(self, open_elm, signals, arrays, data_dir,
                 capacity=pids.DEFAULT_CAPACITY_KWH, rules=None,
                 notifier=None, interval=5.0, wake_v=13.0,
                 asleep_poll=60.0, rest_log_every=300.0, retry=30.0,
                 checkup_days=7.0, run_checkup=None, shutdown_below=None,
                 shutdown_after=600.0, shutdown=None, clock=time.time,
                 info=None, vehicle=None, baselines=None, private=(),
                 watch=None):
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
        self.dc = analysis.DcSession(
            capacity, cost.load_tariff(data_dir)["dc_price"])
        self.slow = snapshot.GroupCache()  # per-cell values every 30 s
        # watch=None: the default list; [] turns it off
        keys = load_watch(data_dir, private) if watch is None else watch
        self.watch = WatchReader(keys) if keys else None
        self.tyres = tyres.Monitor(data_dir, clock) if any(
            s.key == "motor_rpm" for s in signals) else None
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
        self.survey = survey.Survey(data_dir, private, clock)
        self.still_since = None
        self.charging = False       # charging with the car off
        self.awake_seen = clock()   # last awake round, or the Pi's start
        self.last_probe = None
        self.probe_off = False      # the BMS did not answer: wait for a wake
        self.last_values = {}

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
            self.probe_off = False
            return self.awake_round(now, v12)
        if not self.charging and self.charge_probe(now):
            self.charging = True
        if self.charging:
            wait = self.awake_round(now, v12)
            current = self.last_values.get("pack_current")
            if current is None or current > -1:
                self.charging = False   # done: the next round sleeps
            else:
                self.state.update(status="กำลังชาร์จ (รถไม่ได้เปิด) กำลังบันทึก")
            return wait
        return self.asleep_round(now, v12)

    def charge_probe(self, now):
        """With the car looking asleep: one pack current request, at most
        every CHARGE_PROBE_EVERY and only within CHARGE_PROBE_WINDOW of the
        car being awake. True when the pack is taking charge."""
        if self.probe_off or now - self.awake_seen > CHARGE_PROBE_WINDOW:
            return False
        if self.last_probe is not None and \
                now - self.last_probe < CHARGE_PROBE_EVERY:
            return False
        sig = next((s for s in self.signals if s.key == "pack_current"),
                   None)
        if sig is None:
            return False
        self.last_probe = now
        try:
            current = sig.value(self.elm.read_did(sig.header, sig.did),
                                self.capacity)
        except (ElmError, OSError):
            current = None
        if current is None:
            self.probe_off = True
            return False
        return current < -2  # charging is negative on the S05

    def awake_round(self, now, v12):
        self.low_since = None
        self.awake_seen = now
        if self.awake_since is None:
            self.awake_since = now
            # `obd stop` paused the survey until the car is started again;
            # a Pi that lost power with the car has rebooted since then
            paused = self.survey.load() and \
                self.survey.state["pause_until_wake"]
            if paused and boot_time() > paused:
                self.survey.set(pause_until_wake=False)
            if self.watch is not None:
                self.watch.wake()
        try:
            snap = snapshot.take(self.elm, self.signals, self.arrays,
                                 self.capacity, slow=self.slow)
        except (ElmError, OSError) as e:
            self.disconnect()
            self.state.update(status="อ่านค่าไม่ได้ (%s)" % e)
            return self.retry
        values = snap.values
        self.last_values = values
        if values.get("soc") is None:
            self.state.update(time=snap.time, values=values, arrays={},
                              alerts=[], status="รถตื่นแต่ BMS ยังไม่ตอบ")
            return self.interval
        tyre = None
        if self.tyres is not None:
            try:
                tyre = self.tyres.round(self.elm, values)
            except (ElmError, OSError):
                pass  # the next round reads the wheels again
        logged = snap
        if self.watch is not None:
            try:
                extra = self.watch.round(self.elm)
            except (ElmError, OSError):
                extra = {}  # the next round reconnects if it is gone
            if extra:
                # raw hex is for the file only, not the dashboard
                logged = snapshot.Snapshot(snap.time,
                                           dict(snap.values, **extra),
                                           snap.arrays, snap.errors)
        self.log_drive(logged)
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
                          dc=self.dc.update(snap.time, values), tyres=tyre,
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
        # the DID survey uses the time between rounds, only while parked:
        # the motor must be read and stopped (an unread motor is not "0")
        # for SURVEY_STILL_S, so a stop at a red light does not count;
        # charging counts at once
        rpm = values.get("motor_rpm")
        if rpm is None or abs(rpm) >= 50:
            self.still_since = None
        elif self.still_since is None:
            self.still_since = now
        charging = (values.get("pack_current") or 0) < -2
        parked = self.still_since is not None and (
            charging or now - self.still_since >= SURVEY_STILL_S)
        if parked and self.survey.active():
            try:
                self.survey.run(self.elm, self.interval - 1)
            except (ElmError, OSError) as e:  # the adapter, not one DID
                self.survey.flush()
                self.disconnect()
                self.state.update(status="สำรวจ DID สะดุด (%s)" % e)
                return self.retry
            self.state.update(status="รถตื่น กำลังบันทึก · " +
                              self.survey.status())
            return 0.5
        if self.survey.load():
            # say why the survey is waiting, so the page still shows it
            if self.survey.active():
                why = "รอ: อ่านรอบมอเตอร์ไม่ได้" if rpm is None else \
                    "รอ: กำลังขับ" if self.still_since is None else \
                    "รอจอดนิ่งครบ %d นาที" % (SURVEY_STILL_S // 60)
            else:
                why = ""
            self.state.update(status="รถตื่น กำลังบันทึก · " +
                              self.survey.status() +
                              (" · " + why if why else ""))
        return self.interval

    def run_predict(self):
        """After each drive or charge: early warnings that are new or got
        worse go out through the notifier (once, remembered on disk)."""
        from . import predict
        try:
            findings = predict.assess(self.data_dir)
            if self.notifier:
                predict.notify_changes(findings,
                                       self.path("predict_state.json"),
                                       self.notifier.send)
        except Exception:  # a broken file must not stop the logging
            traceback.print_exc()

    def asleep_round(self, now, v12):
        self.still_since = None
        if self.awake_since is not None:
            self.awake_since = None
            self.close_drive_log()
            self.survey.flush()
            self.run_predict()
        # the car slept: a survey paused by `obd stop` goes on next start
        if self.survey.load() and self.survey.state["pause_until_wake"]:
            self.survey.set(pause_until_wake=False)
        if self.last_rest_log is None or \
                now - self.last_rest_log >= self.rest_log_every:
            month = datetime.datetime.fromtimestamp(now).strftime("%Y%m")
            with snapshot.CsvLog(self.path("aux12v_%s.csv" % month),
                                 "rest") as log:
                log.write(snapshot.Snapshot(now, {"aux_12v": v12}))
            self.last_rest_log = now
        status = "รถหลับ ไม่ส่งข้อความเข้ารถ · แบต 12V %.2f V" % v12
        if self.survey.load():
            status += " · " + self.survey.status()
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
