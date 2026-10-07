"""ECU identification (ISO 14229 DIDs F187-F197) and recognising which
battery pack a car has from its BMS part number."""

from .elm327 import NegativeResponse, NoData

# Identification DIDs read from every ECU found
ID_DIDS = [
    (0xF187, "part_number", "รหัสชิ้นส่วน"),
    (0xF18A, "supplier", "ผู้ผลิต"),
    (0xF197, "system_name", "ชื่อระบบ"),
    (0xF191, "hw_number", "เลขฮาร์ดแวร์"),
    (0xF193, "hw_version", "เวอร์ชันฮาร์ดแวร์"),
    (0xF195, "sw_version", "เวอร์ชันซอฟต์แวร์"),
]

LABELS = {key: label for _, key, label in ID_DIDS}


def as_text(raw):
    text = raw.decode("ascii", "replace").strip("\x00 ")
    if raw and all(32 <= b < 127 or b == 0 for b in raw):
        return text
    return raw.hex(" ").upper()


def read_identity(elm, header):
    """{key: text} for the identification DIDs the ECU answers ("" for
    the ones it does not). Raises NoData when the ECU is silent."""
    info, answered = {}, False
    for did, key, _ in ID_DIDS:
        try:
            info[key] = as_text(elm.read_did(header, did))
            answered = True
        except NegativeResponse:
            info[key] = ""
            answered = True  # it answered, so the ECU exists
        except NoData:
            info[key] = ""
    if not answered:
        raise NoData("ECU %s ไม่ตอบ" % header)
    return info


def match_pack(known, info):
    """The entry of `known` whose part number prefix matches the BMS, or
    None. Entries look like {"part": "...", "capacity": 56.1, "label": ...}."""
    part = (info.get("part_number") or "").replace(" ", "").upper()
    if not part:
        return None
    for entry in known:
        if part.startswith(entry["part"].replace(" ", "").upper()):
            return entry
    return None


def describe(info):
    return "  ".join("%s: %s" % (LABELS[k], v) for k, v in info.items() if v)


def bms_identity(elm, vehicle):
    """(header, info) from the first of the vehicle's BMS headers that
    answers, or (None, {})."""
    for header in vehicle.bms_headers:
        try:
            return header, read_identity(elm, header)
        except NoData:
            continue
    return None, {}


def pack_info(vehicle, capacity, bms=None, abnormal_mv=30):
    """Static facts the dashboard shows next to the live values. The page
    estimates the rated voltage as cell count x nominal cell voltage."""
    return {"car": vehicle.name, "capacity_kwh": capacity,
            "cell_nominal_v": vehicle.cell_nominal_v, "bms": bms or {},
            "abnormal_mv": abnormal_mv}
