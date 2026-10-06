"""User-defined signals loaded from a JSON file (--signals), so DIDs found
with discover/sniff can be used without editing the code.

{
  "signals": [
    {"key": "obc_temp", "label": "อุณหภูมิ OBC", "header": "761",
     "did": "F2C1", "formula": "A-40", "unit": "C"}
  ],
  "arrays": [
    {"key": "cells", "label": "แรงดันรายเซลล์", "header": "7A1",
     "did": "F2A0", "offset": 0, "count": 108, "bytes": 2,
     "scale": 0.001, "add": 0, "unit": "V"}
  ]
}

Formulas use A, B, C ... for the data bytes after the DID (like Car
Scanner/Torque) and CAP for the pack capacity in kWh. Arrays named "cells"
(cell voltages) and "temps" (temperature sensors) are drawn by the
dashboard.
"""

import ast
import json
import operator
from dataclasses import dataclass

from . import pids

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_,
    ast.LShift: operator.lshift, ast.RShift: operator.rshift,
}


class ConfigError(Exception):
    pass


def compile_formula(text):
    """Validate a formula and return (tree, number_of_bytes_used)."""
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise ConfigError("สูตรผิด %r: %s" % (text, e))
    used = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            name = node.id
            if name == "CAP":
                continue
            if len(name) != 1 or not "A" <= name <= "Z":
                raise ConfigError("สูตร %r ใช้ชื่อ %s ไม่ได้" % (text, name))
            used = max(used, ord(name) - ord("A") + 1)
        elif not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp,
                                   ast.Constant, ast.Load, ast.USub,
                                   ast.UAdd, *_BINOPS)):
            raise ConfigError("สูตร %r มีส่วนที่ไม่รองรับ" % text)
        if isinstance(node, ast.Constant) and \
                not isinstance(node.value, (int, float)):
            raise ConfigError("สูตร %r มีค่าที่ไม่ใช่ตัวเลข" % text)
    return tree, used


def evaluate(tree, data, capacity):
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "CAP":
                return capacity
            return data[ord(node.id) - ord("A")]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
    return ev(tree)


def _did(value):
    did = int(str(value), 16)
    if not 0 <= did <= 0xFFFF:
        raise ConfigError("DID %r ไม่ถูกต้อง" % value)
    return did


def _header(value):
    header = str(value).upper()
    if len(header) != 3 or int(header, 16) > 0x7F7:
        raise ConfigError("header %r ต้องเป็น CAN id 11 บิต เช่น 7A1" % value)
    return header


def make_signal(item):
    tree, used = compile_formula(item["formula"])
    return pids.Signal(
        key=item["key"], label=item.get("label", item["key"]),
        did=_did(item["did"]), unit=item.get("unit", ""), min_len=used,
        status=pids.CUSTOM,
        decode=lambda d, c, t=tree: evaluate(t, d, c),
        note=item.get("note", ""), header=_header(item["header"]))


@dataclass(frozen=True)
class ArraySignal:
    """A block of equally sized unsigned values inside one DID response."""
    key: str
    label: str
    header: str
    did: int
    offset: int
    count: int
    width: int
    scale: float
    add: float
    unit: str

    def decode(self, data):
        values = []
        for i in range(self.count):
            pos = self.offset + i * self.width
            chunk = data[pos:pos + self.width]
            if len(chunk) < self.width:
                break
            values.append(int.from_bytes(chunk, "big") * self.scale +
                          self.add)
        return values


def make_array(item):
    width = int(item.get("bytes", 2))
    if width not in (1, 2, 3, 4):
        raise ConfigError("bytes ต้องเป็น 1-4")
    return ArraySignal(
        key=item["key"], label=item.get("label", item["key"]),
        header=_header(item["header"]), did=_did(item["did"]),
        offset=int(item.get("offset", 0)), count=int(item["count"]),
        width=width, scale=float(item.get("scale", 1)),
        add=float(item.get("add", 0)), unit=item.get("unit", ""))


def load(path):
    """Return (signals, arrays) from a JSON file; ([], []) for None."""
    if not path:
        return [], []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        signals = [make_signal(s) for s in data.get("signals", [])]
        arrays = [make_array(a) for a in data.get("arrays", [])]
    except (OSError, ValueError, KeyError) as e:
        raise ConfigError("อ่านไฟล์ %s ไม่ได้: %s" % (path, e))
    keys = [s.key for s in signals] + [a.key for a in arrays]
    clash = set(keys) & set(pids.SIGNALS_BY_KEY) or \
        {k for k in keys if keys.count(k) > 1}
    if clash:
        raise ConfigError("ชื่อ key ซ้ำ: %s" % ", ".join(sorted(clash)))
    return signals, arrays
