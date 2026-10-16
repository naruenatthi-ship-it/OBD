"""One round of readings: built-in signals, user signals, arrays and the
12 V battery voltage."""

import csv
import datetime
import time
from dataclasses import dataclass, field

from . import pids
from .elm327 import ElmError, NegativeResponse, NoData


def read_signals(elm, signals, capacity):
    """Read each DID once and decode every signal that uses it.
    Returns {key: (raw_bytes_or_None, value_or_None, error_or_None)}."""
    cache = {}
    results = {}
    for sig in signals:
        ident = (sig.header, sig.did)
        if ident not in cache:
            try:
                cache[ident] = (elm.read_did(sig.header, sig.did), None)
            except NegativeResponse as e:
                cache[ident] = (None, str(e))
            except NoData as e:
                cache[ident] = (None, "ไม่ตอบ (%s)" % e)
        raw, err = cache[ident]
        value = None
        if raw is not None:
            try:
                value = sig.value(raw, capacity)
            except (ArithmeticError, IndexError) as e:
                err = "คำนวณสูตรไม่ได้ (%s)" % e
            if value is None and err is None and sig.decode is not None:
                err = "ข้อมูลสั้นเกินไป (%d ไบต์)" % len(raw)
        results[sig.key] = (raw, value, err)
    return results


class GroupCache:
    """Grouped signals (one DID per cell: 120+ requests) are read at most
    every `period` seconds in continuous modes; rounds in between reuse the
    last values so pack-level values keep updating quickly."""

    def __init__(self, period=30.0, clock=time.monotonic):
        self.period, self.clock = period, clock
        self.last = None
        self.results = {}

    def read(self, elm, signals, capacity):
        if self.last is None or self.clock() - self.last >= self.period:
            self.results = read_signals(elm, signals, capacity)
            self.last = self.clock()
        return self.results


def array_summary(key, values):
    """min/max/delta of an array, with 1-based positions."""
    if not values:
        return {}
    lo = min(range(len(values)), key=values.__getitem__)
    hi = max(range(len(values)), key=values.__getitem__)
    out = {
        key + "_min": values[lo], key + "_min_no": lo + 1,
        key + "_max": values[hi], key + "_max_no": hi + 1,
    }
    if key == "cells":
        out["cells_delta_mv"] = (values[hi] - values[lo]) * 1000
    else:
        out[key + "_delta"] = values[hi] - values[lo]
    return out


@dataclass
class Snapshot:
    time: float
    values: dict
    arrays: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def apply_groups(values, errors, raw, groups):
    """Turn grouped signals (cells, temperature sensors) into arrays: the
    members leave `values`, the array and its min/max/delta take their
    place, and pack-level max/min are filled in when no direct signal
    gave them."""
    arrays = {}
    for g in groups:
        arr = g.collect(values)
        for k in g.members:
            values.pop(k, None)
            errors.pop(k, None)
            raw.pop(k, None)
        if not arr:
            continue
        arrays[g.key] = arr
        values.update(array_summary(g.key, arr))
        if g.key == "cells":
            values.setdefault("cell_v_max", max(arr))
            values.setdefault("cell_v_min", min(arr))
        elif g.key == "temps":
            values.setdefault("batt_temp_max", max(arr))
            values.setdefault("batt_temp_min", min(arr))
    return arrays


def take(elm, signals, arrays=(), capacity=pids.DEFAULT_CAPACITY_KWH,
         read_12v=True, slow=None):
    """`arrays` holds ArraySignal (one DID, many values) and GroupArray
    (many signals gathered into one array) objects. With `slow` (a
    GroupCache) the group members are read only when it is due."""
    groups = [a for a in arrays if hasattr(a, "members")]
    did_arrays = [a for a in arrays if not hasattr(a, "members")]
    if slow is not None and groups:
        members = {k for g in groups for k in g.members}
        results = read_signals(elm, [s for s in signals
                                     if s.key not in members], capacity)
        results.update(slow.read(elm, [s for s in signals
                                       if s.key in members], capacity))
    else:
        results = read_signals(elm, signals, capacity)
    values = {k: v for k, (_, v, _) in results.items()}
    errors = {k: e for k, (_, _, e) in results.items() if e}
    raw = {k: r for k, (r, _, _) in results.items() if r is not None}
    arr_values = apply_groups(values, errors, raw, groups)
    values.update(pids.derived(values))
    if read_12v:
        try:
            values["aux_12v"] = elm.battery_voltage()
        except ElmError as e:
            errors["aux_12v"] = str(e)
    for arr in did_arrays:
        try:
            data = elm.read_did(arr.header, arr.did)
        except (NoData, NegativeResponse) as e:
            errors[arr.key] = str(e)
            continue
        arr_values[arr.key] = arr.decode(data)
        values.update(array_summary(arr.key, arr_values[arr.key]))
    return Snapshot(time.time(), values, arr_values, errors, raw)


class CsvLog:
    """Append snapshots to a CSV file. A value that the file has no column
    for yet (a newer program version, or cells read for the first time)
    adds the column by rewriting the file once."""

    def __init__(self, path, label=""):
        self.path = path
        self.label = label
        self.file = None
        self.writer = None
        self.start = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.file:
            self.file.close()
        return False

    def write(self, snap):
        if self.start is None:
            self.start = snap.time
        row = {"time": datetime.datetime.fromtimestamp(snap.time)
               .isoformat(timespec="seconds"),
               "elapsed_s": round(snap.time - self.start, 1),
               "label": self.label}
        row.update({k: v for k, v in snap.values.items() if v is not None})
        for key, values in snap.arrays.items():
            for i, value in enumerate(values, 1):
                row["%s_%d" % (key, i)] = round(value, 4)
        if self.writer is None:
            self._open(list(row))
        missing = [k for k in row if k not in self.writer.fieldnames]
        if missing:
            self._add_columns(missing)
        self.writer.writerow(row)
        self.file.flush()

    def _add_columns(self, missing):
        self.file.close()
        with open(self.path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        fields = list(self.writer.fieldnames) + missing
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fields, restval="")
            w.writeheader()
            w.writerows(rows)
        self.file = open(self.path, "a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.file, fields, restval="",
                                     extrasaction="ignore")

    def _open(self, fields):
        header = None
        try:
            with open(self.path, newline="", encoding="utf-8") as f:
                header = next(csv.reader(f), None)
        except OSError:
            pass
        self.file = open(self.path, "a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.file, header or fields,
                                     restval="", extrasaction="ignore")
        if not header:
            self.writer.writeheader()
