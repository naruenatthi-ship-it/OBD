"""End-to-end test against ELM327-emulator with the deepal_s05 scenario."""

import json
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


def test_dashboard_data(url, tmp_path):
    import json
    import threading
    import urllib.request

    from deepal_s05 import config, dashboard, identity, vehicles

    signals, arrays = config.load(DEMO)
    vehicle = vehicles.deepal_s05()
    ir_file = tmp_path / "cell_ir.json"
    ir_file.write_text(json.dumps({"pack_mohm": 61, "cells": [0.4, 0.8]}))
    with Elm327(url) as elm:
        elm.initialize()
        poller = dashboard.Poller(elm, pids.SIGNALS[:3] + signals, arrays,
                                  56.1, interval=0.2,
                                  info=identity.pack_info(vehicle, 56.1),
                                  vehicle=vehicle)
        server = dashboard.make_server(poller, "127.0.0.1", 0,
                                       ir_path=str(ir_file))
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
            ir = json.load(urllib.request.urlopen(
                "http://127.0.0.1:%d/ir" % port))
        finally:
            poller.stop.set()
            poller.join(5)
            server.shutdown()
            server.server_close()
    assert data["error"] is None
    assert len(data["arrays"]["cells"]) == 108
    assert data["values"]["obc_temp"] == 50
    assert data["custom"] == ["obc_temp", "insulation_kohm"]
    assert data["values"]["insulation_kohm"] == 5000
    assert data["values"]["insulation_ohm_per_v"] == pytest.approx(
        5000e3 / 358.6)
    assert data["info"]["bms"]["part_number"] == "DEMO-BMS-01"
    assert data["info"]["capacity_kwh"] == 56.1
    assert ir["cells"] == [0.4, 0.8]
    assert "Deepal S05" in page


def test_health_saves_cell_ir(url, tmp_path, capsys):
    history = tmp_path / "health.csv"
    assert main(["--port", url, "--signals", DEMO, "health", "--samples",
                 "1", "--ir", "3", "--cell-ir", "--min-range", "5",
                 "--history", str(history)]) == 0
    saved = json.loads((tmp_path / "cell_ir.json").read_text())
    assert len(saved["cells"]) == 108
    assert "แท็บ IR" in capsys.readouterr().out


def test_check_reads_bms_and_known_pack(url, tmp_path, capsys,
                                        monkeypatch):
    from deepal_s05 import vehicles
    out = tmp_path / "check.json"
    assert main(["--port", url, "check", "--save", str(out)]) == 0
    text = capsys.readouterr().out
    assert "BMS 7A1: รหัสชิ้นส่วน: DEMO-BMS-01" in text
    assert "ยังไม่รู้จักรหัส BMS นี้" in text
    assert json.loads(out.read_text())["bms"]["sw_version"] == "V1.00"

    monkeypatch.setattr(vehicles, "DEEPAL_PACKS", [
        {"part": "DEMO-BMS", "capacity": 68.82, "label": "ทดสอบ"}])
    assert main(["--port", url, "check", "--save", str(out)]) == 0
    assert "รู้จักแบตนี้: ทดสอบ (68.82 kWh)" in capsys.readouterr().out
    assert json.loads(out.read_text())["capacity_kwh"] == 68.82
    # an explicit --capacity wins over the detected pack
    assert main(["--port", url, "--capacity", "56.1", "check", "--save",
                 str(out)]) == 0
    assert json.loads(out.read_text())["capacity_kwh"] == 56.1


def test_cli_dtc(url, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_checkup_twice_compares(url, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = ["--port", url, "checkup", "--headers", "761,7A1",
            "--samples", "1"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "ผลรวม: ⚠️ ควรเฝ้าดู" in out and "P0562-16" in out
    assert main(["--port", url, "checkup", "--headers", "7A1",
                 "--samples", "1"]) == 0
    out = capsys.readouterr().out
    assert "เทียบกับการตรวจครั้งก่อน" in out
    rows = (tmp_path / "checkup_history.csv").read_text(
        encoding="utf-8").splitlines()
    assert len(rows) == 3
    assert main(["trends"]) == 0
    assert (tmp_path / "trends.html").exists()


def test_live_alert_from_signals_file(url, tmp_path, capsys):
    path = tmp_path / "s.json"
    data = json.load(open(DEMO, encoding="utf-8"))
    data["alerts"] = [{"key": "obc_temp", "label": "อุณหภูมิ OBC",
                       "unit": "C", "warn_above": 45}]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert main(["--port", url, "--signals", str(path), "live",
                 "--count", "1"]) == 0
    assert "⚠️ อุณหภูมิ OBC 50 C (เกณฑ์ > 45)" in capsys.readouterr().out


def test_drivetest_modes(url, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["--port", url, "--signals", DEMO, "drivetest",
                 "--duration", "2", "--name", "t"]) == 0
    assert main(["--port", url, "drivetest", "--mode", "route",
                 "--duration", "9", "--interval", "0", "--km", "1",
                 "--name", "r"]) == 0
    assert main(["--port", url, "drivetest", "--duration", "2",
                 "--name", "t"]) == 0
    out = capsys.readouterr().out
    assert "ผลทดสอบ t (accel)" in out and "Wh/km" in out
    assert "เทียบกับครั้งก่อน" in out
    header = (tmp_path / "drivetest_history.csv").read_text(
        encoding="utf-8").splitlines()[0]
    assert "wh_per_km" in header and "obc_temp_rise" in header


def test_reverse_workflow(url, tmp_path, capsys):
    scan = tmp_path / "scan.csv"
    assert main(["--port", url, "discover", "--header", "7A1,761",
                 "--ranges", "F2C0-F2C1,F250-F251", "--save",
                 str(scan)]) == 0
    text = scan.read_text(encoding="utf-8")
    assert "761,F2C1" in text and "7A1,F250" in text
    assert main(["scandiff", str(scan), str(scan)]) == 0
    assert "ไม่มีไบต์ไหนเปลี่ยน" in capsys.readouterr().out
    rec = tmp_path / "rec.csv"
    assert main(["--port", url, "record", "--dids-from", str(scan),
                 "--interval", "0", "--duration", "4",
                 "--csv", str(rec)]) == 0
    assert "did_761_F2C1" in rec.read_text(encoding="utf-8")
    assert main(["correlate", str(rec)]) == 0
    assert "ไบต์ที่เปลี่ยนระหว่างบันทึก" in capsys.readouterr().out


def test_discover_leaves_out_vin(url, tmp_path, capsys):
    out = tmp_path / "scan.csv"
    assert main(["--port", url, "discover", "--header", "7A1", "--ranges",
                 "F187-F190", "--save", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "F187" in text and "F190" not in text
    assert "เลข VIN" in capsys.readouterr().out
    assert main(["--port", url, "discover", "--header", "7A1", "--ranges",
                 "F190-F190", "--keep-ids", "--save", str(out)]) == 0
    assert "F190" in out.read_text(encoding="utf-8")
