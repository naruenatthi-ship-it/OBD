"""Warning rules checked against each round of readings, and optional push
notifications through ntfy (https://ntfy.sh).

The default limits are rough starting points chosen from general LFP / 12 V
practice, not Deepal specifications. Override or add rules in the
--signals file:

  "alerts": [
    {"key": "obc_temp", "label": "อุณหภูมิ OBC", "unit": "C",
     "warn_above": 70, "critical_above": 85}
  ]
"""

import csv
import statistics
import time
import urllib.request
from dataclasses import dataclass, replace

OK, INFO, WARN, CRIT = 0, 1, 2, 3
LEVEL_ICON = {OK: "✅", INFO: "ℹ️", WARN: "⚠️", CRIT: "🔴"}
LEVEL_TEXT = {OK: "ปกติ", INFO: "ข้อมูล", WARN: "ควรเฝ้าดู",
              CRIT: "ควรให้ช่างตรวจ"}


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    unit: str = ""
    warn_above: float = None
    critical_above: float = None
    warn_below: float = None
    critical_below: float = None
    requires: str = None  # only check when this value was read too
    advice: str = ""


@dataclass(frozen=True)
class Alert:
    level: int
    key: str
    label: str
    value: float
    unit: str
    message: str

    def text(self):
        return "%s %s" % (LEVEL_ICON[self.level], self.message)


DEFAULT_RULES = [
    Rule("aux_12v", "แบต 12V", "V", warn_below=13.0, critical_below=12.0,
         requires="soc",
         advice="ตอนรถ READY ควรสูงกว่า 13.2 V ถ้าต่ำ DC-DC อาจไม่ชาร์จหรือแบต"
                " 12V มีปัญหา (ถ้ารถไม่ได้ READY ค่าต่ำได้ตามปกติ)"),
    Rule("batt_temp_max", "อุณหภูมิแบตสูงสุด", "°C", warn_above=45,
         critical_above=55, advice="แบตร้อน ลดการชาร์จเร็ว/ขับหนัก และเช็กระบบหล่อเย็น"),
    Rule("temp_delta_c", "อุณหภูมิในแพ็กต่างกัน", "°C", warn_above=8,
         critical_above=15,
         advice="ความร้อนในแพ็กไม่สม่ำเสมอ อาจเป็นระบบหล่อเย็น (ปั๊ม/น้ำยา)"),
    Rule("charge_temp", "อุณหภูมิหัวชาร์จ", "°C", warn_above=65,
         critical_above=80,
         advice="หัวชาร์จร้อน เช็กหัวชาร์จ/ปลั๊กว่าหลวมหรือสกปรก"
                " (สูตรค่านี้ยังรอยืนยัน)"),
    Rule("delta_mv", "ส่วนต่างแรงดันเซลล์", "mV", warn_above=100,
         critical_above=200,
         advice="เซลล์ไม่สมดุล ลองชาร์จ AC ให้เต็มแล้วเสียบทิ้งไว้ (คำสั่ง balance)"),
    Rule("cell_v_min", "แรงดันเซลล์ต่ำสุด", "V", warn_below=2.95,
         critical_below=2.8, advice="มีเซลล์แรงดันต่ำมาก ควรชาร์จและตรวจสอบ"),
    Rule("cell_v_max", "แรงดันเซลล์สูงสุด", "V", warn_above=3.65,
         critical_above=3.7, advice="มีเซลล์แรงดันสูงเกินช่วงปกติของ LFP"),
    Rule("soh", "SOH", "%", warn_below=85, critical_below=75,
         advice="สุขภาพแบตลดลง (สูตร SOH ยังรอยืนยัน)"),
    # needs an "insulation_kohm" signal in the --signals file
    Rule("insulation_ohm_per_v", "ความต้านทานฉนวนไฟแรงสูง", "Ω/V",
         warn_below=500, critical_below=100,
         advice="ฉนวนระบบไฟแรงสูงต่ำ เสี่ยงไฟรั่ว ควรให้ศูนย์ตรวจ"
                " (มาตรฐานขั้นต่ำ 100 Ω/V)"),
    # compared with this car's own usual value, see add_baselines()
    Rule("insulation_pct_of_normal", "ค่าฉนวนเทียบกับปกติของรถคันนี้", "%",
         warn_below=50,
         advice="ค่าฉนวนตกลงมากจากที่เคยวัดได้ อาจมีความชื้นเข้าระบบไฟแรงสูง"
                " ถ้าตกเฉพาะหลังฝนตกหรือล้างรถยิ่งน่าสงสัย ควรให้ศูนย์ตรวจ"),
]

