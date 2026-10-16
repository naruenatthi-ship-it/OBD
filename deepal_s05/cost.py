"""What charging costs: energy into the battery per charge (from the
recordings), the energy taken from the grid, and baht, summed by month.

AC charges at home are priced with the household tariff, flat or TOU;
DC fast charges with a per-kWh station price. The rates are examples to
replace with the owner's own bill: Thai tariffs and the Ft charge change
every few months. Settings live in tariff.json in the data folder:

  {"mode": "tou", "flat": 4.2, "on_peak": 5.7982, "off_peak": 2.6369,
   "ft": 0.0, "vat": 7, "dc_price": 7.5, "ac_efficiency": 0.9}

TOU on-peak is Monday-Friday 09:00-22:00; public holidays (off-peak all
day) are not known to the program, so a charge on one is priced a little
high.
"""

import json
import os

from . import predict

DEFAULTS = {
    "mode": "flat",         # "flat" or "tou"
    "flat": 4.2,            # baht/kWh, all-in (rough household average)
    "on_peak": 5.7982,      # MEA/PEA TOU base rates, before Ft and VAT
    "off_peak": 2.6369,
    "ft": 0.0,              # baht/kWh added to TOU rates (see your bill)
    "vat": 7.0,             # % added to TOU rates
    "dc_price": 7.5,        # baht/kWh at fast chargers (per battery kWh)
    "ac_efficiency": 0.9,   # battery kWh / grid kWh for home AC charging
}
TARIFF_FILE = "tariff.json"
MAX_STEP_S = 60.0


def load_tariff(folder):
    t = dict(DEFAULTS)
    try:
        with open(os.path.join(folder, TARIFF_FILE), encoding="utf-8") as f:
            t.update(json.load(f))
    except (OSError, ValueError):
        pass
    return t


def on_peak(t):
    return t.weekday() < 5 and 9 <= t.hour < 22


def tou_rate(tariff, t):
    base = tariff["on_peak"] if on_peak(t) else tariff["off_peak"]
    return (base + tariff["ft"]) * (1 + tariff["vat"] / 100)


def charge_cost(rows, tariff):
    """One charging session -> dict with battery kWh, grid kWh and baht.
    Each step is priced at its own time, so a TOU charge that starts
    before 22:00 is split correctly."""
    steps = []  # (time, battery kWh)
    for a, b in zip(rows, rows[1:]):
        dt = (b["_t"] - a["_t"]).total_seconds()
        v, i = predict.num(a.get("pack_voltage")), \
            predict.num(a.get("pack_current"))
        if dt <= MAX_STEP_S and v is not None and i is not None and i < 0:
            steps.append((a["_t"], -i * v * dt / 3.6e6, -i))
    kwh = sum(e for _, e, _ in steps)
    dc = max((amps for _, _, amps in steps), default=0) > 40
    if dc:
        grid, baht = None, kwh * tariff["dc_price"]
    else:
        grid = kwh / tariff["ac_efficiency"]
        baht = sum(e / tariff["ac_efficiency"] *
                   (tou_rate(tariff, t) if tariff["mode"] == "tou"
                    else tariff["flat"]) for t, e, _ in steps)
    soc = [predict.num(r.get("soc")) for r in rows]
    soc = [s for s in soc if s is not None]
    return {"start": rows[0]["_t"], "end": rows[-1]["_t"],
            "kind": "DC" if dc else "AC", "kwh": kwh, "grid_kwh": grid,
            "baht": baht, "soc_start": soc[0] if soc else None,
            "soc_end": soc[-1] if soc else None}


def charges(folder, tariff=None):
    tariff = tariff or load_tariff(folder)
    rows = predict.read_drive(folder, ("drive_*.csv", "charge_*.csv"),
                              days=3650, cell_every=10 ** 9)
    out = []
    for kind, s in predict.sessions(rows):
        if kind == "charge":
            c = charge_cost(s, tariff)
            if c["kwh"] >= 0.5:
                out.append(c)
    return out


