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
           "ลบ = ชาร์จเข้าแบต (ยืนยันกับ S05 56.1 ไทย: -8.0 A x 405 V"
           " = 3.2 kW ตรงกับหน้าจอรถ)"),
    Signal("cell_v_max", "แรงดันเซลล์สูงสุด", 0xF250, "V", 2, VALIDATED,
           lambda d, c: _u16(d) / 1000),
    Signal("cell_v_min", "แรงดันเซลล์ต่ำสุด", 0xF251, "V", 2, VALIDATED,
           lambda d, c: _u16(d) / 1000),
    Signal("batt_temp_max", "อุณหภูมิแบตสูงสุด", 0xF252, "C", 1, VALIDATED,
           lambda d, c: d[0] - 40),
    Signal("batt_temp_min", "อุณหภูมิแบตต่ำสุด", 0xF253, "C", 1, VALIDATED,
           lambda d, c: d[0] - 40),
    # On a Thai S05 56.1 this read 10 C (once 35 C) while AC charging in
    # 30 C weather, so it is probably not the charge port temperature
    Signal("charge_temp", "อุณหภูมิหัวชาร์จ", 0xF255, "C", 1, EXPERIMENTAL,
           lambda d, c: (d[0] - 32) / 1.8,
           "รถไทย 56.1 อ่านได้ 10 °C ตอนชาร์จ น่าจะไม่ใช่อุณหภูมิหัวชาร์จ"),
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
    # Max charge power the BMS accepts right now (x0.1 kW). 0x0151 = 33.7
    # matched 83 A x 400 V at the P1B27 freeze frame; AC charge read 10.6.
    Signal("dc_accept_kw", "แบตรับชาร์จได้", 0xF233, "kW", 2, CANDIDATE,
           lambda d, c: _u16(d) / 10,
           "กำลังชาร์จสูงสุดที่ BMS ยอมรับ ณ ตอนนี้ (รอยืนยันตอนชาร์จ DC)"),
    Signal("pack_power_raw", "กำลังแพ็ก (raw)", 0xF236, "", 0, CANDIDATE,
           None, "ยังไม่มีสูตร เก็บค่าดิบไว้เทียบ"),
]

SIGNALS_BY_KEY = {s.key: s for s in SIGNALS}

# Signals shown by the "live" command
# Per-cell voltages and pack temperature sensors, found on a Thai S05 Max
# 56.1 kWh (2026-10-16): one DID per cell, 2 bytes in mV, 120 cells in
# three blocks (their mean x 120 matched F228 exactly), and 16 one-byte
# temperatures (A-40, matching F252/F253). The cell numbering follows the
# DID order and may not match the physical order in the pack.
CELL_DIDS = (list(range(0xF29F, 0xF2F0)) + list(range(0xF3A9, 0xF3B1)) +
             list(range(0xF3E1, 0xF400)))
TEMP_DIDS = list(range(0xF2F0, 0xF300))

CELL_SIGNALS = [
    Signal("cell_%03d" % (n + 1), "เซลล์ #%d" % (n + 1), did, "V", 2,
           CANDIDATE, lambda d, c: _u16(d) / 1000)
    for n, did in enumerate(CELL_DIDS)]
TEMP_SIGNALS = [
    Signal("temp_%02d" % (n + 1), "อุณหภูมิแพ็ก T%d" % (n + 1), did, "C", 1,
           CANDIDATE, lambda d, c: d[0] - 40)
    for n, did in enumerate(TEMP_DIDS)]

