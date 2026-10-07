"""Deepal S05 battery (BMS) signals read over UDS service 0x22.

Sources: https://github.com/jpires71/Deepal_S05_PIDs
  - deepal_s05_abrp_submit_candidate.json (primary)
  - README.md (alternative DIDs that disagree with the JSON)

The source was validated on a 68.8 kWh pack. Formulas that embed the pack
capacity take it as a parameter so they can be used on the 56.1 kWh pack
sold in Thailand.
"""

from dataclasses import dataclass
from typing import Callable, Optional

BMS_HEADER = "7A1"

VALIDATED = "validated"
CANDIDATE = "candidate"
EXPERIMENTAL = "experimental"
CUSTOM = "custom"  # added by the user with --signals

DEFAULT_CAPACITY_KWH = 56.1


def _u16(d):
    return d[0] * 256 + d[1]


def _u24(d):
    return (d[0] << 16) | (d[1] << 8) | d[2]


def _u32(d):
    return (d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]


@dataclass(frozen=True)
class Signal:
    key: str
    label: str
    did: int
    unit: str
    min_len: int
    status: str
    # decode(data_bytes_after_did, capacity_kwh) -> value; None = raw only
    decode: Optional[Callable[[bytes, float], float]]
    note: str = ""
    header: str = BMS_HEADER

    @property
    def request(self):
        return "22%04X" % self.did

    def value(self, data, capacity_kwh=DEFAULT_CAPACITY_KWH):
        if self.decode is None or len(data) < self.min_len:
            return None
        return self.decode(data, capacity_kwh)


SIGNALS = [
    Signal("soc", "SOC", 0xF22F, "%", 1, VALIDATED,
           lambda d, c: d[0]),
    Signal("pack_voltage", "แรงดันแพ็ก", 0xF228, "V", 2, VALIDATED,
           lambda d, c: _u16(d) / 10),
    Signal("pack_current", "กระแสแพ็ก", 0xF229, "A", 2, VALIDATED,
           lambda d, c: (_u16(d) - 6015) / 10,
           "ทิศทาง +/- (ชาร์จ/จ่าย) ยังไม่ยืนยัน"),
    Signal("cell_v_max", "แรงดันเซลล์สูงสุด", 0xF250, "V", 2, VALIDATED,
           lambda d, c: _u16(d) / 1000),
    Signal("cell_v_min", "แรงดันเซลล์ต่ำสุด", 0xF251, "V", 2, VALIDATED,
           lambda d, c: _u16(d) / 1000),
    Signal("batt_temp_max", "อุณหภูมิแบตสูงสุด", 0xF252, "C", 1, VALIDATED,
           lambda d, c: d[0] - 40),
    Signal("batt_temp_min", "อุณหภูมิแบตต่ำสุด", 0xF253, "C", 1, VALIDATED,
           lambda d, c: d[0] - 40),
    Signal("charge_temp", "อุณหภูมิหัวชาร์จ", 0xF255, "C", 1, CANDIDATE,
           lambda d, c: (d[0] - 32) / 1.8),
    Signal("soh", "SOH", 0xF27D, "%", 3, CANDIDATE,
           lambda d, c: _u24(d) / 10000),
    # Alternatives from the source README (disagree with the JSON profile)
    Signal("soc_alt", "SOC (สูตร README)", 0xF231, "%", 2, CANDIDATE,
           lambda d, c: _u16(d) / 10),
    Signal("soh_alt", "SOH (สูตร README)", 0xF264, "%", 1, EXPERIMENTAL,
           lambda d, c: 100 - d[0] / 68,
           "สูตรจากรถ 68 kWh ยังไม่รู้ความหมายจริง"),
    Signal("energy_available", "พลังงานคงเหลือ", 0xF264, "kWh", 2,
           EXPERIMENTAL,
           lambda d, c: c - _u16(d) / 1000),
    Signal("efc", "รอบชาร์จเต็ม (EFC)", 0xF27F, "cycles", 4, EXPERIMENTAL,
           lambda d, c: _u32(d) / 1000000 / c),
    Signal("pack_power_raw", "กำลังแพ็ก (raw)", 0xF236, "", 0, CANDIDATE,
           None, "ยังไม่มีสูตร เก็บค่าดิบไว้เทียบ"),
]

SIGNALS_BY_KEY = {s.key: s for s in SIGNALS}

# Signals shown by the "live" command
LIVE_KEYS = ["soc", "pack_voltage", "pack_current", "cell_v_max",
             "cell_v_min", "batt_temp_max", "batt_temp_min", "soh",
             "charge_temp"]


def derived(values):
    """Values computed from other signals; `values` maps key -> value."""
    out = {}
    v, i = values.get("pack_voltage"), values.get("pack_current")
    if v is not None and i is not None:
        out["pack_power_kw"] = v * i / 1000
    hi, lo = values.get("cell_v_max"), values.get("cell_v_min")
    if hi is not None and lo is not None:
        out["cell_delta_mv"] = (hi - lo) * 1000
    hi, lo = values.get("batt_temp_max"), values.get("batt_temp_min")
    if hi is not None and lo is not None:
        out["temp_delta_c"] = hi - lo
    # insulation resistance per volt of pack voltage, the unit safety
    # standards use (ISO 6469-3: at least 100 ohm/V on the DC side)
    r = values.get("insulation_kohm")
    if isinstance(r, (int, float)) and v:
        out["insulation_ohm_per_v"] = r * 1000 / v
    return out
