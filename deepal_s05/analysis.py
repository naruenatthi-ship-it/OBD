"""Offline helpers: spot cell-voltage-like data and summarise sniffed
diagnostic traffic (ISO-TP over 11-bit CAN)."""

import re

# UDS request service ids and their names (requests 0x10-0x3E)
SERVICE_NAMES = {
    0x10: "DiagnosticSessionControl",
    0x11: "ECUReset",
    0x14: "ClearDiagnosticInformation",
    0x19: "ReadDTCInformation",
    0x22: "ReadDataByIdentifier",
    0x23: "ReadMemoryByAddress",
    0x27: "SecurityAccess",
    0x28: "CommunicationControl",
    0x2E: "WriteDataByIdentifier",
    0x2F: "InputOutputControl",
    0x31: "RoutineControl",
    0x34: "RequestDownload",
    0x3E: "TesterPresent",
    0x85: "ControlDTCSetting",
}


def find_cell_run(data, lo_mv=2500, hi_mv=4300, min_run=8, max_spread_mv=400):
    """Longest run of big-endian 16-bit values that look like cell voltages
    in mV. Returns dict(offset, count, min_v, max_v) or None."""
    best = None
    for align in (0, 1):
        run_start, values = None, []
        for pos in range(align, len(data) - 1, 2):
            v = data[pos] << 8 | data[pos + 1]
            if lo_mv <= v <= hi_mv:
                if run_start is None:
                    run_start, values = pos, []
                values.append(v)
                continue
            best = _better(best, run_start, values, min_run, max_spread_mv)
            run_start, values = None, []
        best = _better(best, run_start, values, min_run, max_spread_mv)
    return best


def _better(best, start, values, min_run, max_spread_mv):
    if start is None or len(values) < min_run:
        return best
    if max(values) - min(values) > max_spread_mv:
        return best
    if best and best["count"] >= len(values):
        return best
    return {"offset": start, "count": len(values),
            "min_v": min(values) / 1000, "max_v": max(values) / 1000}


def describe_cell_run(run):
    return "อาจเป็นแรงดันรายเซลล์: %d ค่า %.3f-%.3f V (เริ่มไบต์ที่ %d)" % (
        run["count"], run["min_v"], run["max_v"], run["offset"])


LINE_RE = re.compile(r"^([0-9A-F]{3})((?:[0-9A-F]{2}){1,8})$")


def parse_sniff_line(line):
    """'7A1 03 22 F2 2F 00 00 00 00' (ATCAF0, ATH1) -> ('7A1', bytes)."""
    compact = line.replace(" ", "").upper()
    m = LINE_RE.match(compact)
    if not m:
        return None
    return m.group(1), bytes.fromhex(m.group(2))


def reassemble(frames):
    """Join raw ISO-TP frames [(time, can_id, data)] into messages
    [(time, can_id, payload)]. Flow-control frames are dropped."""
    pending = {}
    out = []
    for t, can_id, data in frames:
        if not data:
            continue
        kind = data[0] >> 4
        if kind == 0:
            length = data[0] & 0x0F
            if 0 < length <= len(data) - 1:
                out.append((t, can_id, bytes(data[1:1 + length])))
        elif kind == 1 and len(data) >= 2:
            length = ((data[0] & 0x0F) << 8) | data[1]
            pending[can_id] = [t, length, bytearray(data[2:])]
        elif kind == 2 and can_id in pending:
            entry = pending[can_id]
            entry[2].extend(data[1:])
            if len(entry[2]) >= entry[1]:
                out.append((entry[0], can_id, bytes(entry[2][:entry[1]])))
                del pending[can_id]
    return out


def is_request(payload):
    sid = payload[0]
    return 0x01 <= sid <= 0x0A or 0x10 <= sid <= 0x3E or sid == 0x85


def request_key(payload):
    """Requests are grouped by service plus identifier where there is one."""
    sid = payload[0]
    if sid in (0x22, 0x2E, 0x2F) and len(payload) >= 3:
        return payload[:3]
    if sid in (0x10, 0x11, 0x19, 0x27, 0x28, 0x3E, 0x85) and len(payload) >= 2:
        return payload[:2]
    if sid == 0x31 and len(payload) >= 4:
        return payload[:4]
    return payload[:1]


def summarise(messages):
    """Pair each request with the next response from request id + 8 and
    group them. Returns a list of dicts sorted by request id and key."""
    groups = {}
    waiting = {}  # response id -> group of the last request
    for t, can_id, payload in messages:
        if not payload:
            continue
        if is_request(payload):
            key = (can_id, request_key(payload))
            g = groups.setdefault(key, {
                "request_id": can_id, "request": payload,
                "service": SERVICE_NAMES.get(payload[0], "0x%02X" % payload[0]),
                "count": 0, "response_id": None, "responses": [],
            })
            g["count"] += 1
            rx = "%03X" % (int(can_id, 16) + 8)
            waiting[rx] = g
        elif can_id in waiting:
            g = waiting.pop(can_id)
            g["response_id"] = can_id
            g["responses"].append(payload)
    result = list(groups.values())
    for g in result:
        last = g["responses"][-1] if g["responses"] else b""
        g["last_response"] = last
        g["cell_run"] = find_cell_run(last) if len(last) > 16 else None
        g["changes"] = len(set(g["responses"])) > 1
    result.sort(key=lambda g: (g["request_id"], g["request"]))
    return result


