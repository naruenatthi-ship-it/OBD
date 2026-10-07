"""BMS identification, pack matching and the insulation value."""

import pytest

from deepal_s05 import alerts, identity, pids, vehicles
from deepal_s05.elm327 import NegativeResponse, NoData


class FakeElm:
    def __init__(self, answers):
        self.answers = answers  # {(header, did): bytes | exception}

    def read_did(self, header, did):
        a = self.answers.get((header, did), NoData("NO DATA"))
        if isinstance(a, Exception):
            raise a
        return a


def test_read_identity_and_fallback_header():
    crv = vehicles.Vehicle(
        key="x", name="x", protocol="7", signals=[], live_keys=[],
        capacity=1.1, default_header="18DA01F1",
        bms_headers=("18DA01F1", "18DA16F1"))
    elm = FakeElm({("18DA16F1", 0xF187): b"1B000-ABC\x00",
                   ("18DA16F1", 0xF18A): NegativeResponse(0x22, 0x31)})
    header, info = identity.bms_identity(elm, crv)
    assert header == "18DA16F1"
    assert info["part_number"] == "1B000-ABC"
    assert info["supplier"] == ""
    assert identity.describe(info) == "รหัสชิ้นส่วน: 1B000-ABC"
    assert identity.bms_identity(FakeElm({}), crv) == (None, {})


def test_as_text_binary():
    assert identity.as_text(b"\x01\x02") == "01 02"


def test_match_pack():
    known = [{"part": "ABC 12", "capacity": 56.1, "label": "small"},
             {"part": "ABC", "capacity": 68.82, "label": "any ABC"}]
    assert identity.match_pack(known, {"part_number": "abc12-77"})["capacity"] \
        == 56.1
    assert identity.match_pack(known, {"part_number": "ABC99"})["label"] == \
        "any ABC"
    assert identity.match_pack(known, {"part_number": "XYZ"}) is None
    assert identity.match_pack(known, {}) is None


def test_insulation_per_volt_and_rule():
    d = pids.derived({"pack_voltage": 400.0, "insulation_kohm": 100})
    assert d["insulation_ohm_per_v"] == pytest.approx(250)
    assert "insulation_ohm_per_v" not in pids.derived(
        {"insulation_kohm": 100})
    found = alerts.evaluate({"insulation_ohm_per_v": 250},
                            alerts.DEFAULT_RULES)
    assert [(a.key, a.level) for a in found] == [
        ("insulation_ohm_per_v", alerts.WARN)]
    found = alerts.evaluate({"insulation_ohm_per_v": 90},
                            alerts.DEFAULT_RULES)
    assert found[0].level == alerts.CRIT
