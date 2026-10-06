import csv

import pytest

from deepal_s05 import config, reverse
from deepal_s05.cli import parse_ranges


def test_parse_ranges():
    assert parse_ranges("0100-01FF, F100-F2FF,F2A0") == [
        (0x0100, 0x01FF), (0xF100, 0xF2FF), (0xF2A0, 0xF2A0)]


def write_scan(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["header", "did", "length", "raw", "cell_like"])
        for header, did, raw in rows:
            w.writerow([header, did, len(raw) // 2, raw, ""])


def test_diff_scans(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    write_scan(a, [("7A1", "F2C0", "4500AA"), ("7A1", "F2C1", "01"),
                   ("761", "0101", "1122")])
    write_scan(b, [("7A1", "F2C0", "5A00AB"), ("7A1", "F2C1", "01"),
                   ("761", "0101", "112233"), ("7E0", "F190", "00")])
    d = reverse.diff_scans(reverse.read_scan(a), reverse.read_scan(b))
    assert d["changed"] == {("7A1", "F2C0"): [(0, 0x45, 0x5A),
                                             (2, 0xAA, 0xAB)]}
    assert d["length"] == [("761", "0101")]
    assert d["only_b"] == [("7E0", "F190")] and d["only_a"] == []
    assert reverse.temp_guess(0x45, 0x5A) == \
        "ถ้าเป็นอุณหภูมิ (A-40): 29 -> 50 °C"
    assert reverse.temp_guess(0x05, 0xF0) == ""


def recorded_rows():
    rows = []
    for k in range(12):
        temp = 25 + k * 2                       # known reference
        volts = 330.0 + (k % 4) * 3 + k * 0.5
        noise = (k * 37) % 251
        data = bytes([noise, 0x10, temp + 40]) + \
            int(volts * 10).to_bytes(2, "big")
        rows.append({"batt_temp_max": float(temp), "pack_voltage": volts,
                     "did_761_F2C1": data})
    return rows


def test_correlate_finds_formula_that_evaluates():
    rows = recorded_rows()
    best = reverse.correlate(rows, "batt_temp_max", ["did_761_F2C1"])[0]
    assert (best["name"], best["pos"]) == ("u8", 2)
    assert best["formula"] == "C-40" and best["rmse"] < 1e-6
    v = reverse.correlate(rows, "pack_voltage", ["did_761_F2C1"])[0]
    assert v["formula"] == "(D*256+E)/10"
    tree, _ = config.compile_formula(v["formula"])
    assert config.evaluate(tree, rows[3]["did_761_F2C1"], 0) == \
        pytest.approx(340.5)


def test_moving_bytes_and_read_record(tmp_path):
    path = tmp_path / "r.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "note", "soc", "did_7A1_F2C0"])
        w.writerow(["t1", "", "50", "450010"])
        w.writerow(["t2", "", "51", "460010"])
        w.writerow(["t3", "", "", ""])
    rows, keys = reverse.read_record(str(path))
    assert keys == ["did_7A1_F2C0"]
    assert rows[0]["soc"] == 50 and rows[2]["did_7A1_F2C0"] is None
    assert reverse.moving_bytes(rows, keys) == [
        ("did_7A1_F2C0", 0, 0x45, 0x46, 2)]