def read_sniff_log(path):
    """Read a log written by the sniff command: '<seconds>\\t<line>'."""
    frames = []
    with open(path, encoding="utf-8") as f:
        for row in f:
            if row.startswith("#"):
                continue
            t, _, line = row.rstrip("\n").partition("\t")
            parsed = parse_sniff_line(line or t)
            if parsed:
                try:
                    ts = float(t) if line else 0.0
                except ValueError:
                    ts = 0.0
                frames.append((ts, parsed[0], parsed[1]))
    return frames


def linear_fit(xs, ys):
    """Least-squares fit y = a + b*x. Returns (a, b, r2) or None."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return a, b, r2


def estimate_ir(currents, voltages, min_range_a=20.0):
    """Internal resistance from how voltage moves with current: the slope of
    V against I. The current sign convention does not matter because the
    magnitude of the slope is used. Returns a dict with r_mohm (None when the
    current did not vary enough), r2, n and i_range."""
    i_range = max(currents) - min(currents) if currents else 0.0
    out = {"n": len(currents), "i_range": i_range, "r_mohm": None,
           "r2": None}
    if i_range < min_range_a:
        return out
    fit = linear_fit(currents, voltages)
    if fit is None:
        return out
    out["r_mohm"] = abs(fit[1]) * 1000
    out["r2"] = fit[2]
    return out


def balance_status(history, window_s=1800, stable_mv=1.0, full_soc=99):
    """Judge balancing from [(time, soc, delta_mv)] samples, oldest first.

    Returns (state, text) where state is one of: no_data, not_full,
    collecting, improving, rising, stable."""
    usable = [(t, s, d) for t, s, d in history
              if d is not None and s is not None]
    if not usable:
        return "no_data", "ยังไม่มีข้อมูลส่วนต่างแรงดันเซลล์"
    t_now, soc_now, d_now = usable[-1]
    if soc_now < full_soc:
        return "not_full", ("SOC %.0f%% ยังไม่เต็ม BMS จะบาลานซ์ได้ดีตอนใกล้ 100%%"
                            % soc_now)
    full = [x for x in usable if x[1] >= full_soc]
    if t_now - full[0][0] < window_s:
        return "collecting", ("เต็มแล้ว กำลังเก็บข้อมูล (%d/%d นาที) ส่วนต่างตอนนี้ %.1f mV"
                              % ((t_now - full[0][0]) // 60, window_s // 60,
                                 d_now))
    past = [x for x in full if x[0] <= t_now - window_s][-1]
    change = d_now - past[2]
    minutes = (t_now - past[0]) / 60
    if abs(change) <= stable_mv:
        return "stable", ("ส่วนต่างนิ่งแล้วที่ %.1f mV (เปลี่ยนไม่เกิน %.1f mV ใน %.0f นาที)"
                          % (d_now, stable_mv, minutes))
    if change < 0:
        return "improving", ("ส่วนต่างลดลง %.1f mV ใน %.0f นาที (ตอนนี้ %.1f mV) BMS กำลังบาลานซ์"
                             % (-change, minutes, d_now))
    return "rising", ("ส่วนต่างเพิ่มขึ้น %.1f mV ใน %.0f นาที (ตอนนี้ %.1f mV)"
                      % (change, minutes, d_now))


CHARGING_V = 13.2  # above this the DC-DC converter is charging the 12 V


def aux12v_state(v):
    """Rough reading of a 12 V lead-acid battery voltage."""
    if v >= CHARGING_V:
        return "DC-DC กำลังชาร์จแบต 12V (รถตื่นอยู่)"
    if v >= 12.6:
        return "เต็ม"
    if v >= 12.4:
        return "ค่อนข้างดี"
    if v >= 12.2:
        return "ปานกลาง"
    return "ต่ำ ควรตรวจแบต 12V"


def aux12v_summary(readings):
    """[(time, volts)] -> min/max, times the DC-DC switched on, and the
    resting voltage trend in V per hour (readings below CHARGING_V)."""
    if not readings:
        return None
    volts = [v for _, v in readings]
    wakes = sum(1 for (_, a), (_, b) in zip(readings, readings[1:])
                if a < CHARGING_V <= b)
    rest = [(t, v) for t, v in readings if v < CHARGING_V]
    trend = None
    if len(rest) >= 3 and rest[-1][0] - rest[0][0] >= 600:
        fit = linear_fit([(t - rest[0][0]) / 3600 for t, _ in rest],
                         [v for _, v in rest])
        if fit:
            trend = fit[1]
    return {"min": min(volts), "max": max(volts), "wakes": wakes,
            "trend_v_per_h": trend, "n": len(readings),
            "hours": (readings[-1][0] - readings[0][0]) / 3600}
