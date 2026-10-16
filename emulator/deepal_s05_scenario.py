"""ELM327-emulator scenario 'deepal_s05': a Deepal S05 BMS (7A1/7A9) with a
56.1 kWh pack, answering the DIDs listed in deepal_s05/pids.py.

The values are made up for testing, not recorded from a car. The per-cell
DIDs (120 cells in F29F-F2EF, F3A9-F3B0, F3E1-F3FF) and the 16 pack
temperature DIDs (F2F0-F2FF) follow the layout found on a Thai S05 Max.
Made up as well: the insulation DID 22F310,
the trouble codes, the extended session support and the second ECU at
761/769 (standing in for the on-board charger), which exist only to
exercise the discover and ecus commands.

Use it with run_emulator.py, or from the emulator prompt (started in this
directory): merge deepal_s05_scenario, then scenario deepal_s05.
"""

import random

from elm.obd_message import ELM_FOOTER, ST, iso_tp_frames

REQ = "7A1"
RSP = "7A9"
OBC_REQ = "761"
OBC_RSP = "769"
MOTOR_REQ = "7A7"
MOTOR_RSP = "7AF"


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


CELL_DIDS = ([0xF29F + i for i in range(81)] + [0xF3A9 + i for i in range(8)]
             + [0xF3E1 + i for i in range(31)])
TEMP_DIDS = [0xF2F0 + i for i in range(16)]


def cell_millivolts(count=120, seed=5):
    """Made-up cell voltages around 3.330 V, cell 47 low, cell 83 high."""
    rnd = random.Random(seed)
    mv = [3330 + rnd.randint(-2, 2) for _ in range(count)]
    mv[46], mv[82] = 3325, 3334
    return mv


def cell_entries():
    out = {}
    for n, (did, mv) in enumerate(zip(CELL_DIDS, cell_millivolts())):
        out["CELL_%03d" % (n + 1)] = entry(
            "Cell %d voltage (made up)" % (n + 1), "%04X" % did,
            "%02X %02X" % (mv >> 8, mv & 0xFF))
    temps = [69, 70, 70, 71, 69, 70, 71, 72, 70, 69, 70, 71, 72, 72, 71, 70]
    for n, (did, raw) in enumerate(zip(TEMP_DIDS, temps)):
        out["TEMP_%02d" % (n + 1)] = entry(
            "Pack temperature %d (made up)" % (n + 1), "%04X" % did,
            "%02X" % raw)
    return out


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
        "DC_ACCEPT": entry("Max charge kW accepted", "F233", "01 51"),
        "CELL_V_MAX": entry("Cell max voltage", "F250", "0D 03"),   # 3.331 V
        "CELL_V_MIN": entry("Cell min voltage", "F251", "0D 00"),   # 3.328 V
        "T_MAX": entry("Battery temp max", "F252", "47"),           # 31 C
        "T_MIN": entry("Battery temp min", "F253", "45"),           # 29 C
        "T_CHARGE": entry("Charge port temp", "F255", "56"),        # 30 C
        "E_SUM": entry("Energy (experimental)", "F264", "0F A0"),
        "SOH": entry("SOH", "F27D", "0F 0F 78"),                    # 98.7 %
        "EFC": entry("EFC (experimental)", "F27F", "31 0D C8 F8"),  # 14.67
        **cell_entries(),
        "INSULATION": entry("Insulation resistance kOhm (made up)", "F310",
                            "13 88"),                               # 5000
        "BMS_PART": entry("Part number", "F187", ascii_hex("DEMO-BMS-01")),
        "BMS_SW": entry("Software version", "F195", ascii_hex("V1.00")),
        "BMS_VIN": entry("VIN (made up)", "F190",
                         ascii_hex("DEMOVIN0000000001")),
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
        # Electric drive, same layout as a real Thai S05 (values made up):
        # 49 C motor, 53 C inverter, 3000 rpm, 25.0 Nm, 398 V
        "MOTOR_TEMP": entry("Motor temperature", "FD0A", "59",
                            MOTOR_REQ, MOTOR_RSP),
        "INV_TEMP": entry("Inverter temperature", "FD1E", "5D",
                          MOTOR_REQ, MOTOR_RSP),
        "MOTOR_RPM": entry("Motor speed", "FD07", "59 D8", MOTOR_REQ,
                           MOTOR_RSP),
        "MOTOR_TQ": entry("Motor torque", "FD08", "10 9A", MOTOR_REQ,
                          MOTOR_RSP),
        "MOTOR_DCV": entry("DC link voltage", "FD1A", "01 8E", MOTOR_REQ,
                           MOTOR_RSP),
        # Gateway 700: odometer 42,817.4 km, 0.1 km little endian, as on
        # the real car
        "ISO_CANDIDATE": entry("Insulation candidate", "F260", "1F 40"),
        "ODOMETER": entry("Odometer", "F1AE", "8E 88 06 00", "700", "708"),
        "MOTOR_OTHER": fixed("Unsupported DID", "22[0-9A-F]{4}", "7F 22 31",
                             MOTOR_REQ, MOTOR_RSP),
        # Any other DID: requestOutOfRange, like a real ECU
        "OTHER": fixed("Unsupported DID", "22[0-9A-F]{4}", "7F 22 31"),
        "OBC_OTHER": fixed("Unsupported DID", "22[0-9A-F]{4}", "7F 22 31",
                           OBC_REQ, OBC_RSP),
    }
}
