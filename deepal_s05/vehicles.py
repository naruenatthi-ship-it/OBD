"""Supported cars: which signals to read, how to talk to them, and which
values make up the per-cell and temperature views."""

import os
import re
from dataclasses import dataclass, field

from . import obdb, pids


@dataclass(frozen=True)
class GroupArray:
    """An array built from several signals (for example 72 cell voltages
    spread over two DIDs). Values outside [low, high] are left out."""
    key: str
    label: str
    unit: str
    members: tuple
    low: float = float("-inf")
    high: float = float("inf")

    def collect(self, values):
        out = []
        for k in self.members:
            v = values.get(k)
            if isinstance(v, (int, float)) and self.low < v < self.high:
                out.append(v)
        return out


@dataclass
class Vehicle:
    key: str
    name: str
    protocol: str                  # ELM327 ATSP: "6" 11-bit, "7" 29-bit
    signals: list
    live_keys: list                # read by live/charge/dashboard
    capacity: float                # kWh, for energy estimates
    default_header: str            # discover default
    groups: list = field(default_factory=list)
    status_keys: list = field(default_factory=list)   # extra status line
    rule_overrides: list = field(default_factory=list)
    source: str = ""

    @property
    def can29(self):
        return self.protocol in ("7", "9")

    def signal(self, key):
        return next((s for s in self.signals if s.key == key), None)

    def live_signals(self):
        wanted = set(self.live_keys)
        for g in self.groups:
            wanted.update(g.members)
        return [s for s in self.signals if s.key in wanted]

    def group_members(self):
        return {k for g in self.groups for k in g.members}

    def default_headers(self, start=None, end=None):
        """All request ids to probe for ecus/dtc/checkup."""
        if self.can29:
            lo, hi = int(start or "00", 16), int(end or "FF", 16)
            return ["18DA%02XF1" % i for i in range(lo, hi + 1)]
        lo, hi = int(start or "700", 16), int(end or "7FF", 16)
        return ["%03X" % i for i in range(lo, hi + 1)]


def deepal_s05(**_):
    return Vehicle(
        key="deepal-s05", name="Deepal S05", protocol="6",
        signals=list(pids.SIGNALS), live_keys=list(pids.LIVE_KEYS),
        capacity=pids.DEFAULT_CAPACITY_KWH, default_header=pids.BMS_HEADER,
        source="jpires71/Deepal_S05_PIDs")


# -- Honda CR-V e:HEV (6th generation, 2023-) from OBDb -------------------

CRV_REPO = "Honda-CR-V-Hybrid"
CRV_DEFAULT_FILE = os.path.join("obdb", CRV_REPO + ".json")

# OBDb id -> (our key, Thai label); keys used by alerts and screens
CRV_KEYS = {
    "CRVH_SOC": ("soc", "SOC"),
    "CRVH_HVB_V": ("pack_voltage", "แรงดันแบตไฮบริด"),
    "CRVH_HVB_C_TOTAL": ("pack_current", "กระแสแบตไฮบริด"),
    "CRVH_HVB_CAP": ("hv_capacity_ah", "ความจุแบตไฮบริด"),
    "CRVH_CELL_MAX_SOC": ("cell_soc_max", "SOC เซลล์สูงสุด"),
    "CRVH_CELL_MIN_SOC": ("cell_soc_min", "SOC เซลล์ต่ำสุด"),
    "CRVH_BATTERY_COOLANT_TEMPERATURE": ("hv_coolant_temp",
                                         "อุณหภูมิน้ำหล่อเย็นแบต"),
    "CRVH_DC_DC_CONVERTER_TEMPERATURE": ("dcdc_temp", "อุณหภูมิ DC-DC"),
    "CRVH_HV_BATTERY_FAN_3_SPEED": ("hv_fan_rpm", "พัดลมแบตไฮบริด"),
    "CRVH_MOTOR_INVERTER_CURRENT": ("inverter_current",
                                    "กระแสอินเวอร์เตอร์มอเตอร์"),
    "CRVH_GENERATOR_MOTOR_SPEED": ("generator_rpm", "รอบเจนเนอเรเตอร์"),
    "CRVH_ECT_1": ("engine_coolant_temp", "อุณหภูมิน้ำหล่อเย็นเครื่อง"),
    "CRVH_ODO": ("odometer", "เลขไมล์"),
    "CRVH_FLV": ("fuel_l", "น้ำมันเหลือ"),
    "CRVH_TP_FL": ("tire_fl", "ลมยางหน้าซ้าย"),
    "CRVH_TP_FR": ("tire_fr", "ลมยางหน้าขวา"),
    "CRVH_TP_RL": ("tire_rl", "ลมยางหลังซ้าย"),
    "CRVH_TP_RR": ("tire_rr", "ลมยางหลังขวา"),
}

