"""ELM327-emulator scenario 'deepal_s05': a Deepal S05 BMS (7A1/7A9) with a
56.1 kWh pack, answering the DIDs listed in deepal_s05/pids.py.

The values are made up for testing, not recorded from a car.

Use it with run_emulator.py, or from the emulator prompt (started in this
directory): merge deepal_s05_scenario, then scenario deepal_s05.
"""

from elm.obd_message import ELM_FOOTER, HD, SZ, DT, ST

REQ = "7A1"
RSP = "7A9"


def answer(did, data):
    """Positive single-frame answer to 22<did> carrying `data` (hex)."""
    payload = "62 %s %s %s" % (did[:2], did[2:], data)
    return HD(RSP) + SZ("%02X" % len(payload.split())) + DT(payload)


def entry(descr, did, data):
    responses = [answer(did, d) for d in data] if isinstance(data, list) \
        else answer(did, data)
    return {
        "Request": "^22" + did + ELM_FOOTER,
        "Descr": descr,
        "Header": REQ,
        "Response": responses,
    }


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
        # Any other DID in F2xx: requestOutOfRange, like a real ECU
        "OTHER_F2": {
            "Request": "^22F2[0-9A-F]{2}" + ELM_FOOTER,
            "Descr": "Unsupported DID",
            "Header": REQ,
            "Response": HD(RSP) + SZ("03") + DT("7F 22 31"),
        },
    }
}
