import csv
import json

import pytest

from deepal_s05 import autopilot, survey
from deepal_s05.elm327 import ElmError, NegativeResponse, NoData
from deepal_s05.snapshot import Snapshot


class Tick:
    """A clock that moves 0.1 s every time it is read."""

    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        self.t += 0.1
        return self.t


class Elm:
    """Answers a few DIDs, refuses or ignores the rest."""

    def __init__(self, answers, timeout_from=None):
        self.answers = answers
        self.timeout_from = timeout_from
        self.asked = []

    def read_did(self, header, did):
        self.asked.append((header, did))
        if self.timeout_from is not None and did >= self.timeout_from:
            raise ElmError("timeout waiting for reply")
        if (header, did) in self.answers:
            return self.answers[(header, did)]
        if did % 2:
            raise NegativeResponse(0x22, 0x31)
        raise NoData("NO DATA")


def test_plan_puts_likely_ranges_first():
    tasks = survey.plan(["7a1", "700"], [(0xF000, 0xF00F), (0, 0xF)])
    assert [(t["header"], t["start"]) for t in tasks] == [
        ("7A1", 0xF000), ("700", 0xF000), ("7A1", 0), ("700", 0)]


def test_survey_resumes_where_it_stopped(tmp_path):
    clock = Tick()
    s = survey.Survey(str(tmp_path), private={0xF190}, clock=clock)
    s.start(["7A1", "700"], [(0xF18F, 0xF1A0)])
    elm = Elm({("7A1", 0xF190): b"VIN", ("7A1", 0xF195): b"\x01\x02"})
    n = s.run(elm, 1.0)                 # about 10 reads
    assert 0 < n < 18 and s.current()["header"] == "7A1"
    s.flush()
    # a new process (the Pi rebooted) carries on from the saved place
    s2 = survey.Survey(str(tmp_path), private={0xF190}, clock=clock)
    assert s2.load() and s2.done() == n
    while s2.run(elm, 100):
        pass
    assert s2.current() is None and "ครบแล้ว" in s2.status()
    rows = list(csv.DictReader(open(tmp_path / survey.RESULTS_FILE)))
    assert [(r["did"], r["raw"]) for r in rows] == [
        ("F190", "(ซ่อน)"), ("F195", "0102")]
    assert len(elm.asked) == 2 * 18     # every DID asked exactly once


def test_survey_puts_back_dids_when_the_adapter_is_gone(tmp_path):
    s = survey.Survey(str(tmp_path), clock=Tick())
    s.start(["7A1"], [(0x0000, 0x00FF)])
    with pytest.raises(ElmError):
        s.run(Elm({}, timeout_from=0x10), 100)
    # the 5 timed-out DIDs were not counted as checked
    assert s.current()["next"] == 0x10


def test_survey_pause_and_status(tmp_path):
    s = survey.Survey(str(tmp_path), clock=Tick())
    assert "ยังไม่ได้เริ่ม" in s.status()
    s.start(["7A1"], [(0, 0xFF)])
    s.set(paused=True)
    assert not s.active() and s.run(Elm({}), 10) == 0
    assert "หยุดชั่วคราว" in s.status()
    s.set(paused=False, pause_until_wake=1.0)
    assert not s.active() and "รอสตาร์ทรถรอบใหม่" in s.status()


def make_pilot(tmp_path, monkeypatch, volts, values):
    clock = Tick()
    elm = Elm({("7A1", 0x0002): b"\x05"})
    elm.volts = volts
    elm.initialize = lambda: {}
    elm.battery_voltage = lambda: elm.volts[0]
    elm.close = lambda: None
    monkeypatch.setattr(autopilot.snapshot, "take", lambda *a, **k: Snapshot(
        clock.t, dict(values)))
    pilot = autopilot.Autopilot(lambda: elm, [], [], str(tmp_path),
                                clock=clock)
    pilot.survey.start(["7A1"], [(0, 0xFF)])
    return pilot, elm


def test_autopilot_surveys_only_standing_still(tmp_path, monkeypatch):
    pilot, elm = make_pilot(tmp_path, monkeypatch, [13.9], {
        "soc": 80, "pack_current": -16.0, "motor_rpm": 0})
    assert pilot.step() == 0.5             # charging, standing still
    assert elm.asked and "สำรวจ" in pilot.state.data["status"]
    driving, elm2 = make_pilot(tmp_path / "d", monkeypatch, [13.9], {
        "soc": 80, "pack_current": 60.0, "motor_rpm": 4000})
    assert driving.step() == driving.interval and elm2.asked == []
    unread, elm3 = make_pilot(tmp_path / "u", monkeypatch, [13.9], {
        "soc": 80, "pack_current": -16.0})     # motor did not answer
    assert unread.step() == unread.interval and elm3.asked == []


def test_autopilot_waits_two_minutes_when_not_charging(tmp_path,
                                                      monkeypatch):
    pilot, elm = make_pilot(tmp_path, monkeypatch, [13.9], {
        "soc": 80, "pack_current": 3.0, "motor_rpm": 0})  # red light?
    assert pilot.step() == pilot.interval and elm.asked == []
    pilot.clock.t += autopilot.SURVEY_STILL_S
    assert pilot.step() == 0.5 and elm.asked


def test_stop_waits_for_the_car_to_sleep(tmp_path, monkeypatch):
    pilot, elm = make_pilot(tmp_path, monkeypatch, [13.9], {
        "soc": 80, "pack_current": 0.0, "motor_rpm": 0})
    monkeypatch.setattr(autopilot, "boot_time", lambda: 0.0)
    pilot.survey.set(pause_until_wake=1_800_000_000.0 + 10_000)
    pilot.still_since = 0.0                # parked for long already
    pilot.step()                           # service restarted, car on
    assert elm.asked == []
    elm.volts = [12.4]
    pilot.step()                           # the car went to sleep
    assert json.load(open(tmp_path / survey.STATE_FILE))[
        "pause_until_wake"] is False
    elm.volts = [13.9]
    pilot.step()
    pilot.clock.t += autopilot.SURVEY_STILL_S
    pilot.step()
    assert elm.asked                       # started again: carries on
