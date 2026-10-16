import json
import math
import threading
import urllib.request

from deepal_s05 import autopilot, dashboard, visual


def write(path, text):
    path.write_text(text, encoding="utf-8")


def topology_folder(tmp_path):
    write(tmp_path / "ecus_20261016-1000.csv",
          "header,response,part_number,supplier,system_name,hw_number,"
          "hw_version,sw_version\n"
          "7A1,7A9,2101210-EM01,,,,,\n7A4,7AC,2139010-DQ07,,,,,\n"
          "700,708,,,,,,\n731,739,,,,,,\n")
    write(tmp_path / "dtc_20261016-1001.csv",
          "header,code,status,status_text,description\n"
          "7A1,P1B27-05,0x89,x,y\n7A4,P1A89-00,0x08,x,y\n")
    return tmp_path


def test_topology_states(tmp_path):
    topo = visual.load_topology(str(topology_folder(tmp_path)))
    state = {e["header"]: e["state"] for e in topo["ecus"]}
    assert state == {"700": "ok", "731": "ok", "7A1": "bad", "7A4": "warn"}
    page = visual.topology_html(topo)
    assert "แบตเตอรี่ไฟแรงสูง (BMS)" in page and "P1B27-05" in page
    assert "2139010-DQ07" in page


def test_topology_prefers_newer_history_and_handles_nothing(tmp_path):
    import os
    import time
    topology_folder(tmp_path)
    hist = tmp_path / "dtc_history.json"
    write(hist, json.dumps([{"time": "2026-10-17", "answered": ["7A1", "7F1"],
                             "scanned": ["7A1", "7F1"], "codes": []}]))
    later = time.time() + 60
    os.utime(hist, (later, later))
    state = {e["header"]: e["state"] for e in
             visual.load_topology(str(tmp_path))["ecus"]}
    assert state["7A1"] == "ok" and state["7F1"] == "ok"
    assert state["700"] == "none"  # not in that DTC scan
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "ยังไม่มีข้อมูล" in visual.topology_html(
        visual.load_topology(str(empty)))


def test_drive_samples_from_raw_and_decoded_columns():
    rows = [{"motor_rpm": "8090", "motor_torque": "100", "pack_voltage": "400",
             "pack_current": "250"},
            {"did_7A7_FD07": "%04X" % (20000 + 4045),
             "did_7A7_FD08": "%04X" % (4000 + 500), "pack_voltage": "399",
             "pack_current": "-50"},
            {"motor_rpm": "100"}]
    assert visual.drive_samples(rows) == [(8090, 100, 400, 250),
                                          (4045, 50, 399, -50)]


def test_efficiency_cells():
    rpm, tq = 6000, 150
    pm = visual.mech_kw(rpm, tq)
    samples = [(rpm, tq, 400, pm / 0.9 * 1000 / 400),     # driving, 90 %
               (rpm, -50, 400, visual.mech_kw(rpm, -50) * 0.8 * 1000 / 400),
               (rpm, tq, 400, pm / 0.1 * 1000 / 400),     # out of range
               (100, 1, 400, 2)]                          # too small
    cells = visual.efficiency_cells(samples)
    assert math.isclose(cells[(6, 6)][0], 0.9)
    assert math.isclose(cells[(6, -2)][0], 0.8)
    assert len(cells) == 2


def test_dyno_page(tmp_path):
    lines = ["time,pack_voltage,pack_current,motor_rpm,motor_torque"]
    for k in range(40):
        rpm = 300 * k
        tq = 250 if rpm < 6000 else 250 * 6000 / max(rpm, 1)
        pm = visual.mech_kw(rpm, tq)
        lines.append("t,400,%.2f,%d,%.1f" % (pm / 0.9 * 1000 / 400, rpm, tq))
    write(tmp_path / "rec_drive_1.csv", "\n".join(lines) + "\n")
    page = visual.dyno_html(visual.drive_files(str(tmp_path)))
    assert "157 kW" in page  # 250 Nm at 6000 rpm
    assert "250 Nm" in page and "<rect" in page
    assert "ยังไม่มีไฟล์" in visual.dyno_html([])


def test_charge_curve_page(tmp_path):
    lines = ["time,soc,pack_voltage,pack_current,batt_temp_max,cells_max,"
             "cells_min,cells_delta_mv,cells_max_no"]
    for soc in range(50, 101, 5):
        lines.append("t,%d,400,-16,30,3.4,3.39,10,%d" % (
            soc, 19 if soc >= 90 else 5))
    write(tmp_path / "charge_1.csv", "\n".join(lines) + "\n")
    series, top = visual.charge_series(
        visual.read_csv(str(tmp_path / "charge_1.csv")))
    assert series["power"][0] == (50, 6.4)  # charging shown as positive
    assert top == {"19": 3}
    page = visual.charge_curve_html(visual.charge_files(str(tmp_path)))
    assert "#19" in page and "<path" in page


def test_dashboard_visual_routes(tmp_path):
    topology_folder(tmp_path)
    write(tmp_path / "freeze_1.json", json.dumps({"time": "t", "frames": [
        {"header": "7A1", "code": "P1B27-05", "error": "NRC 0x31"}]}))
    write(tmp_path / "page.html", "<p>hi</p>")
    server = dashboard.make_server(autopilot.State([], []), "127.0.0.1", 0,
                                   str(tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        def get(path):
            return urllib.request.urlopen(base + path).read().decode()
        assert "P1B27-05" in get("/topology")
        assert "ยังไม่มีไฟล์" in get("/dyno")
        assert "ยังไม่มีไฟล์" in get("/charge")
        assert "ไม่มีภาพนิ่ง" in get("/freeze")
        r = urllib.request.urlopen(base + "/files/page.html")
        assert r.headers["Content-Type"].startswith("text/html")
    finally:
        server.shutdown()
        server.server_close()