def monthly(items):
    months = {}
    for c in items:
        m = months.setdefault(c["start"].strftime("%Y-%m"), {
            "count": 0, "kwh": 0.0, "baht": 0.0, "ac_kwh": 0.0,
            "dc_kwh": 0.0, "ac_baht": 0.0, "dc_baht": 0.0})
        m["count"] += 1
        m["kwh"] += c["kwh"]
        m["baht"] += c["baht"]
        k = c["kind"].lower()
        m[k + "_kwh"] += c["kwh"]
        m[k + "_baht"] += c["baht"]
    return dict(sorted(months.items()))


def cost_html(folder):
    import html
    from . import visual
    tariff = load_tariff(folder)
    items = charges(folder, tariff)
    if not items:
        return visual.page("ค่าไฟการชาร์จ", '<section class="card">ยังไม่มีการชาร์จ'
                           'ที่บันทึกไว้ (โหมดอัตโนมัติบันทึกให้เองทุกครั้งที่ชาร์จ'
                           'ขณะ Pi ทำงาน)</section>')
    rows = "".join(
        "<tr><td>%s</td><td>%d</td><td>%.1f</td><td>%.0f</td><td>%.1f</td>"
        "<td>%.0f</td><td><b>%.0f</b></td></tr>" % (
            m, v["count"], v["ac_kwh"], v["ac_baht"], v["dc_kwh"],
            v["dc_baht"], v["baht"])
        for m, v in monthly(items).items())
    recent = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%.1f</td><td>%s</td>"
        "<td>%.0f</td></tr>" % (
            c["start"].strftime("%d/%m %H:%M"), c["kind"],
            "%s→%s%%" % (fmt(c["soc_start"]), fmt(c["soc_end"])), c["kwh"],
            "%.1f" % c["grid_kwh"] if c["grid_kwh"] is not None else "-",
            c["baht"]) for c in reversed(items[-20:]))
    mode = ("TOU (on-peak %.2f / off-peak %.2f บาท + Ft %.2f + VAT %g%%)" % (
        tariff["on_peak"], tariff["off_peak"], tariff["ft"], tariff["vat"])
        if tariff["mode"] == "tou" else "อัตราเดียว %.2f บาท/kWh" % tariff["flat"])
    body = (
        '<section class="card"><h2>สรุปรายเดือน (บาท)</h2><div class="table">'
        '<table><tr><th>เดือน</th><th>ครั้ง</th><th>บ้าน AC kWh</th>'
        '<th>บ้าน บาท</th><th>ชาร์จเร็ว DC kWh</th><th>DC บาท</th><th>รวม บาท</th>'
        '</tr>%s'
        '</table></div></section><section class="card"><h2>การชาร์จล่าสุด'
        '</h2><div class="table"><table><tr><th>เริ่ม</th><th>แบบ</th>'
        '<th>SOC</th><th>เข้าแบต kWh</th><th>จากไฟบ้าน kWh</th><th>บาท</th>'
        '</tr>%s</table></div></section><p class="muted">ไฟบ้าน: %s · ประสิทธิภาพ'
        'การชาร์จ AC ที่ใช้ %.0f%% · ตู้ชาร์จเร็ว DC %.2f บาท/kWh (ต่อ kWh ที่เข้าแบต)'
        ' · ค่าเหล่านี้เป็นตัวอย่าง แก้ให้ตรงบิลของคุณในไฟล์ %s · TOU ไม่รู้วันหยุด'
        'นักขัตฤกษ์ ค่าที่คิดในวันหยุดจะสูงกว่าจริงเล็กน้อย</p>' % (
            rows, recent, html.escape(mode),
            tariff["ac_efficiency"] * 100, tariff["dc_price"], TARIFF_FILE))
    return visual.page("ค่าไฟการชาร์จ", body)


def fmt(v):
    return "-" if v is None else "%.0f" % v
