import pytest

from deepal_s05 import analysis, dtc


@pytest.mark.parametrize("raw,code", [
    ((0x05, 0x62, 0x16), "P0562-16"),
    ((0x0A, 0x80, 0x00), "P0A80-00"),
    ((0xC1, 0x00, 0x87), "U0100-87"),
    ((0x91, 0x23, 0x01), "B1123-01"),
    ((0x5F, 0xFF, 0xFF), "C1FFF-FF"),
])
def test_format_dtc(raw, code):
    assert dtc.format_dtc(*raw) == code


def test_generic_and_manufacturer_codes():
    assert dtc.describe("P0562-16") == "แรงดันระบบ 12V ต่ำ"
    assert dtc.describe("P1A23-00") == dtc.MANUFACTURER_SPECIFIC
    assert dtc.describe("U2100-00") == dtc.MANUFACTURER_SPECIFIC
    assert dtc.describe("B2001-00") == dtc.MANUFACTURER_SPECIFIC
    assert dtc.describe("P3400-00") == ""  # generic, not in the table
    assert dtc.describe("P3000-00") == dtc.MANUFACTURER_SPECIFIC


def test_parse_response():
    msg = bytes.fromhex("5902FF" "0A800008" "C1110020" "05621650")
    records = dtc.parse_response(msg)
    assert [r["code"] for r in records] == ["P0A80-00", "U0111-00",
                                            "P0562-16"]
    assert records[0]["status_text"] == "ยืนยันแล้ว"
    assert records[1]["active"] and "เคยเกิด" in records[1]["status_text"]
    assert not records[2]["active"]  # 0x50: tests not completed only


def test_parse_response_rejects_other_messages():
    with pytest.raises(ValueError):
        dtc.parse_response(bytes.fromhex("620F2F48"))


def test_aux12v_state():
    assert "DC-DC" in analysis.aux12v_state(14.1)
    assert analysis.aux12v_state(12.7) == "เต็ม"
    assert "ต่ำ" in analysis.aux12v_state(11.9)


def test_aux12v_summary():
    readings = [(0, 12.6), (1800, 12.55), (3600, 12.5), (3700, 13.9),
                (4000, 14.0), (5400, 12.7), (7200, 12.45)]
    s = analysis.aux12v_summary(readings)
    assert s["wakes"] == 1
    assert s["min"] == 12.45 and s["max"] == 14.0
    assert s["trend_v_per_h"] < 0
    assert analysis.aux12v_summary([]) is None


@pytest.mark.parametrize("code", ["P1B27-05", "P0A80-00", "U0100-87",
                                  "C1234-1F", "B2AAA-00"])
def test_code_bytes_round_trip(code):
    assert dtc.format_dtc(*dtc.code_bytes(code)) == code


def test_parse_snapshot():
    msg = bytes.fromhex("59041B270589" "01" "02" "F229" "1773" "DD01"
                        "0001A2" "02" "01" "F229" "1700")
    snap = dtc.parse_snapshot(msg, {0xF229: 2, 0xDD01: 3}.get)
    assert snap["code"] == "P1B27-05" and snap["status"] == 0x89
    assert snap["records"][0] == {"record": 1, "values": [
        (0xF229, bytes.fromhex("1773")), (0xDD01, bytes.fromhex("0001A2"))]}
    assert snap["records"][1]["values"] == [(0xF229, b"\x17\x00")]
    assert snap["unparsed"] == b""


def test_parse_snapshot_unknown_length_and_empty():
    msg = bytes.fromhex("59041B270589" "01" "02" "F229" "1773" "ABCD" "0102")
    snap = dtc.parse_snapshot(msg, {0xF229: 2}.get)
    assert snap["records"][0]["values"] == [(0xF229, b"\x17\x73")]
    assert snap["unparsed"] == bytes.fromhex("ABCD0102")
    empty = dtc.parse_snapshot(bytes.fromhex("59041B270589"), {}.get)
    assert empty["records"] == [] and empty["unparsed"] == b""
    with pytest.raises(ValueError):
        dtc.parse_snapshot(bytes.fromhex("5902FF"), {}.get)


def test_snapshot_request_is_allowed():
    from deepal_s05.elm327 import ALLOWED_REQUESTS
    assert ALLOWED_REQUESTS.match("19041B2705FF")
    for bad in ("1904", "19041B27", "14FFFFFF", "190A", "2EF22900"):
        assert not ALLOWED_REQUESTS.match(bad)


class SnapshotElm:
    """read_snapshot/read_did stand-in for read_freeze_frames."""

    def __init__(self):
        self.did_reads = []

    def read_snapshot(self, header, code_bytes, record=0xFF):
        from deepal_s05.elm327 import NegativeResponse
        if code_bytes == dtc.code_bytes("P1B53-05"):
            raise NegativeResponse(0x19, 0x31)
        return bytes.fromhex("59041B270589" "01" "03" "F229" "1773"
                             "DD01" "0001A2" "F190" "4C4A31")

    def read_did(self, header, did):
        from deepal_s05.elm327 import NegativeResponse
        self.did_reads.append(did)
        if did == 0xDD01:
            return b"\x00\x00\x00"
        if did == 0xF190:
            return b"VIN"
        raise NegativeResponse(0x22, 0x31)


def test_read_freeze_frames_decodes_known_and_hides_vin():
    from deepal_s05 import cli, vehicles
    elm = SnapshotElm()
    car = vehicles.deepal_s05()
    out = cli.read_freeze_frames(elm, "7A1", ["P1B27-05", "P1B53-05"],
                                 car.signals, 56.1)
    values = out[0]["records"][0]["values"]
    current = values[0]
    assert current["key"] == car.signal("pack_current").key
    assert current["value"] == car.signal("pack_current").value(
        bytes.fromhex("1773"), 56.1)
    assert values[1] == {"did": "DD01", "raw": "0001A2"}
    assert values[2]["raw"] == "(ซ่อน)"
    assert "0x31" in out[1]["error"]
    # lengths are learned once per DID
    assert elm.did_reads.count(0xDD01) == 1


def test_freeze_command_over_fake_adapter(tmp_path, capsys):
    import json
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from fake_elm import FakeElm
    from deepal_s05 import cli
    responses = {
        ("7A1", "1902FF"): "7A9 07 59 02 FF 1B 27 05 89",
        ("7A1", "19041B2705FF"): "7A9 10 0C 59 04 1B 27 05 89\n"
                                 "7A9 21 01 01 F2 29 17 73 00",
        ("7A1", "22F229"): "7A9 05 62 F2 29 17 73",
    }
    out = tmp_path / "freeze.json"
    with FakeElm(responses) as fake:
        assert cli.main(["--port", fake.url, "freeze", "--headers", "7A1",
                         "--save", str(out)]) == 0
    assert "19041B2705FF" in [r for _, r in fake.requests]
    frame = json.loads(out.read_text(encoding="utf-8"))["frames"][0]
    assert frame["code"] == "P1B27-05"
    assert frame["records"][0]["values"][0]["key"] == "pack_current"
    assert "กระแส" in capsys.readouterr().out
