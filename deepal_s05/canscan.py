"""Listen to what the car broadcasts on its own (roadmap item E), without
sending anything: the adapter monitors (ATMA) in silent mode (ATCSM1,
no acknowledgements) one block of CAN ids at a time, because a
Bluetooth adapter cannot keep up with the whole bus at once.

The result is one row per CAN id: how often it was seen and which bytes
changed. Two runs, for example "charging" and "parked", compared with
`compare` show which ids only exist, or only move, while charging - the
place to look for on-board charger values that 7A4 will not give over
diagnostics.
"""

import csv
import time

# 11-bit ids in blocks of 32 (filter xx0/xx0, mask 7E0)
BLOCK = 0x20
MAX_SAMPLES = 16


def blocks(lo=0x000, hi=0x7FF, size=BLOCK):
    return [(b, min(b + size - 1, hi)) for b in range(lo, hi + 1, size)]


def parse_frame(line):
    """'3A1 8 00 11 22 33 44 55 66 77' or '3A1 00 11 ...' (headers on,
    spaces on or off, optional DLC) -> (id, bytes) or None."""
    parts = line.split()
    if len(parts) == 1:  # spaces off
        text = parts[0]
        if len(text) < 5 or len(text) % 2 == 0:
            return None
        parts = [text[:3]] + [text[i:i + 2] for i in range(3, len(text), 2)]
    try:
        can_id = int(parts[0], 16)
        data = [int(p, 16) for p in parts[1:]]
    except ValueError:
        return None
    if len(parts[0]) != 3 or not data:
        return None
    if len(parts[1]) == 1:  # DLC shown (ATD1)
        data = data[1:]
    return can_id, bytes(data)


class Collector:
    """Per-id statistics: count, first and last time, first frame and the
    min/max of every byte position."""

    def __init__(self):
        self.ids = {}

    def add(self, t, can_id, data):
        s = self.ids.get(can_id)
        if s is None:
            s = self.ids[can_id] = {"count": 0, "first_t": t, "last_t": t,
                                    "sample": data, "lo": list(data),
                                    "hi": list(data), "len": len(data),
                                    "seen": []}
        s["count"] += 1
        # different payloads, for ids that multiplex several values
        # behind a selector byte (the odometer may only be in one of them)
        if data not in s["seen"] and len(s["seen"]) < MAX_SAMPLES:
            s["seen"].append(data)
        s["last_t"] = t
        for k, b in enumerate(data[:len(s["lo"])]):
            s["lo"][k] = min(s["lo"][k], b)
            s["hi"][k] = max(s["hi"][k], b)

    def rows(self, seconds_per_id):
        out = []
        for can_id in sorted(self.ids):
            s = self.ids[can_id]
            moving = [k for k in range(len(s["lo"]))
                      if s["lo"][k] != s["hi"][k]]
            out.append({
                "id": "%03X" % can_id, "count": s["count"],
                "hz": round(s["count"] / seconds_per_id.get(can_id, 1), 1),
                "len": s["len"], "sample": s["sample"].hex(" ").upper(),
                "moving_bytes": " ".join(str(k) for k in moving),
                "min": " ".join("%02X" % b for b in s["lo"]),
                "max": " ".join("%02X" % b for b in s["hi"]),
                "samples": "|".join(d.hex().upper() for d in s["seen"])})
        return out


FIELDS = ["id", "count", "hz", "len", "sample", "moving_bytes", "min", "max",
          "samples"]


def scan(elm, seconds=2.0, lo=0x000, hi=0x7FF, progress=None):
    """Monitor every block for `seconds`; returns (rows, overflows,
    warnings from the adapter)."""
    col = Collector()
    listened = {}
    overflows = 0
    warnings = set()
    for start, end in blocks(lo, hi):
        mask = 0x7FF & ~(BLOCK - 1)
        warnings.update(elm.setup_monitor("%03X" % start, "%03X" % mask))
        t0 = time.monotonic()

        def on_line(line):
            f = parse_frame(line)
            if f and start <= f[0] <= end:
                col.add(time.monotonic() - t0, *f)
        overflows += elm.monitor(on_line, duration=seconds)
        for can_id in range(start, end + 1):
            listened[can_id] = seconds
        if progress:
            progress(start, end, len(col.ids))
    return col.rows(listened), overflows, sorted(warnings)


def save(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def compare(a, b):
    """Ids only in run a, only in run b, and those whose moving bytes
    differ: {"only_a": [...], "only_b": [...], "changed": [...]}"""
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    def differs(x, y):
        if x["moving_bytes"] != y["moving_bytes"]:
            return True  # different bytes move
        # neither moves but the value is not the same (a status flag)
        return not x["moving_bytes"] and x["sample"] != y["sample"]
    changed = sorted(k for k in set(a) & set(b) if differs(a[k], b[k]))
    return {"only_a": only_a, "only_b": only_b, "changed": changed}


def find_value(rows, target, tolerance):
    """Where a number (e.g. the odometer) could sit in broadcast frames:
    every 2-4 byte window of each id's sample, big and little endian,
    read as is, x10 and x100 (0.1 / 0.01 units), within `tolerance` of
    `target`. -> [(id, first byte, length, order, scale, value)]"""
    hits = []
    for r in rows.values() if isinstance(rows, dict) else rows:
        payloads = (r.get("samples") or r["sample"].replace(" ", "")).split("|")
        found = set()
        for p in payloads:
            data = bytes.fromhex(p)
            for hit in _windows(r["id"], data, target, tolerance):
                if hit not in found:
                    found.add(hit)
                    hits.append(hit)
    return hits


def _windows(can_id, data, target, tolerance):
    hits = []
    for n in (2, 3, 4):
        for k in range(len(data) - n + 1):
            for order in ("big", "little"):
                raw = int.from_bytes(data[k:k + n], order)
                for scale in (1, 10, 100):
                    value = raw / scale
                    if abs(value - target) <= tolerance:
                        hits.append((can_id, k, n, order, scale, value))
    return hits
