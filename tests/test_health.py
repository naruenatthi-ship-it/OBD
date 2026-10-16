import csv

import pytest

from deepal_s05 import analysis, report
from deepal_s05.snapshot import CsvLog, Snapshot, array_summary


def test_estimate_ir():
    currents = [0, 20, 40, 60, 80, 100]
    voltages = [360 - 0.08 * i for i in currents]  # 80 mΩ
    ir = analysis.estimate_ir(currents, voltages)
    assert ir["r_mohm"] == pytest.approx(80)
    assert ir["r2"] == pytest.approx(1)
    # the sign convention of the current does not matter
    neg = analysis.estimate_ir([-i for i in currents], voltages)
    assert neg["r_mohm"] == pytest.approx(80)


def test_estimate_ir_needs_current_change():
    ir = analysis.estimate_ir([1, 2, 3], [360, 360, 360])
    assert ir["r_mohm"] is None and ir["i_range"] == 2


def history(points, soc=100):
    return [(t * 60, soc, d) for t, d in points]


def test_balance_status_states():
    assert analysis.balance_status([])[0] == "no_data"
    assert analysis.balance_status([(0, 80, 5)])[0] == "not_full"
    assert analysis.balance_status(history([(0, 30), (10, 25)]))[0] == \
        "collecting"
    state, text = analysis.balance_status(history(
        [(0, 30), (15, 24), (30, 18), (45, 12)]))
    assert state == "improving" and "12.0 mV" in text
    assert analysis.balance_status(history(
        [(0, 12), (15, 11.8), (30, 11.6), (45, 11.5)]))[0] == "stable"
    assert analysis.balance_status(history(
        [(0, 10), (30, 14), (45, 16)]))[0] == "rising"


def test_array_summary():
    out = array_summary("cells", [3.330, 3.325, 3.334])
    assert out["cells_min_no"] == 2 and out["cells_max_no"] == 3
    assert out["cells_delta_mv"] == pytest.approx(9)


def test_csv_log_and_report(tmp_path):
    path = tmp_path / "charge.csv"
    with CsvLog(str(path), "32A") as log:
        for i in range(5):
            log.write(Snapshot(1000.0 + i * 60, {
                "soc": 90 + i, "pack_power_kw": -6.2, "batt_temp_max": 30 + i,
                "cell_delta_mv": 5.0, "aux_12v": 14.1, "obc_temp": 50 + i},
                {"cells": [3.33, 3.331]}))
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert len(rows) == 5 and rows[-1]["elapsed_s"] == "240.0"
    assert rows[0]["cells_2"] == "3.331" and rows[0]["label"] == "32A"

    out = tmp_path / "report.html"
    report.write_report([str(path)], str(out), 56.1)
    page = out.read_text(encoding="utf-8")
    assert "<h2>SOC</h2>" in page and "<h2>obc_temp</h2>" in page
    assert "cells_1" not in page and "_min" not in page
    assert "90 → 94" in page


def test_group_cache_reads_cells_every_period():
    from deepal_s05 import pids, snapshot, vehicles

    class Elm:
        def __init__(self):
            self.reads = []

        def read_did(self, header, did):
            self.reads.append(did)
            return bytes([0x0D, 0x05]) if did != 0xF22F else bytes([50])

        def battery_voltage(self):
            return 13.8

    now = [0.0]
    cache = snapshot.GroupCache(30, clock=lambda: now[0])
    elm = Elm()
    v = vehicles.deepal_s05()
    sigs = [pids.SIGNALS_BY_KEY["soc"]] + pids.CELL_SIGNALS
    snap = snapshot.take(elm, sigs, v.groups, slow=cache)
    assert len(snap.arrays["cells"]) == 120 and len(elm.reads) == 121
    now[0] = 10
    snap = snapshot.take(elm, sigs, v.groups, slow=cache)
    assert len(elm.reads) == 122  # only SOC again
    assert len(snap.arrays["cells"]) == 120  # cells from the cache
    now[0] = 31
    snapshot.take(elm, sigs, v.groups, slow=cache)
    assert len(elm.reads) == 243


def test_csvlog_adds_new_columns(tmp_path):
    import csv
    from deepal_s05 import snapshot
    path = tmp_path / "drive.csv"
    with snapshot.CsvLog(str(path), "drive") as log:
        log.write(snapshot.Snapshot(1.0, {"soc": 80}))
    with snapshot.CsvLog(str(path), "drive") as log:  # next program run
        log.write(snapshot.Snapshot(2.0, {"soc": 79, "motor_temp": 50}))
        log.write(snapshot.Snapshot(3.0, {"soc": 78}))
    rows = list(csv.DictReader(open(path)))
    assert [r["soc"] for r in rows] == ["80", "79", "78"]
    assert [r["motor_temp"] for r in rows] == ["", "50", ""]


def test_trip_summaries():
    from deepal_s05 import analysis, pids
    rows = []
    # 10 minutes at 80 km/h drawing 10 kW, sampled every 6 s, then a stop
    # of 20 minutes and a second 3-minute trip with 2 kW of regen
    for i in range(101):
        rows.append({"time": "2026-10-16T12:%02d:%02d" % (i * 6 // 60,
                                                         i * 6 % 60),
                     "motor_rpm": str(80 * pids.RPM_PER_KMH),
                     "pack_power_kw": "10", "soc": str(90 - i // 50),
                     "motor_temp": str(50 + i // 10)})
    for i in range(31):
        rows.append({"time": "2026-10-16T12:%02d:%02d" % (30 + i * 6 // 60,
                                                         i * 6 % 60),
                     "speed_kmh": "36", "pack_power_kw": "-2"})
    a, b = analysis.trip_summaries(rows)
    assert abs(a["km"] - 80 * 10 / 60) < 0.01
    assert abs(a["kwh"] - 10 * 10 / 60) < 0.01
    assert abs(a["kwh_100km"] - 12.5) < 0.01
    assert a["motor_max"] == 60 and a["soc_start"] == 90
    assert abs(b["km"] - 36 * 3 / 60) < 0.01 and b["regen_kwh"] > 0.09
    assert pids.derived({"motor_rpm": -809})["speed_kmh"] == 10


def test_eco_table_ac_split():
    from deepal_s05 import analysis

    def rec(power_at):  # 60 s parked, ramp, 40 s at 80, ramp, 40 s at 100
        rows, t = [], 0
        for speed, secs in ((0, 60), (40, 10), (80, 40), (90, 5), (100, 40)):
            for _ in range(secs // 2):
                rows.append({"time": "2026-10-16T12:%02d:%02d" % (t // 60,
                                                                 t % 60),
                             "speed_kmh": str(speed),
                             "pack_power_kw": str(power_at(speed))})
                t += 2
        return rows
    table = analysis.eco_table({
        "off": rec(lambda v: 0.3 + v * 0.1),
        "on": rec(lambda v: 1.3 + v * 0.1)})
    off, on = table["off"], table["on"]
    assert set(off) == {0, 80, 100}  # the ramps are not counted
    assert abs(on[0]["kw"] - off[0]["kw"] - 1.0) < 0.01
    assert abs(off[80]["kwh_100"] - 8.3 / 80 * 100) < 0.1
    assert abs(on[100]["kwh_100"] - off[100]["kwh_100"] - 1.0) < 0.1