CRV_LIVE = ["soc", "pack_voltage", "pack_current", "hv_coolant_temp",
            "dcdc_temp", "hv_fan_rpm", "engine_coolant_temp", "tire_fl",
            "tire_fr", "tire_rl", "tire_rr"]

CRV_STATUS = ["hv_fan_rpm", "hv_coolant_temp", "dcdc_temp", "tire_fl",
              "tire_fr", "tire_rl", "tire_rr"]

# Rough starting limits for a Li-ion (NMC) hybrid pack and tyres; not
# Honda specifications. Override them in the --signals file.
CRV_RULES = [
    {"key": "cell_v_max", "warn_above": 4.15, "critical_above": 4.2,
     "advice": "มีเซลล์แรงดันสูงเกินช่วงปกติของแบตลิเทียมไอออน"},
    {"key": "cell_v_min", "warn_below": 3.3, "critical_below": 3.0,
     "advice": "มีเซลล์แรงดันต่ำมาก"},
    {"key": "delta_mv", "warn_above": 50, "critical_above": 100,
     "advice": "เซลล์ไม่สมดุล ให้ศูนย์ตรวจแบตไฮบริด"},
    {"key": "hv_coolant_temp", "label": "อุณหภูมิน้ำหล่อเย็นแบต",
     "unit": "°C", "warn_above": 45, "critical_above": 55,
     "advice": "แบตไฮบริดร้อน ให้ตรวจระบบระบายความร้อนแบต"},
    {"key": "dcdc_temp", "label": "อุณหภูมิ DC-DC", "unit": "°C",
     "warn_above": 80, "critical_above": 95},
] + [{"key": k, "label": label, "unit": "kPa", "warn_below": 200,
      "critical_below": 170, "advice": "ลมยางอ่อน เติมลมและเช็กรอยรั่ว"}
     for k, label in (("tire_fl", "ลมยางหน้าซ้าย"), ("tire_fr", "ลมยางหน้าขวา"),
                      ("tire_rl", "ลมยางหลังซ้าย"),
                      ("tire_rr", "ลมยางหลังขวา"))]

MODULE_TEMP = re.compile(r"HVB_MOD_\w+_T$")


def crv_hybrid(obdb_path=None, year=None, **_):
    path = obdb_path or CRV_DEFAULT_FILE
    if not os.path.exists(path):
        raise obdb.ObdbError(
            "ยังไม่มีไฟล์ข้อมูล OBDb ของ CR-V (%s) ดาวน์โหลดด้วย:\n"
            "  python -m deepal_s05 --car crv-hybrid fetch-obdb" % path)
    signals, cells, temps, seen = [], [], [], set()
    for command in obdb.load_commands(path):
        if not obdb.applies(command, year):
            continue
        for sig in command["signals"]:
            oid = sig["id"]
            if sig.get("path") == "Battery.Cells" and \
                    sig["fmt"].get("unit") == "millivolts":
                key, label = oid.lower(), sig.get("name", oid)
                signals.append(obdb.make_signal(command, sig, key, label,
                                                scale=0.001, unit="V"))
                cells.append(key)
                continue
            key, label = CRV_KEYS.get(oid, (oid.lower(), None))
            if key in seen:  # same value from a second source
                key = oid.lower()
            seen.add(key)
            signals.append(obdb.make_signal(command, sig, key, label))
            if MODULE_TEMP.search(oid):
                temps.append(key)
    groups = [
        GroupArray("cells", "แรงดันรายเซลล์", "V", tuple(cells), 0.5, 4.9),
        GroupArray("temps", "อุณหภูมิโมดูลแบต", "C", tuple(temps), -40, 120),
    ]
    return Vehicle(
        key="crv-hybrid", name="Honda CR-V e:HEV", protocol="7",
        signals=signals, live_keys=list(CRV_LIVE), capacity=1.1,
        default_header="18DA01F1", groups=groups,
        status_keys=list(CRV_STATUS), rule_overrides=list(CRV_RULES),
        source="OBDb/%s (CC BY-SA 4.0)" % CRV_REPO)


CARS = {
    "deepal-s05": deepal_s05,
    "crv-hybrid": crv_hybrid,
}

OBDB_REPOS = {"crv-hybrid": (CRV_REPO, CRV_DEFAULT_FILE)}


def load(car, obdb_path=None, year=None):
    if car not in CARS:
        raise obdb.ObdbError("ไม่รู้จักรถ %r (มี: %s)" % (car,
                                                     ", ".join(CARS)))
    return CARS[car](obdb_path=obdb_path, year=year)
