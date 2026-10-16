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


def test_power_cut_after_a_hit_does_not_write_it_twice(tmp_path):
    clock = Tick()
    s = survey.Survey(str(tmp_path), clock=clock)
    s.start(["754"], [(0xF190, 0xF1AF)])
    elm = Elm({("754", 0xF193): b"\x01", ("754", 0xF195): b"\x02"})
    s.run(elm, 1.0)                     # no flush: the power went
    s2 = survey.Survey(str(tmp_path), clock=clock)
    assert s2.load()
    while s2.run(elm, 100):
        pass
    rows = list(csv.DictReader(open(tmp_path / survey.RESULTS_FILE)))
    assert [r["did"] for r in rows] == ["F193", "F195"]


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


def test_page_shows_survey_progress_while_it_waits(tmp_path, monkeypatch):
    pilot, elm = make_pilot(tmp_path, monkeypatch, [13.9], {
        "soc": 80, "pack_current": 60.0, "motor_rpm": 4000})
    pilot.step()                           # driving: waits, still shown
    status = pilot.state.data["status"]
    assert "สำรวจ 0.0%" in status and "รอ: กำลังขับ" in status
    elm.volts = [12.4]
    pilot.step()                           # asleep
    assert "สำรวจ" in pilot.state.data["status"]


def test_survey_shortens_the_adapter_timer_and_restores_it(tmp_path):
    s = survey.Survey(str(tmp_path), clock=Tick())
    s.start(["7A1"], [(0, 0xFF)])
    elm = Elm({})
    sent = []
    elm._ok = sent.append
    s.run(elm, 0.5)
    assert sent == [survey.QUICK_ST, survey.NORMAL_ST]
    broken = Elm({}, timeout_from=0)
    broken._ok = sent.append
    with pytest.raises(ElmError):
        s.run(broken, 100)
    assert sent[-1] == survey.NORMAL_ST       # restored even on errors


def test_digest_keeps_what_is_worth_a_look(tmp_path):
    p = tmp_path / survey.RESULTS_FILE
    p.write_text(
        "time,header,did,length,raw\n"
        "2026-10-16T20:00:00,754,F187,6,333630330000\n"
        "2026-10-16T20:00:01,754,F190,17,(ซ่อน)\n"
        "2026-10-16T20:00:02,754,F1B0,4,00000000\n"
        "2026-10-16T20:00:03,754,F1F0,1,8D\n"
        "2026-10-16T20:00:04,754,FEF7,20," + "AB" * 20 + "\n"
        "2026-10-16T20:02:00,754,F1F0,1,8E\n"
        "2026-10-16T20:02:01,754,F1F0,1,8E\n", encoding="utf-8")
    lines, newest = survey.digest(str(p))
    assert newest == "2026-10-16T20:02:01"
    assert lines[0] == "== 754: 5 DID (00/FF ล้วน 1) =="
    assert "F187='3603'" in lines[1] and "F190=(ซ่อน)" in lines[1]
    assert "  F1F0 8D → 8E" in lines
    assert "  FEF7 " + "AB" * 16 + "…(20B)" in lines
    # only what came after the last digest
    lines, _ = survey.digest(str(p), since="2026-10-16T20:00:04")
    assert lines == ["== 754: 1 DID (00/FF ล้วน 0) ==", "  F1F0 8E"]


def test_digest_command_sends_only_new_rows(tmp_path, capsys):
    from deepal_s05 import cli
    p = tmp_path / survey.RESULTS_FILE
    p.write_text("time,header,did,length,raw\n"
                 "2026-10-16T20:00:00,7A1,F228,2,0FCA\n", encoding="utf-8")
    assert cli.main(["survey", "digest", "--dir", str(tmp_path)]) == 0
    assert "F228 0FCA" in (tmp_path / survey.DIGEST_FILE).read_text("utf-8")
    capsys.readouterr()
    assert cli.main(["survey", "digest", "--dir", str(tmp_path)]) == 0
    assert "ไม่มีผลใหม่" in capsys.readouterr().out
