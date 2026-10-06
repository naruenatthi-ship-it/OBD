"""Helpers for working out unknown DIDs from raw data collected at home:
compare two scans, and find bytes that move together with a known value."""

import csv
import math

INTERPRETATIONS = [
    # name, byte width, decoder(data, pos) -> int
    ("u8", 1, lambda d, i: d[i]),
    ("u16", 2, lambda d, i: (d[i] << 8) | d[i + 1]),
    ("s16", 2, lambda d, i: ((d[i] << 8) | d[i + 1]) -
     (0x10000 if d[i] & 0x80 else 0)),
    ("u16le", 2, lambda d, i: d[i] | (d[i + 1] << 8)),
    ("u32", 4, lambda d, i: int.from_bytes(d[i:i + 4], "big")),
]


def read_scan(path):
    """discover CSV -> {(header, did): bytes}."""
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[(row["header"].upper(), row["did"].upper())] = \
                bytes.fromhex(row["raw"])
    return out


def diff_scans(a, b):
    """Changes between two scans (dicts from read_scan).

    Returns dict with only_a, only_b (sorted keys), length (keys whose
    length changed) and changed: {key: [(pos, old, new)]}."""
    changed, length = {}, []
    for key in sorted(set(a) & set(b)):
        x, y = a[key], b[key]
        if len(x) != len(y):
            length.append(key)
            continue
        moves = [(i, x[i], y[i]) for i in range(len(x)) if x[i] != y[i]]
        if moves:
            changed[key] = moves
    return {"only_a": sorted(set(a) - set(b)),
            "only_b": sorted(set(b) - set(a)),
            "length": length, "changed": changed}


def temp_guess(old, new):
    """Text for a byte that reads like a temperature (A-40), else ""."""
    if all(-20 <= v - 40 <= 120 for v in (old, new)):
        return "ถ้าเป็นอุณหภูมิ (A-40): %d -> %d °C" % (old - 40, new - 40)
    return ""


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / \
        math.sqrt(sxx * syy)


def nice_number(v):
    """Snap a fitted factor to a round value when it is close to one.
    Returns (value, is_round)."""
    for c in (1, 2, 4, 5, 8, 10, 16, 20, 25, 50, 64, 100, 128, 256, 1000,
              1024, 10000):
        for sign in (1, -1):
            for cand in (sign * c, sign / c):
                if abs(v - cand) <= abs(cand) * 0.03:
                    return cand, True
    return v, False


def formula_text(name, pos, scale, offset):
    """Formula in the --signals syntax (A, B, C ... = data bytes)."""
    def L(k):
        return chr(ord("A") + k) if k < 26 else "?"
    a, b = L(pos), L(pos + 1)
    raw = {
        "u8": a,
        "u16": "(%s*256+%s)" % (a, b),
        "s16": "(((%s*256+%s)+32768)%%65536-32768)" % (a, b),
        "u16le": "(%s+%s*256)" % (a, b),
        "u32": "(%s*16777216+%s*65536+%s*256+%s)" % (
            a, b, L(pos + 2), L(pos + 3)),
    }[name]
    if scale == 1:
        text = raw
    elif scale and abs(1 / scale) > 1 and \
            abs(1 / scale - round(1 / scale)) < 1e-9:
        text = "%s/%g" % (raw, 1 / scale)
    else:
        text = "%s*%g" % (raw, scale)
    if abs(offset) >= 0.0005:
        text += "%+g" % round(offset, 3)
    return text


def correlate(rows, ref_key, did_keys, min_r=0.9, top=10):
    """Find byte interpretations in recorded DIDs that follow `ref_key`.

    rows: list of dicts with ref_key -> number and did_key -> bytes.
    Returns the best candidates sorted by |r|: dicts with did, name, pos,
    r, scale, offset, rmse, formula."""
    found = []
    for key in did_keys:
        samples = [(r[ref_key], r[key]) for r in rows
                   if r.get(ref_key) is not None and r.get(key)]
        if len(samples) < 5:
            continue
        refs = [s[0] for s in samples]
        width = min(len(s[1]) for s in samples)
        for name, size, dec in INTERPRETATIONS:
            for pos in range(0, width - size + 1):
                xs = [dec(s[1], pos) for s in samples]
                if len(set(xs)) < 3:
                    continue
                r = pearson(xs, refs)
                if abs(r) < min_r:
                    continue
                n = len(xs)
                mx, my = sum(xs) / n, sum(refs) / n
                sxx = sum((x - mx) ** 2 for x in xs)
                scale = sum((x - mx) * (y - my)
                            for x, y in zip(xs, refs)) / sxx
                scale, nice = nice_number(scale)
                offset = my - scale * mx
                rmse = math.sqrt(sum((scale * x + offset - y) ** 2
                                     for x, y in zip(xs, refs)) / n)
                found.append({"did": key, "name": name, "pos": pos,
                              "r": r, "scale": scale, "offset": offset,
                              "rmse": rmse, "nice": nice,
                              "formula": formula_text(name, pos, scale,
                                                      offset)})
    # Equal fits are common (two values rising together); real formulas
    # tend to use round factors and offsets, and unsigned big-endian
    # values, so prefer those.
    found.sort(key=lambda c: (-round(abs(c["r"]), 3), round(c["rmse"], 6),
                              not c["nice"],
                              abs(c["offset"] - round(c["offset"])) > 1e-6,
                              c["name"] != "u16" and c["name"] != "u8"))
    return found[:top]


def moving_bytes(rows, did_keys):
    """Bytes that change during a recording: [(did, pos, min, max,
    distinct)] sorted by number of distinct values, most first."""
    out = []
    for key in did_keys:
        datas = [r[key] for r in rows if r.get(key)]
        if len(datas) < 2:
            continue
        width = min(len(d) for d in datas)
        for pos in range(width):
            vals = [d[pos] for d in datas]
            if len(set(vals)) > 1:
                out.append((key, pos, min(vals), max(vals), len(set(vals))))
    out.sort(key=lambda m: -m[4])
    return out


def read_record(path):
    """record CSV -> (rows, did_keys); DID columns hold hex strings."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        did_keys = [c for c in fields if c.startswith("did_")]
        rows = []
        for row in reader:
            out = {}
            for k, v in row.items():
                if k in did_keys:
                    out[k] = bytes.fromhex(v) if v else None
                else:
                    try:
                        out[k] = float(v)
                    except (TypeError, ValueError):
                        out[k] = None
            rows.append(out)
    return rows, did_keys