# Electric drive (motor controller "EDS2", 7A7 -> 7AF), from a 12-minute
# drive on a Thai S05 Max (2026-10-16). Torque x speed matched pack power
# (e.g. regen -42.5 Nm at 9256 rpm vs -103 A x 405 V = -41.8 kW), and
# FD1A tracked F228 within 0.2 V on average. FD0A rose steadily 49 -> 69 C
# (motor), FD1E jumped with current (inverter). The offset is A-50, not
# the BMS's A-40: with A-40 the idle motor read ~10 C above the pack every
# time the car stood (45 vs 33 C after hours parked on, 41 vs 31 C at 6:29
# after 3.5 h off in 23 C air), and a motor cools faster than the pack.
# Still a candidate until it is seen at air temperature after a full night.
MOTOR_HEADER = "7A7"
# Motor rpm per km/h shown on the speedometer: 6470 rpm at an 80 km/h
# cruise (28 steady samples, 2026-10-16), about a 10.9:1 reduction on
# 19-inch wheels. GPS speed is usually a few percent lower.
RPM_PER_KMH = 80.9
MOTOR_SIGNALS = [
    Signal("motor_temp", "อุณหภูมิมอเตอร์", 0xFD0A, "C", 1, CANDIDATE,
           lambda d, c: d[0] - 50, "สูตร A-50 (A-40 ร้อนกว่าแบตตอนจอดเสมอ) ยังรอยืนยัน",
           header=MOTOR_HEADER),
    Signal("inverter_temp", "อุณหภูมิอินเวอร์เตอร์", 0xFD1E, "C", 1,
           CANDIDATE, lambda d, c: d[0] - 50,
           "สูตร A-50 (A-40 ร้อนกว่าแบตตอนจอดเสมอ) ยังรอยืนยัน",
           header=MOTOR_HEADER),
    Signal("motor_rpm", "รอบมอเตอร์", 0xFD07, "rpm", 2, CANDIDATE,
           lambda d, c: _u16(d) - 20000, header=MOTOR_HEADER),
    Signal("motor_torque", "แรงบิดมอเตอร์", 0xFD08, "Nm", 2, CANDIDATE,
           lambda d, c: (_u16(d) - 4000) / 10, "ลบ = เบรกชาร์จกลับ",
           header=MOTOR_HEADER),
    Signal("motor_dc_v", "แรงดันเข้าอินเวอร์เตอร์", 0xFD1A, "V", 2,
           CANDIDATE, lambda d, c: _u16(d), header=MOTOR_HEADER),
]

# Odometer, 0.1 km. Found by a DID scan of every ECU on a Thai S05
# (2026-10-16 17:06): ECU 700 F1AE = 8E 88 06 00 (little endian) and the
# radars 753/754 F1F5 = 06 88 8E (big endian), both 42,817.4 km, between
# the dashboard reading that morning (42,769) and later in the day; the
# telematics box keeps the same unit in its snapshots (706 F308/F309).
# Confirmed: the dashboard showed 42,817 km while this read 42,817.4.
ODOMETER_SIGNALS = [
    Signal("odometer_km", "เลขไมล์", 0xF1AE, "km", 4, VALIDATED,
           lambda d, c: int.from_bytes(d[:4], "little") / 10,
           "ยืนยันกับหน้าปัด 42,817 km (2026-10-16)", header="700"),
]

# Insulation (HV bus to chassis) candidate: F260 read 0x1F40 = 8000 on the
# BMS live and in both freeze frames (one taken at -125 A), the kind of
# fixed ceiling an insulation monitor reports when the resistance is above
# its range (8000 kOhm = 20,000 Ohm/V at 400 V, far above the 500 Ohm/V
# warning). Logged under its own key so it cannot trigger the insulation
# alerts until confirmed.
INSULATION_CANDIDATES = [
    Signal("iso_candidate_kohm", "ค่าฉนวน? (F260 รอยืนยัน)", 0xF260, "kΩ", 2,
           EXPERIMENTAL, lambda d, c: _u16(d),
           "ตัวเต็งค่าฉนวนไฟแรงสูง ยังไม่ยืนยัน ไม่ใช้แจ้งเตือน"),
]

LIVE_KEYS = ["soc", "pack_voltage", "pack_current", "cell_v_max",
             "cell_v_min", "batt_temp_max", "batt_temp_min", "soh",
             "charge_temp", "motor_temp", "inverter_temp", "motor_rpm",
             "motor_torque", "odometer_km", "iso_candidate_kohm", "dc_accept_kw"]


# Above this charging current (A into the pack) it is a DC fast charge:
# home AC at 32 A x 230 V gives at most about 18 A at 400 V
DC_CHARGE_A = 40


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
    rpm = values.get("motor_rpm")
    if isinstance(rpm, (int, float)):
        out["speed_kmh"] = abs(rpm) / RPM_PER_KMH
    # fast (DC) charging: more than AC can push into the pack (charging is
    # negative on the S05) with the car standing: regen braking also puts
    # over 100 A in (a "stop DC charging" alert came while driving). The
    # motor ECU may not answer while charging, so no rpm counts as still.
    # Copies of SOC and pack temperature that only exist then, so the
    # alert rules can warn about them
    moving = isinstance(rpm, (int, float)) and abs(rpm) >= 50
    if i is not None and i < -DC_CHARGE_A and not moving:
        if values.get("soc") is not None:
            out["dc_charge_soc"] = values["soc"]
        if values.get("batt_temp_max") is not None:
            out["dc_charge_temp"] = values["batt_temp_max"]
        if v is not None:
            out["dc_supply_kw"] = -v * i / 1000  # what the pack takes in
    # insulation resistance per volt of pack voltage, the unit safety
    # standards use (ISO 6469-3: at least 100 ohm/V on the DC side)
    r = values.get("insulation_kohm")
    if isinstance(r, (int, float)) and v:
        out["insulation_ohm_per_v"] = r * 1000 / v
    return out
