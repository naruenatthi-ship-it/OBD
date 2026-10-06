"""ELM327-emulator scenario 'deepal_s05': a Deepal S05 BMS (7A1/7A9) with a
56.1 kWh pack, answering the DIDs listed in deepal_s05/pids.py.

The values are made up for testing, not recorded from a car. So are the
per-cell DID 22F2A0, the temperature sensor DID 22F2A1, the trouble codes, the extended session support and the second ECU at
761/769 (standing in for the on-board charger): they exist only to exercise
the discover and ecus commands. The real addresses and DIDs are unknown.

Use it with run_emulator.py, or from the emulator prompt (started in this
directory): merge deepal_s05_scenario, then scenario deepal_s05.
"""

import random

from elm.obd_message import ELM_FOOTER, ST, iso_tp_frames

REQ = "7A1"
RSP = "7A9"
OBC_REQ = "761"
OBC_RSP = "769"


def answer(did, data, rsp=RSP):
    """Positive answer to 22<did> carrying `data` (hex), framed as ISO-TP."""
    payload = ("62 %s %s %s" % (did[:2], did[2:], data)).split()
    return iso_tp_frames(payload, rsp)


def entry(descr, did, data, req=REQ, rsp=RSP):
    responses = [answer(did, d, rsp) for d in data] \
        if isinstance(data, list) else answer(did, data, rsp)
    return {
        "Request": "^22" + did + ELM_FOOTER,
        "Descr": descr,
        "Header": req,
        "Response": responses,
    }


def ascii_hex(text):
    return " ".join("%02X" % ord(c) for c in text)


def fixed(descr, request, response_hex, req=REQ, rsp=RSP):
    return {
        "Request": "^" + request + ELM_FOOTER,
        "Descr": descr,
        "Header": req,
        "Response": iso_tp_frames(response_hex.split(), rsp),
    }


def cell_voltages(count=108, seed=5):
    """Made-up cell voltages around 3.330 V, cell 47 low, cell 83 high."""
    rnd = random.Random(seed)
    mv = [3330 + rnd.randint(-2, 2) for _ in range(count)]
    mv[46], mv[82] = 3325, 3334
    return " ".join("%02X %02X" % (v >> 8, v & 0xFF) for v in mv)


ObdMessage = {
    "deepal_s05": {
        "ELM_VOLTAGE": {
            "Request": "^ATRV" + ELM_FOOTER,
            "Descr": "Voltage detected by OBD-II adapter",
            "Response": ST("13.1V "),
        },
        # 72 %, then 71 % on alternate reads
        "SOC": entry("SOC", "F22F", ["48", "48", "47"]),
        "SOC_ALT": entry("SOC (README)", "F231", "02 D0"),           # 72.0 %
        "PACK_V": entry("Pack voltage", "F228", "0E 02"),           # 358.6 V
        # -1.2 A, 15.4 A, 42.0 A
        "PACK_I": entry("Pack current", "F229",
                        ["17 73", "18 19", "19 23"]),
        "PACK_P": entry("Pack power raw", "F236", "00 15 2A"),
        "CELL_V_MAX": entry("Cell max voltage", "F250", "0D 03"),   # 3.331 V
        "CELL_V_MIN": entry("Cell min voltage", "F251", "0D 00"),   # 3.328 V
        "T_MAX": entry("Battery temp max", "F252", "47"),           # 31 C
        "T_MIN": entry("Battery temp min", "F253", "45"),           # 29 C
        "T_CHARGE": entry("Charge port temp", "F255", "56"),        # 30 C
        "E_SUM": entry("Energy (experimental)", "F264", "0F A0"),
        "SOH": entry("SOH", "F27D", "0F 0F 78"),                    # 98.7 %
        "EFC": entry("EFC (experimental)", "F27F", "31 0D C8 F8"),  # 14.67
        "CELLS": entry("Cell voltages (made up)", "F2A0", cell_voltages()),
        "TEMPS": entry("Temperature sensors (made up)", "F2A1",
                       "45 46 46 47 45 46 47 48 46 45 46 47 48 48 47 46"),
        "BMS_PART": entry("Part number", "F187", ascii_hex("DEMO-BMS-01")),
        "BMS_SW": entry("Software version", "F195", ascii_hex("V1.00")),
        "BMS_EXT": fixed("Extended session", "1003", "50 03 00 32 01 F4"),
        "BMS_DEF": fixed("Default session", "1001", "50 01 00 32 01 F4"),
        "BMS_TP": fixed("Tester present", "3E00", "7E 00"),
        "OBC_PART": entry("Part number", "F187", ascii_hex("DEMO-OBC-01"),
                          OBC_REQ, OBC_RSP),
        "OBC_TEMP": entry("Temperature (made up)", "F2C1", "5A",
                          OBC_REQ, OBC_RSP),
        # Made-up trouble codes: BMS P0A80-00 (confirmed) and U0111-00
        # (failed since clear), OBC P0562-16 (failed now, confirmed)
        "BMS_DTC": fixed("Trouble codes", "1902FF",
                         "59 02 FF 0A 80 00 08 C1 11 00 20"),
        "OBC_DTC": fixed("Trouble codes", "1902FF", "59 02 FF 05 62 16 09",
                         OBC_REQ, OBC_RSP),
        # Any other DID: requestOutOfRange, like a real ECU
        "OTHER": fixed("Unsupported DID", "22[0-9A-F]{4}", "7F 22 31"),
        "OBC_OTHER": fixed("Unsupported DID", "22[0-9A-F]{4}", "7F 22 31",
                           OBC_REQ, OBC_RSP),
    }
}
