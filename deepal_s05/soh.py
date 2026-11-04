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
MIN_DROP = 20.0          # % SOC drives must use (counted only while
                         # recording, so parked drain is left out)
MAX_DRIVE_JOIN_S = 14 * 86400  # drives between two charges are joined
MAX_STEP_S = 60.0        # longer gaps are not integrated over
MAX_JOIN_S = 12 * 3600   # pieces of one charge may be this far apart
NOMINAL_CELL_V = 3.2     # LFP


def nominal_ah(capacity_kwh, cells=120):
    """Rated capacity in Ah from the rated kWh: 56.1 kWh / (120 x 3.2 V)
    = 146 Ah (the BMS answers 148 in F238, maybe the cell rating)."""
    return capacity_kwh * 1000 / (cells * NOMINAL_CELL_V)


def measure(rows, discharge=False):
    """One charge (rows with _t, soc, pack_current, pack_voltage, possibly
    from several recordings with gaps) -> dict, or None when it cannot be
    measured. With discharge=True the rows are drives instead: the charge
    that came out of the pack divided by the SOC it lost (EXPERIMENTAL).

    The Pi only records while the car is awake and may lose power when the
    car shuts itself off in the middle of a charge. Only stretches with
    readings less than MAX_STEP_S apart are used: the charge that went in
    during such a stretch is divided by the SOC it gained, summed over all
    stretches. Time with no readings counts for neither side.

    The BMS reports whole percents, so each stretch is cut to run from its
    first SOC step to its last: otherwise every stretch could be up to 1 %
    off, which adds up over the many short stretches of a week of drives."""
    sign = 1 if discharge else -1   # charging is negative on the S05
    pts = []
    for r in rows:
        soc = predict.num(r.get("soc"))
        i = predict.num(r.get("pack_current"))
        if soc is None or i is None:
            continue
        pts.append((r["_t"], soc, i, predict.num(r.get("pack_voltage"))))
    if len(pts) < 10:
        return None
    stretches, cur = [], [pts[0]]
    for prev, p in zip(pts, pts[1:]):
        dt = (p[0] - prev[0]).total_seconds()
        if dt > MAX_STEP_S or dt < 0:
            stretches.append(cur)
            cur = []
        cur.append(p)
    stretches.append(cur)
    ah = kwh = gain = 0.0
    blocks = 0
    for st in stretches:
        steps = [k for k in range(1, len(st)) if st[k][1] != st[k - 1][1]]
        if len(steps) < 2:
            continue
        st = st[steps[0]:steps[-1] + 1]
        blocks += 1
        for (t0, s0, i0, v0), (t1, s1, i1, v1) in zip(st, st[1:]):
            dt = (t1 - t0).total_seconds()
            current = sign * (i0 + i1) / 2
            ah += current * dt / 3600
            if v0 is not None and v1 is not None:
                kwh += current * (v0 + v1) / 2 * dt / 3.6e6
        gain += -sign * (st[-1][1] - st[0][1])
    soc0, soc1 = pts[0][1], pts[-1][1]
    if gain < (MIN_DROP if discharge else MIN_GAIN) or ah <= 0:
        return None
    peak = max(sign * i for _, _, i, _ in pts)
    kind = "ขับ" if discharge else ("DC" if peak > 40 else "AC")
    return {"time": pts[-1][0], "soc_start": soc0, "soc_end": soc1,
            "gain": gain, "blocks": blocks,
            "ah": ah, "kwh": kwh, "capacity_ah": ah / (gain / 100),
            "capacity_kwh": kwh / (gain / 100), "peak_a": peak,
            "kind": kind, "to_full": not discharge and soc1 >= 99}


def charge_events(sessions):
    """Group the `charge` sessions of predict.sessions into charges: the
    pieces of one charge are separated by gaps (the car slept or the Pi
    lost power) but the SOC only rises across them and no drive comes
    between."""
    events, cur = [], None
    for kind, rows in sessions:
        if kind == "idle":
            continue
        if kind != "charge":
            cur = None
            continue
        if cur is not None:
            gap = (rows[0]["_t"] - cur[-1]["_t"]).total_seconds()
            before = predict.num(cur[-1].get("soc"))
            after = predict.num(rows[0].get("soc"))
            if gap <= MAX_JOIN_S and before is not None and \
                    after is not None and after >= before - 1:
                cur.extend(rows)
                continue
        cur = list(rows)
        events.append(cur)
    return events


