"""End-to-end test against ELM327-emulator with the deepal_s05 scenario."""

import os
import socket
import sys
import time

import pytest

pytest.importorskip("elm")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emulator"))

from run_emulator import start  # noqa: E402

from deepal_s05 import pids  # noqa: E402
from deepal_s05.cli import main  # noqa: E402
from deepal_s05.snapshot import read_signals  # noqa: E402
from deepal_s05.elm327 import Elm327, NegativeResponse  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def url():
    port = free_port()
    with start(port):
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection(("localhost", port), 0.5).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        yield "socket://localhost:%d" % port


def test_read_all_signals(url):
    with Elm327(url) as elm:
        elm.initialize()
        results = read_signals(elm, pids.SIGNALS, 56.1)
    assert all(err is None for _, _, err in results.values())
    assert results["pack_voltage"][1] == pytest.approx(358.6)
    assert results["soh"][1] == pytest.approx(98.7)


def test_negative_response(url):
    with Elm327(url) as elm:
        elm.initialize()
        with pytest.raises(NegativeResponse) as e:
            elm.read_did("7A1", 0xF201)
    assert e.value.nrc == 0x31


def test_cli_check(url, tmp_path, capsys):
    out = tmp_path / "check.json"
    assert main(["--port", url, "check", "--save", str(out)]) == 0
    assert "14 จาก 14" in capsys.readouterr().out
    assert out.exists()


def test_cli_ecus(url, capsys):
    assert main(["--port", url, "ecus", "--start", "760", "--end",
                 "7A2"]) == 0
    out = capsys.readouterr().out
    assert "761 -> 769" in out and "DEMO-OBC-01" in out
    assert "7A1 -> 7A9" in out and "พบ 2 ECU" in out


def test_cli_discover_finds_cells(url, tmp_path, capsys):
    out_csv = tmp_path / "dids.csv"
    assert main(["--port", url, "discover", "--start", "F29F", "--end",
                 "F2A1", "--extended", "--save", str(out_csv)]) == 0
    out = capsys.readouterr().out
    assert "22F2A0  216" in out
    assert "อาจเป็นแรงดันรายเซลล์: 108 ค่า" in out
    assert "F2A0" in out_csv.read_text(encoding="utf-8")


DEMO = os.path.join(os.path.dirname(__file__), "..", "examples",
                    "signals_demo.json")


def test_check_with_custom_signals(url, capsys):
    assert main(["--port", url, "--signals", DEMO, "check"]) == 0
    out = capsys.readouterr().out
    assert "อุณหภูมิ OBC" in out and " 50 C" in out
    assert "108 ค่า" in out and "(#47)" in out and "(#83)" in out
    assert "แบต 12V" in out


def test_charge_and_health(url, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["--port", url, "--signals", DEMO, "charge", "--interval",
                 "0", "--duration", "0.001", "--csv", "c.csv",
                 "--label", "16A"]) == 0
    assert "cells_108" in (tmp_path / "c.csv").read_text(encoding="utf-8")
    assert main(["--port", url, "health", "--samples", "1"]) == 0
    out = capsys.readouterr().out
    assert "SOC ช่วงกลาง" in out
    assert (tmp_path / "health_history.csv").exists()


def test_dashboard_data(url):
    import json
    import threading
    import urllib.request

    from deepal_s05 import config, dashboard

    signals, arrays = config.load(DEMO)
    with Elm327(url) as elm:
        elm.initialize()
        poller = dashboard.Poller(elm, pids.SIGNALS[:3] + signals, arrays,
                                  56.1, interval=0.2)
        server = dashboard.make_server(poller, "127.0.0.1", 0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        poller.start()
        try:
            deadline = time.monotonic() + 20
            while True:
                data = json.load(urllib.request.urlopen(
                    "http://127.0.0.1:%d/data" % port))
                if data["time"] or time.monotonic() > deadline:
                    break
                time.sleep(0.2)
            page = urllib.request.urlopen(
                "http://127.0.0.1:%d/" % port).read().decode()
        finally:
            poller.stop.set()
            poller.join(5)
            server.shutdown()
            server.server_close()
    assert data["error"] is None
    assert len(data["arrays"]["cells"]) == 108
    assert data["values"]["obc_temp"] == 50
    assert data["custom"] == ["obc_temp"]
    assert "Deepal S05" in page


def test_cli_dtc(url, tmp_path, capsys):
    out_csv = tmp_path / "dtc.csv"
    assert main(["--port", url, "dtc", "--headers", "761,7A1,7B0",
                 "--save", str(out_csv)]) == 0
    out = capsys.readouterr().out
    assert "P0562-16" in out and "แรงดันระบบ 12V ต่ำ" in out
    assert "P0A80-00" in out and "ECU ที่ตอบ 2 กล่อง" in out
    assert "P0562-16" in out_csv.read_text(encoding="utf-8")


def test_cli_aux12v(url, tmp_path, capsys):
    out_csv = tmp_path / "a.csv"
    assert main(["--port", url, "aux12v", "--interval", "0", "--count", "3",
                 "--csv", str(out_csv)]) == 0
    assert "สรุป" in capsys.readouterr().out
    rows = out_csv.read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("time,elapsed_s,label,aux_12v")
    assert len(rows) == 4
