"""Read vehicle signal definitions from OBDb (https://github.com/OBDb).

OBDb signal sets are shared under CC BY-SA 4.0. This program does not ship
them: `fetch-obdb` downloads the file for a car, and load() turns it into
Signal objects at run time.

An OBDb command looks like
  {"hdr": "DA01", "rax": "01", "cmd": {"22": "202A"},
   "signals": [{"id": "CRVH_SOC", "path": "Battery",
                "fmt": {"bix": 400, "len": 8, "unit": "percent"}}]}
hdr DA01 is the 29-bit request id 18DA01F1 (answered by 18DAF101); bix and
len count bits from the first data byte after the echoed DID.
"""

import json
import urllib.request

from . import pids

RAW_URL = ("https://raw.githubusercontent.com/OBDb/%s/main/"
           "signalsets/v3/default.json")

UNITS = {
    "percent": "%", "volts": "V", "millivolts": "mV", "amps": "A",
    "celsius": "C", "fahrenheit": "F", "kilopascal": "kPa", "bars": "bar",
    "kilometers": "km", "miles": "mi", "liters": "L", "rpm": "rpm",
    "kilowatts": "kW", "seconds": "s", "millimeters": "mm",
    "ampereHours": "Ah", "scalar": "", "offon": "", "unknown": "",
}

OBDB = "obdb"  # Signal.status for imported signals


class ObdbError(Exception):
    pass


def request_header(hdr):
    """"DA01" -> "18DA01F1" (29-bit id with the usual tester address)."""
    hdr = hdr.upper()
    if len(hdr) != 4:
        raise ObdbError("unsupported header %r" % hdr)
    return "18" + hdr + "F1"


def applies(command, year):
    """Whether a command's model-year filter includes `year` (None = any)."""
    f = command.get("filter")
    if not f or year is None:
        return True
    if year in f.get("years", []):
        return True
    if "to" in f and year <= f["to"]:
        return True
    if "from" in f and year >= f["from"]:
        return True
    return False


def extract(data, fmt):
    """Decode one signal from the bytes after the DID, or None."""
    bix, length = fmt.get("bix", 0), fmt["len"]
    total = len(data) * 8
    if bix + length > total:
        return None
    raw = (int.from_bytes(data, "big") >> (total - bix - length)) & \
        ((1 << length) - 1)
    if fmt.get("sign") and raw >> (length - 1):
        raw -= 1 << length
    if "map" in fmt:
        entry = fmt["map"].get(str(raw))
        return entry["value"] if entry else raw
    value = raw * fmt.get("mul", 1) / fmt.get("div", 1) + fmt.get("add", 0)
    if "nullmax" in fmt and value > fmt["nullmax"]:
        return None
    if "nullmin" in fmt and value < fmt["nullmin"]:
        return None
    return value


def make_signal(command, sig, key, label=None, scale=1.0, unit=None):
    fmt = sig["fmt"]
    service, ident = next(iter(command["cmd"].items()))
    if service != "22":
        raise ObdbError("only service 22 commands are supported")

    def decode(data, capacity, fmt=fmt, scale=scale):
        value = extract(data, fmt)
        if isinstance(value, (int, float)) and scale != 1:
            value *= scale
        return value

    return pids.Signal(
        key=key, label=label or sig.get("name", sig["id"]),
        did=int(ident, 16),
        unit=unit if unit is not None else UNITS.get(fmt.get("unit"),
                                                     fmt.get("unit", "")),
        min_len=(fmt.get("bix", 0) + fmt["len"] + 7) // 8, status=OBDB,
        decode=decode, note="OBDb " + sig["id"],
        header=request_header(command["hdr"]))


def load_commands(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data["commands"]
    except (OSError, ValueError, KeyError) as e:
        raise ObdbError("อ่านไฟล์ OBDb %s ไม่ได้: %s" % (path, e))


def fetch(repo, path):
    """Download a car's signal set from OBDb to `path`."""
    with urllib.request.urlopen(RAW_URL % repo, timeout=30) as r:
        body = r.read()
    json.loads(body)  # refuse to save something that is not JSON
    with open(path, "wb") as f:
        f.write(body)
    return len(body)