def drive_events(sessions):
    """Group the drives (and the times the car was on but parked) between
    two charges: one event ends at a charge session, or where the SOC is
    higher than when the last recording stopped (charged without the Pi
    recording)."""
    events, cur = [], None
    for kind, rows in sessions:
        if kind == "charge":
            cur = None
            continue
        if cur is not None:
            gap = (rows[0]["_t"] - cur[-1]["_t"]).total_seconds()
            before = predict.num(cur[-1].get("soc"))
            after = predict.num(rows[0].get("soc"))
            if gap <= MAX_DRIVE_JOIN_S and before is not None and \
                    after is not None and after <= before + 1:
                cur.extend(rows)
                continue
        if kind != "drive":
            cur = None   # an event starts with a drive
            continue
        cur = list(rows)
        events.append(cur)
    return events


def charge_rows(folder):
    """Rows from autopilot drive_*.csv and `charge` charge_*.csv files."""
    return predict.read_drive(folder, ("drive_*.csv", "charge_*.csv"),
                              days=3650, cell_every=10 ** 9)


def measurements(folder):
    """Charges and drives that could be measured, oldest first."""
    sessions = predict.sessions(charge_rows(folder))
    out = [m for m in (measure(s) for s in charge_events(sessions)) if m]
    out += [m for m in (measure(s, discharge=True)
                        for s in drive_events(sessions)) if m]
    return sorted(out, key=lambda m: m["time"])


def check_soh(folder, capacity_kwh=56.1, cells=120):
    """A predict.Finding for /health."""
    rated = nominal_ah(capacity_kwh, cells)
    ms = measurements(folder)
    part = "ความจุแบตจริง (SOH วัดเอง)"
    advice = ("วัดจากกระแสที่เข้าแบตระหว่างชาร์จ ÷ SOC ที่เพิ่มขึ้น หรือกระแสที่ออก"
              "ระหว่างขับ ÷ SOC ที่ลดลง (หลายทริปรวมกันได้, EXPERIMENTAL)"
              " เทียบกับ %.0f Ah (%.1f kWh) · แม่นที่สุดเมื่อชาร์จจาก SOC ต่ำจนเต็ม 100%%"
              % (rated, capacity_kwh))
    if not ms:
        return predict.Finding(
            "soh_measured", part, predict.NODATA,
            "ยังวัดไม่ได้: ต้องขับให้ SOC ลดรวมกันอย่างน้อย %d%% ก่อนชาร์จครั้งถัดไป"
            " (Pi บันทึกเองตอนขับ) หรือชาร์จเพิ่ม %d%% ขณะ Pi บันทึกอยู่"
            % (MIN_DROP, MIN_GAIN), advice)
    pts = [(m["time"], m["capacity_ah"] / rated * 100) for m in ms]
    f = predict.trend_finding("soh_measured", part, pts, 80, False, "%",
                              "%.1f", advice)
    last = ms[-1]
    f.summary = "ครั้งล่าสุด %.1f%% (%.0f Ah, %s %.0f→%.0f%%) · %s" % (
        pts[-1][1], last["capacity_ah"],
        "ขับ" if last["kind"] == "ขับ" else "ชาร์จ " + last["kind"],
        last["soc_start"],
        last["soc_end"], f.summary.split(" · ", 1)[-1])
    return f


def report(folder, capacity_kwh=56.1, cells=120):
    """Text table for the `soh` command."""
    rated = nominal_ah(capacity_kwh, cells)
    ms = measurements(folder)
    lines = ["%-16s %4s %9s %6s %7s %8s %7s %6s" % (
        "เสร็จเมื่อ", "แบบ", "SOC", "วัดได้", "Ah", "ความจุ Ah", "kWh*",
        "SOH")]
    for m in ms:
        lines.append("%-16s %4s %4.0f→%-4.0f %5.0f%% %7.1f %8.1f %7.1f %5.1f%%%s"
                     % (m["time"].strftime("%Y-%m-%d %H:%M"), m["kind"],
                        m["soc_start"], m["soc_end"], m["gain"], m["ah"],
                        m["capacity_ah"], m["capacity_kwh"],
                        m["capacity_ah"] / rated * 100,
                        " ✓เต็ม" if m["to_full"] else ""))
    return ms, rated, "\n".join(lines)

