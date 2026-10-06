import json

import pytest

from deepal_s05 import config


def test_formula_evaluation():
    tree, used = config.compile_formula("((A*256+B)-6015)/10")
    assert used == 2
    assert config.evaluate(tree, bytes([0x17, 0x73]), 56.1) == \
        pytest.approx(-1.2)
    tree, _ = config.compile_formula("CAP-(A*256+B)/1000")
    assert config.evaluate(tree, bytes([0x0F, 0xA0]), 56.1) == \
        pytest.approx(52.1)
    tree, _ = config.compile_formula("(A>>4)&0x0F")
    assert config.evaluate(tree, bytes([0xAB]), 0) == 0x0A


@pytest.mark.parametrize("bad", ["__import__('os')", "A.real", "open",
                                 "AB+1", "'x'", "A if B else C", "f(A)"])
def test_unsafe_formulas_rejected(bad):
    with pytest.raises(config.ConfigError):
        config.compile_formula(bad)


def write(tmp_path, data):
    path = tmp_path / "signals.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_load_signals_and_arrays(tmp_path):
    path = write(tmp_path, {
        "signals": [{"key": "obc_temp", "header": "761", "did": "F2C1",
                     "formula": "A-40", "unit": "C"}],
        "arrays": [{"key": "cells", "header": "7A1", "did": "F2A0",
                    "count": 3, "bytes": 2, "scale": 0.001}],
    })
    signals, arrays = config.load(path)
    assert signals[0].request == "22F2C1"
    assert signals[0].value(bytes([90]), 56.1) == 50
    assert signals[0].value(b"", 56.1) is None
    assert arrays[0].decode(bytes.fromhex("0D020D030D01")) == \
        pytest.approx([3.330, 3.331, 3.329])


def test_array_decode_stops_at_end_of_data(tmp_path):
    path = write(tmp_path, {"arrays": [{
        "key": "temps", "header": "7A1", "did": "F2A1", "count": 4,
        "bytes": 1, "add": -40}]})
    _, arrays = config.load(path)
    assert arrays[0].decode(bytes([70, 71])) == [30, 31]


@pytest.mark.parametrize("data", [
    {"signals": [{"key": "soc", "header": "7A1", "did": "F2C1",
                  "formula": "A"}]},
    {"signals": [{"key": "x", "header": "7A1", "did": "F2C1",
                  "formula": "A"},
                 {"key": "x", "header": "7A1", "did": "F2C2",
                  "formula": "A"}]},
    {"signals": [{"key": "x", "header": "18DA", "did": "F2C1",
                  "formula": "A"}]},
    {"signals": [{"key": "x", "header": "7A1", "did": "F2C1"}]},
])
def test_invalid_files(tmp_path, data):
    with pytest.raises(config.ConfigError):
        config.load(write(tmp_path, data))
