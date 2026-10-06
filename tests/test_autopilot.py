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


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(tmp_path, volts, values=None, **kw):
    clock = Clock()
    elm = FakeElm(volts)
    taken = []

    def take(e, signals, arrays, capacity):
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
        for bad in ("/files/secret.txt", "/files/..%2F..%2Fetc%2Fpasswd"):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(base + bad)
        trends = urllib.request.urlopen(base + "/trends").read().decode()
        assert "ยังไม่มีประวัติ" in trends
        assert json.loads(urllib.request.urlopen(base + "/data").read())[
            "files"] is True
    finally:
        server.shutdown()
        server.server_close()
