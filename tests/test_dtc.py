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
