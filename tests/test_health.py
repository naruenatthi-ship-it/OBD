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
