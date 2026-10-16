"""Battery capacity measured from charging (roadmap item B).

The Thai S05 56.1 kWh does not answer the SOH DID, so the capacity is
measured: the charge that went into the pack (current x time, F229)
divided by the SOC it gained. Done over many charges this shows whether
the pack is losing capacity.

Limits, said plainly on every page that shows the result:
- SOC is the BMS's own estimate; it is recalibrated near full, so
  charges that end at 100 % give the most trustworthy numbers
- a 1 % SOC step on a 30 % charge is already a 3 % error, so only
  charges gaining at least MIN_GAIN percent are used
"""

from . import predict

MIN_GAIN = 30.0          # % SOC a charge must add to be measured
MAX_STEP_S = 60.0        # longer gaps are not integrated over
MIN_COVERAGE = 0.9       # share of the charge time with readings
NOMINAL_CELL_V = 3.2     # LFP


def nominal_ah(capacity_kwh, cells=120):
    """Rated capacity in Ah from the rated kWh: 56.1 kWh / (120 x 3.2 V)
    = 146 Ah (the BMS answers 148 in F238, maybe the cell rating)."""
    return capacity_kwh * 1000 / (cells * NOMINAL_CELL_V)


def measure(rows):
    """One charging session (rows with _t, soc, pack_current,
    pack_voltage) -> dict, or None when it cannot be measured."""
    pts = []
    for r in rows:
        soc = predict.num(r.get("soc"))
        i = predict.num(r.get("pack_current"))
        if soc is None or i is None:
            continue
        pts.append((r["_t"], soc, i, predict.num(r.get("pack_voltage"))))
    if len(pts) < 10:
        return None
    soc0, soc1 = pts[0][1], pts[-1][1]
    gain = soc1 - soc0
    if gain < MIN_GAIN:
        return None
    ah = kwh = covered = 0.0
    for (t0, _, i0, v0), (t1, _, i1, v1) in zip(pts, pts[1:]):
        dt = (t1 - t0).total_seconds()
        if dt > MAX_STEP_S:
            continue
        covered += dt
        current = -(i0 + i1) / 2  # charging is negative on the S05
        ah += current * dt / 3600
        if v0 is not None and v1 is not None:
            kwh += current * (v0 + v1) / 2 * dt / 3.6e6
    span = (pts[-1][0] - pts[0][0]).total_seconds()
    if not span or covered / span < MIN_COVERAGE or ah <= 0:
        return None
    peak = max(-i for _, _, i, _ in pts)
    return {"time": pts[-1][0], "soc_start": soc0, "soc_end": soc1,
            "ah": ah, "kwh": kwh, "capacity_ah": ah / (gain / 100),
            "capacity_kwh": kwh / (gain / 100), "peak_a": peak,
            "kind": "DC" if peak > 40 else "AC",
            "to_full": soc1 >= 99}


def charge_rows(folder):
    """Rows from autopilot drive_*.csv and `charge` charge_*.csv files."""
    return predict.read_drive(folder, ("drive_*.csv", "charge_*.csv"),
                              days=3650, cell_every=10 ** 9)


def measurements(folder):
    out = []
    for kind, s in predict.sessions(charge_rows(folder)):
        if kind != "charge":
            continue
        m = measure(s)
        if m:
            out.append(m)
    return out


def check_soh(folder, capacity_kwh=56.1, cells=120):
    """A predict.Finding for /health."""
    rated = nominal_ah(capacity_kwh, cells)
    ms = measurements(folder)
    part = "ความจุแบตจริง (SOH วัดเอง)"
    advice = ("วัดจากกระแสที่เข้าแบตระหว่างชาร์จ ÷ SOC ที่เพิ่มขึ้น เทียบกับ %.0f Ah"
              " (%.1f kWh) · แม่นที่สุดเมื่อชาร์จจาก SOC ต่ำจนเต็ม 100%%"
              % (rated, capacity_kwh))
    if not ms:
        return predict.Finding(
            "soh_measured", part, predict.NODATA,
            "ยังไม่มีการชาร์จที่วัดได้ (ต้องชาร์จเพิ่มอย่างน้อย %d%% ในครั้งเดียว"
            " ขณะ Pi บันทึกอยู่)" % MIN_GAIN, advice)
    pts = [(m["time"], m["capacity_ah"] / rated * 100) for m in ms]
    f = predict.trend_finding("soh_measured", part, pts, 80, False, "%",
                              "%.1f", advice)
    last = ms[-1]
    f.summary = "ครั้งล่าสุด %.1f%% (%.0f Ah, ชาร์จ %s %.0f→%.0f%%) · %s" % (
        pts[-1][1], last["capacity_ah"], last["kind"], last["soc_start"],
        last["soc_end"], f.summary.split(" · ", 1)[-1])
    return f


def report(folder, capacity_kwh=56.1, cells=120):
    """Text table for the `soh` command."""
    rated = nominal_ah(capacity_kwh, cells)
    ms = measurements(folder)
    lines = ["%-16s %4s %9s %7s %8s %7s %6s" % (
        "ชาร์จเสร็จ", "แบบ", "SOC", "Ah เข้า", "ความจุ Ah", "kWh*", "SOH")]
    for m in ms:
        lines.append("%-16s %4s %4.0f→%-4.0f %7.1f %8.1f %7.1f %5.1f%%%s" % (
            m["time"].strftime("%Y-%m-%d %H:%M"), m["kind"], m["soc_start"],
            m["soc_end"], m["ah"], m["capacity_ah"], m["capacity_kwh"],
            m["capacity_ah"] / rated * 100, " ✓เต็ม" if m["to_full"] else ""))
    return ms, rated, "\n".join(lines)

