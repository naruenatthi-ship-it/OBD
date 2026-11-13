import csv
import json
import os
import socket
import threading
import urllib.request

import pytest

from deepal_s05 import autopilot, dashboard
from deepal_s05.elm327 import ElmError, SocketPort, open_rfcomm
from deepal_s05.snapshot import Snapshot


def test_socket_port():
    a, b = socket.socketpair()
    port = SocketPort(a, timeout=0.05)
    b.sendall(b"old junk")
    port.reset_input_buffer()
    assert port.read(10) == b""          # timeout, nothing left
    port.write(b"ATI\r")
    assert b.recv(10) == b"ATI\r"
    b.sendall(b"ELM327 v2.2\r\r>")
    assert port.read(64) == b"ELM327 v2.2\r\r>"
    port.close()
    b.close()


def test_open_rfcomm_rejects_bad_address():
    with pytest.raises(ValueError):
        open_rfcomm("rfcomm://not-a-mac")


class FakeElm:
    def __init__(self, volts):
        self.volts = list(volts)
        self.closed = False

    def initialize(self):
        return {}

    def battery_voltage(self):
        v = self.volts.pop(0) if len(self.volts) > 1 else self.volts[0]
        if v is None:
            raise ElmError("no reply")
        return v

    def close(self):
        self.closed = True

    # watch DIDs: 780 FD0C answers, 700 F1A8 is refused, the rest silent
    def read_did(self, header, did):
        from deepal_s05.elm327 import NegativeResponse, NoData
        self.watch_reads = getattr(self, "watch_reads", 0) + 1
        if (header, did) == ("780", 0xFD0C):
            return bytes([0x01, 0x2C])
        if (header, did) == ("700", 0xF1A8):
            raise NegativeResponse(0x22, 0x31)
        raise NoData("NO DATA")


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(tmp_path, volts, values=None, **kw):
    clock = Clock()
    elm = FakeElm(volts)
    taken = []

    def take(e, signals, arrays, capacity, slow=None):
        taken.append(clock.t)
        return Snapshot(clock.t, dict(values or {
            "soc": 80, "pack_current": 1.0, "batt_temp_max": 30,
            "aux_12v": 14.0}))

    pilot = autopilot.Autopilot(lambda: elm, [], [], str(tmp_path),
                                clock=clock, **kw)
    return pilot, elm, clock, taken, take


def test_asleep_sends_nothing_and_logs_12v(tmp_path, monkeypatch):
    pilot, elm, clock, taken, take = make(tmp_path, [12.55])
    monkeypatch.setattr(autopilot.snapshot, "take", take)
    assert pilot.step() == pilot.asleep_poll
    assert taken == []
    data = json.loads(pilot.state.json())
    assert "รถหลับ" in data["status"] and data["values"] == {"aux_12v": 12.55}
    files = [f for f in os.listdir(tmp_path) if f.startswith("aux12v_")]
    assert len(files) == 1
    clock.t += 60                                  # not yet 5 minutes
    pilot.step()
    rows = list(csv.DictReader(open(tmp_path / files[0], encoding="utf-8")))
    assert len(rows) == 1


def test_awake_logs_and_alerts(tmp_path, monkeypatch):
    pilot, elm, clock, taken, take = make(
        tmp_path, [13.9], values={"soc": 80, "pack_current": 40.0,
                                  "batt_temp_max": 50, "aux_12v": 13.9})
    monkeypatch.setattr(autopilot.snapshot, "take", take)
    assert pilot.step() == pilot.interval
    data = json.loads(pilot.state.json())
    assert data["status"] == "รถตื่น กำลังบันทึก"
    assert any("อุณหภูมิแบตสูงสุด" in a["text"] for a in data["alerts"])
    assert any(f.startswith("drive_") for f in os.listdir(tmp_path))


def test_weekly_checkup_when_parked(tmp_path, monkeypatch):
    calls = []
    pilot, elm, clock, taken, take = make(
        tmp_path, [13.9], run_checkup=lambda: calls.append(1))
    monkeypatch.setattr(autopilot.snapshot, "take", take)
    pilot.step()
    assert calls == []                  # awake for less than a minute
    clock.t += 61
    assert pilot.step() == 1.0
    assert calls == [1] and elm.closed and pilot.elm is None
    with open(tmp_path / "checkup_history.csv", "w", encoding="utf-8") as f:
        f.write("time,level\n2027-01-15T08:00:00,ok\n")
    clock.t += 10
    pilot.step()
    assert calls == [1]                 # last checkup is recent