# value -> derived key holding it as a percentage of the car's usual value
BASELINE_KEYS = {"insulation_ohm_per_v": "insulation_pct_of_normal"}


def load_baselines(path, min_rows=3):
    """The car's usual values: medians over earlier checkups in
    checkup_history.csv, for keys with at least `min_rows` readings."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return {}
    out = {}
    for key in BASELINE_KEYS:
        nums = []
        for row in rows:
            try:
                nums.append(float(row.get(key)))
            except (TypeError, ValueError):
                pass
        if len(nums) >= min_rows:
            out[key] = statistics.median(nums)
    return out


def add_baselines(values, baselines):
    """A copy of `values` with each baseline key also given as a
    percentage of its usual value."""
    out = dict(values)
    for key, pct_key in BASELINE_KEYS.items():
        v, usual = values.get(key), baselines.get(key)
        if isinstance(v, (int, float)) and usual:
            out[pct_key] = v / usual * 100
    return out


def merge_rules(defaults, overrides):
    """Overrides with the key of a default rule replace its given fields;
    other overrides are added as new rules."""
    rules = {r.key: r for r in defaults}
    for item in overrides:
        fields = {k: item[k] for k in (
            "label", "unit", "warn_above", "critical_above", "warn_below",
            "critical_below", "requires", "advice") if k in item}
        if item["key"] in rules:
            rules[item["key"]] = replace(rules[item["key"]], **fields)
        else:
            fields.setdefault("label", item["key"])
            rules[item["key"]] = Rule(key=item["key"], **fields)
    return list(rules.values())


def _fmt(v):
    return ("%.3f" % v).rstrip("0").rstrip(".") if isinstance(v, float) \
        else str(v)


def evaluate(values, rules):
    """Return alerts for every rule whose limit is crossed, worst first."""
    out = []
    for r in rules:
        v = values.get(r.key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or \
                (r.requires and values.get(r.requires) is None):
            continue
        level, limit, side = OK, None, ""
        for lvl, lim, s in ((CRIT, r.critical_above, ">"),
                            (WARN, r.warn_above, ">")):
            if lim is not None and v > lim:
                level, limit, side = lvl, lim, s
                break
        if level == OK:
            for lvl, lim, s in ((CRIT, r.critical_below, "<"),
                                (WARN, r.warn_below, "<")):
                if lim is not None and v < lim:
                    level, limit, side = lvl, lim, s
                    break
        if level == OK:
            continue
        msg = "%s %s %s (เกณฑ์ %s %s)" % (r.label, _fmt(v), r.unit, side,
                                        _fmt(limit))
        if r.advice:
            msg += " - " + r.advice
        out.append(Alert(level, r.key, r.label, v, r.unit, msg))
    out.sort(key=lambda a: -a.level)
    return out


def ntfy_sender(server, topic):
    url = server.rstrip("/") + "/" + topic

    def send(title, message, priority):
        req = urllib.request.Request(
            url, data=message.encode("utf-8"), method="POST",
            headers={"Title": title, "Priority": priority,
                     "Tags": "car"})
        urllib.request.urlopen(req, timeout=10).close()
    return send


class Notifier:
    """Sends an alert when it is new, gets worse, or is still there after
    `cooldown` seconds. `send(title, message, priority)` does the delivery."""

    def __init__(self, send, cooldown=900, clock=time.time,
                 name="Deepal S05"):
        self.send = send
        self.name = name  # default notification title
        self.cooldown = cooldown
        self.clock = clock
        self.last = {}  # key -> (level, time)
        self.error = None

    def notify(self, alerts, title=None):
        title = title or self.name
        now = self.clock()
        due = []
        for a in alerts:
            if a.level < WARN:
                continue
            prev = self.last.get(a.key)
            if prev is None or a.level > prev[0] or \
                    now - prev[1] >= self.cooldown:
                due.append(a)
        active = {a.key for a in alerts}
        for key in list(self.last):
            if key not in active:
                del self.last[key]  # cleared: notify again if it returns
        if not due:
            return []
        priority = "high" if any(a.level == CRIT for a in due) else "default"
        try:
            self.send(title, "\n".join(a.text() for a in due), priority)
        except Exception as e:  # network trouble must not stop logging
            self.error = str(e)
            return []
        self.error = None
        for a in due:
            self.last[a.key] = (a.level, now)
        return due