def test_low_12v_shutdown(tmp_path, monkeypatch):
    done = []
    pilot, elm, clock, taken, take = make(
        tmp_path, [11.9], shutdown_below=12.2, shutdown_after=600,
        shutdown=lambda: done.append(1))
    monkeypatch.setattr(autopilot.snapshot, "take", take)
    pilot.step()
    assert done == []
    clock.t += 601
    pilot.step()
    assert done == [1]


def test_reconnects_after_adapter_failure(tmp_path, monkeypatch):
    pilot, elm, clock, taken, take = make(tmp_path, [None, 12.6])
    assert pilot.step() == pilot.retry
    assert pilot.elm is None and "ไม่ตอบ" in pilot.state.data["status"]
    assert pilot.step() == pilot.asleep_poll

    def broken():
        raise OSError("no device")
    pilot.open_elm = broken
    pilot.disconnect()
    assert pilot.step() == pilot.retry
    assert "เชื่อมต่อกล่อง OBD ไม่ได้" in pilot.state.data["status"]


def test_dashboard_files_routes(tmp_path):
    (tmp_path / "drive_20270101.csv").write_text("time,soc\n1,80\n",
                                                 encoding="utf-8")
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    (tmp_path / "survey_digest.txt").write_text("สรุป", encoding="utf-8")
    state = autopilot.State([], [])
    server = dashboard.make_server(state, "127.0.0.1", 0, str(tmp_path))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    try:
        listing = urllib.request.urlopen(base + "/files").read().decode()
        assert "drive_20270101.csv" in listing and "secret.txt" not in listing
        body = urllib.request.urlopen(
            base + "/files/drive_20270101.csv").read()
        assert body == b"time,soc\n1,80\n"
        page = urllib.request.urlopen(
            base + "/files/survey_digest.txt").read().decode()
        assert "คัดลอกทั้งหมด" in page and "สรุป" in page
        body = urllib.request.urlopen(base + "/files/survey_digest.txt?dl=1")
        assert "attachment" in body.headers["Content-Disposition"]
        assert body.read() == "สรุป".encode()
        for bad in ("/files/secret.txt", "/files/..%2F..%2Fetc%2Fpasswd"):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(base + bad)
        # the digest page makes the summary itself; copying marks it sent
        (tmp_path / "survey_results.csv").write_text(
            "time,header,did,length,raw\n"
            "2026-10-16T20:00:00,754,F1F0,1,8D\n", encoding="utf-8")
        page = urllib.request.urlopen(base + "/digest").read().decode()
        assert "F1F0 8D" in page and "/digest?sent=" in page
        urllib.request.urlopen(base + "/digest?sent=2026-10-16T20%3A00%3A00")
        page = urllib.request.urlopen(base + "/digest").read().decode()
        assert "ไม่มีผลใหม่" in page
        page = urllib.request.urlopen(base + "/digest?all=1").read().decode()
        assert "F1F0 8D" in page
        page = urllib.request.urlopen(
            base + "/digest?since=2026-10-16T19:00").read().decode()
        assert "F1F0 8D" in page and "ตั้งแต่ 2026-10-16T19:00" in page
        trends = urllib.request.urlopen(base + "/trends").read().decode()
        assert "ยังไม่มีประวัติ" in trends
        assert json.loads(urllib.request.urlopen(base + "/data").read())[
            "files"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_shows_vps_sync_state(tmp_path):
    state = autopilot.State([], [])
    server = dashboard.make_server(state, "127.0.0.1", 0, str(tmp_path))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/data" % port
    try:
        assert "vps" not in json.loads(urllib.request.urlopen(url).read())
        (tmp_path / ".vps_state").write_text("ok 2026-11-04 21:05:09\n")
        d = json.loads(urllib.request.urlopen(url).read())
        assert d["vps"] == {"state": "ok", "time": "2026-11-04 21:05:09"}
        (tmp_path / ".vps_state").write_text("junk")
        assert "vps" not in json.loads(urllib.request.urlopen(url).read())
    finally:
        server.shutdown()


def test_dashboard_script_keeps_its_helper_names():
    # a `var hm = ...` inside render() once hid the hm() box helper: every
    # poll threw and the page said it could not reach the program
    import re
    script = dashboard.PAGE.split("<script>", 1)[1]
    for helper in ("hm", "stat", "f", "esc", "tok", "mix"):
        assert not re.search(r"\bvar\b[^;]*?[\s,]%s\s*=" % helper, script), \
            helper


def test_watch_dids_go_into_the_drive_file(tmp_path, monkeypatch):
    import csv
    pilot, elm, clock, taken, take = make(tmp_path, [13.9])
    monkeypatch.setattr(autopilot.snapshot, "take", take)
    keys = autopilot.load_watch(str(tmp_path))
    assert ("780", "FD0C") in keys and len(keys) >= 30   # examples/ list
    for _ in range(len(keys) // autopilot.WATCH_PER_ROUND + 2):
        pilot.step()
        clock.t += 5
    pilot.close_drive_log()
    path = [f for f in os.listdir(tmp_path) if f.startswith("drive_")][0]
    with open(tmp_path / path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert "did_780_FD0C" in rows[0] and "did_700_F1BD" in rows[0]
    assert "012C" in [r["did_780_FD0C"] for r in rows]
    # each round reads only a few; a refused DID is not asked again
    assert elm.watch_reads <= len(rows) * autopilot.WATCH_PER_ROUND
    assert ("700", "F1A8") in pilot.watch.refused
    # the dashboard does not get the raw hex
    assert not any(k.startswith("did_") for k in
                   json.loads(pilot.state.json())["values"])


def test_watch_list_from_the_data_folder(tmp_path):
    (tmp_path / "watch_live.csv").write_text(
        "header,did\n7a7,fd10\n7A7,FD10\n700,F190\n")
    assert autopilot.load_watch(str(tmp_path), private={0xF190}) == [
        ("7A7", "FD10")]
    pilot = autopilot.Autopilot(lambda: None, [], [], str(tmp_path),
                                watch=[])
    assert pilot.watch is None


class ChargingElm(FakeElm):
    """12 V resting at 12.6 V while the BMS answers the pack current."""
    def __init__(self, current):
        super().__init__([12.6])
        self.current = current
        self.reads = 0

    def read_did(self, header, did):
        from deepal_s05.elm327 import NoData
        self.reads += 1
        if self.current is None:
            raise NoData("NO DATA")
        raw = int(self.current * 10) + 6015    # F229 is (raw - 6015) / 10
        return raw.to_bytes(2, "big")


def make_probe(tmp_path, monkeypatch, current, values):
    from deepal_s05 import pids
    clock = Clock()
    elm = ChargingElm(current)
    taken = []

    def take(e, signals, arrays, capacity, slow=None):
        taken.append(clock.t)
        return Snapshot(clock.t, dict(values))
    monkeypatch.setattr(autopilot.snapshot, "take", take)
    sig = next(s for s in pids.SIGNALS if s.key == "pack_current")
    pilot = autopilot.Autopilot(lambda: elm, [sig], [], str(tmp_path),
                                clock=clock, watch=[])
    return pilot, elm, clock, taken


def test_charging_with_the_car_off_is_logged(tmp_path, monkeypatch):
    pilot, elm, clock, taken = make_probe(
        tmp_path, monkeypatch, -150.0,
        {"soc": 40, "pack_current": -150.0, "aux_12v": 12.6})
    assert pilot.step() == pilot.interval        # probe said charging
    assert pilot.charging and len(taken) == 1
    assert any(f.startswith("drive_") for f in os.listdir(tmp_path))
    clock.t += 5
    pilot.step()
    assert len(taken) == 2 and elm.reads == 1    # no second probe needed


def test_charge_probe_stops_when_the_bms_is_asleep(tmp_path, monkeypatch):
    pilot, elm, clock, taken = make_probe(
        tmp_path, monkeypatch, None, {"soc": 40, "pack_current": 0.0})
    assert pilot.step() == pilot.asleep_poll
    assert elm.reads == 1 and taken == []
    clock.t += autopilot.CHARGE_PROBE_EVERY + 1
    pilot.step()
    assert elm.reads == 1                        # gave up until a wake


def test_charge_probe_only_soon_after_the_car_was_awake(tmp_path,
                                                       monkeypatch):
    pilot, elm, clock, taken = make_probe(
        tmp_path, monkeypatch, 0.5, {"soc": 40, "pack_current": 0.5})
    pilot.step()                                 # parked, not charging
    clock.t += autopilot.CHARGE_PROBE_EVERY + 1
    pilot.step()
    assert elm.reads == 2 and taken == []
    clock.t += autopilot.CHARGE_PROBE_WINDOW
    pilot.step()
    assert elm.reads == 2                        # too long since awake


def test_watch_list_resumes_where_it_stopped(tmp_path):
    # the Pi restarts at every stop: a long list must not start over
    keys = [("700", "%04X" % (0xF100 + n)) for n in range(100)]
    pos = str(tmp_path / ".watch_pos")
    w = autopilot.WatchReader(keys, pos_path=pos)
    elm = FakeElm([13.9])
    for _ in range(autopilot.WATCH_SAVE_EVERY):
        w.round(elm)
    again = autopilot.WatchReader(keys, pos_path=pos)
    assert again.pos == autopilot.WATCH_SAVE_EVERY * autopilot.WATCH_PER_ROUND
