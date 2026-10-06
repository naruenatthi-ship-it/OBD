"""Command line tool: python -m deepal_s05 --port ... <command>"""

import argparse
import csv
import datetime
import json
import sys
import time

from . import analysis, config, pids, snapshot
from .elm327 import (
    Elm327, ElmError, NegativeResponse, NoData, response_header)

STATUS_TH = {
    pids.VALIDATED: "ยืนยันแล้ว",
    pids.CANDIDATE: "รอยืนยัน",
    pids.EXPERIMENTAL: "ทดลอง",
    pids.CUSTOM: "เพิ่มเอง",
}

DERIVED_LABELS = {
    "pack_power_kw": ("กำลังแพ็ก (V×I)", "kW"),
    "cell_delta_mv": ("ส่วนต่างแรงดันเซลล์", "mV"),
    "temp_delta_c": ("ส่วนต่างอุณหภูมิแบต", "C"),
}


def fmt(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        text = "%.3f" % value if abs(value) < 10 else "%.1f" % value
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value)


def connect(args):
    log = None
    if args.verbose:
        def log(direction, text):
            print("   %s %s" % (direction, text.replace("\n", " | ")),
                  file=sys.stderr)
    elm = Elm327(args.port, baudrate=args.baud, timeout=args.timeout,
                 log=log)
    info = elm.initialize()
    print("กล่อง OBD: %s   แรงดันขั้ว OBD: %s" % (
        info["version"], info["voltage"] or "-"))
    return elm


def delta_mv(values):
    """Cell voltage spread: from the per-cell array when configured."""
    if values.get("cells_delta_mv") is not None:
        return values["cells_delta_mv"]
    return values.get("cell_delta_mv")


def print_extras(snap, custom, arrays):
    """12 V voltage, user signals and array summaries of a snapshot."""
    v = snap.values
    print("%-3s %-8s %-26s %12s V" % ("", "ATRV", "แบต 12V (ที่ช่อง OBD)",
                                      fmt(v.get("aux_12v"))))
    for sig in custom:
        print("%-3s %-8s %-26s %12s %-7s %s" % (
            "OK " if sig.key not in snap.errors else "XX ",
            "%s:%s" % (sig.header, sig.request[2:]), sig.label,
            fmt(v.get(sig.key)), sig.unit, snap.errors.get(sig.key, "")))
    for arr in arrays:
        values = snap.arrays.get(arr.key)
        if values is None:
            print("XX  %-8s %-26s %s" % ("%s:%04X" % (arr.header, arr.did),
                                         arr.label, snap.errors.get(arr.key)))
            continue
        print("OK  %-8s %-26s %d ค่า  ต่ำสุด %s (#%d)  สูงสุด %s (#%d) %s" % (
            "%s:%04X" % (arr.header, arr.did), arr.label, len(values),
            fmt(v[arr.key + "_min"]), v[arr.key + "_min_no"],
            fmt(v[arr.key + "_max"]), v[arr.key + "_max_no"], arr.unit))


def cmd_check(args):
    signals = pids.SIGNALS + args.custom
    with connect(args) as elm:
        snap = snapshot.take(elm, signals, args.arrays, args.capacity)

    print("\nความจุแบตที่ตั้งไว้: %.2f kWh\n" % args.capacity)
    print("%-3s %-8s %-26s %12s %-7s %-11s %s" % (
        "", "คำสั่ง", "ค่า", "ผล", "หน่วย", "สถานะสูตร", "raw / หมายเหตุ"))
    ok = 0
    report = []
    for sig in pids.SIGNALS:
        raw, value = snap.raw.get(sig.key), snap.values.get(sig.key)
        err = snap.errors.get(sig.key)
        mark = "OK " if raw is not None and err is None else "XX "
        ok += mark == "OK "
        detail = raw.hex(" ").upper() if raw is not None else ""
        if err:
            detail = (detail + "  " + err).strip()
        elif sig.note:
            detail += "  (" + sig.note + ")"
        print("%-3s %-8s %-26s %12s %-7s %-11s %s" % (
            mark, sig.request, sig.label, fmt(value), sig.unit,
            STATUS_TH[sig.status], detail))
    for sig in signals:
        raw = snap.raw.get(sig.key)
        report.append({
            "key": sig.key, "request": sig.request, "header": sig.header,
            "raw": raw.hex().upper() if raw is not None else None,
            "value": snap.values.get(sig.key), "unit": sig.unit,
            "error": snap.errors.get(sig.key),
        })

    for key, (label, unit) in DERIVED_LABELS.items():
        if snap.values.get(key) is not None:
            print("%-3s %-8s %-26s %12s %s" % (
                "", "", label, fmt(snap.values[key]), unit))
    print_extras(snap, args.custom, args.arrays)

    print("\nตอบกลับ %d จาก %d รายการ" % (ok, len(pids.SIGNALS)))
    print("เทียบกับรถ: SOC ต้องตรงกับหน้าจอรถ, จอดนิ่งกระแสควรใกล้ 0 A")

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump({
                "time": datetime.datetime.now().isoformat(timespec="seconds"),
                "capacity_kwh": args.capacity,
                "aux_12v": snap.values.get("aux_12v"),
                "signals": report,
                "arrays": snap.arrays,
            }, f, ensure_ascii=False, indent=2)
        print("บันทึกผลไว้ที่ %s" % args.save)
    return 0 if ok else 1


def status_line(values, custom):
    text = ("%s  SOC %s%%  %sV  %sA  %skW  เซลล์ %s-%sV (Δ%smV)"
            "  แบต %s-%s°C  SOH %s%%  12V %s" % (
                datetime.datetime.now().strftime("%H:%M:%S"),
                fmt(values.get("soc")), fmt(values.get("pack_voltage")),
                fmt(values.get("pack_current")),
                fmt(values.get("pack_power_kw")),
                fmt(values.get("cells_min", values.get("cell_v_min"))),
                fmt(values.get("cells_max", values.get("cell_v_max"))),
                fmt(delta_mv(values)),
                fmt(values.get("batt_temp_min")),
                fmt(values.get("batt_temp_max")),
                fmt(values.get("soh")), fmt(values.get("aux_12v"))))
    for sig in custom:
        text += "  %s %s%s" % (sig.label, fmt(values.get(sig.key)), sig.unit)
    return text


def live_signals(args):
    return [pids.SIGNALS_BY_KEY[k] for k in pids.LIVE_KEYS] + args.custom


def cmd_live(args):
    signals = live_signals(args)
    with connect(args) as elm:
        log = snapshot.CsvLog(args.csv) if args.csv else None
        try:
            print("กด Ctrl+C เพื่อหยุด\n")
            count = 0
            while args.count == 0 or count < args.count:
                snap = snapshot.take(elm, signals, args.arrays, args.capacity)
                print(status_line(snap.values, args.custom))
                if log:
                    log.write(snap)
                count += 1
                if args.count == 0 or count < args.count:
                    time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nหยุดแล้ว")
        finally:
            if log:
                log.__exit__()
    return 0


def cmd_charge(args):
    signals = live_signals(args)
    label = args.label or ""
    path = args.csv or "charge_%s%s.csv" % (
        datetime.datetime.now().strftime("%Y%m%d_%H%M"),
        "_" + label if label else "")
    history = []
    with connect(args) as elm, snapshot.CsvLog(path, label) as log:
        print("บันทึกทุก %g วินาทีลงไฟล์ %s  กด Ctrl+C เพื่อหยุด" % (
            args.interval, path))
        if args.until_balanced:
            print("จะหยุดเองเมื่อแบตเต็มและส่วนต่างแรงดันเซลล์นิ่ง"
                  " (เปลี่ยนไม่เกิน %g mV ใน %g นาที)" % (
                      args.stable_mv, args.window))
        print()
        start = time.time()
        try:
            while True:
                snap = snapshot.take(elm, signals, args.arrays, args.capacity)
                log.write(snap)
                history.append((snap.time, snap.values.get("soc"),
                                delta_mv(snap.values)))
                state, text = analysis.balance_status(
                    history, args.window * 60, args.stable_mv)
                print(status_line(snap.values, args.custom))
                print("    " + text)
                if args.until_balanced and state == "stable":
                    print("\nบาลานซ์นิ่งแล้ว หยุดบันทึก")
                    break
                if args.duration and time.time() - start >= \
                        args.duration * 60:
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nหยุดแล้ว")
    print("บันทึกไว้ที่ %s\nดูกราฟ: python -m deepal_s05 report %s" % (
        path, path))
    return 0


def cmd_balance(args):
    args.until_balanced = True
    return cmd_charge(args)


def median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] +
                                                 values[mid]) / 2


def sample_ir(elm, capacity, seconds, cells_array=None):
    """Read pack current and voltage (and cell voltages) in turn for
    `seconds`. Each voltage reading is paired with the average of the
    current read just before and after it."""
    cur = pids.SIGNALS_BY_KEY["pack_current"]
    volt = pids.SIGNALS_BY_KEY["pack_voltage"]

    def current():
        return cur.value(elm.read_did(cur.header, cur.did), capacity)

    currents, voltages, cells = [], [], []
    end = time.monotonic() + seconds
    i_before = current()
    while time.monotonic() < end:
        v = volt.value(elm.read_did(volt.header, volt.did), capacity)
        cell_values = None
        if cells_array:
            cell_values = cells_array.decode(
                elm.read_did(cells_array.header, cells_array.did))
        i_after = current()
        currents.append((i_before + i_after) / 2)
        voltages.append(v)
        cells.append(cell_values)
        i_before = i_after
    return currents, voltages, cells


HEALTH_FIELDS = ["time", "zone", "soc", "delta_mv", "temp_max", "temp_min",
                 "aux_12v", "soh", "ir_mohm", "ir_r2"]


def cmd_health(args):
    signals = live_signals(args)
    cells_array = next((a for a in args.arrays if a.key == "cells"), None)
    ir = None
    cell_ir = []
    with connect(args) as elm:
        print("\nอ่านค่า %d รอบ..." % args.samples)
        snaps = []
        for i in range(args.samples):
            snaps.append(snapshot.take(elm, signals, args.arrays,
                                       args.capacity))
            if i < args.samples - 1:
                time.sleep(1)
        if args.ir:
            print("\nวัดค่า IR %d วินาที: ระหว่างนี้ต้องให้กระแสเปลี่ยนมากๆ เช่น"
                  " ให้คนอื่นขับแล้วเร่งแรงสลับกับปล่อยคันเร่ง"
                  " (จอดนิ่งกระแสเปลี่ยนน้อย มักวัดไม่ได้)" % args.ir)
            if args.cell_ir and not cells_array:
                print("ข้าม IR รายเซลล์: ยังไม่มี array ชื่อ cells ใน --signals")
            try:
                currents, voltages, cells = sample_ir(
                    elm, args.capacity, args.ir,
                    cells_array if args.cell_ir else None)
            except (NoData, NegativeResponse) as e:
                print("วัด IR ไม่สำเร็จ: %s" % e)
                currents = []
            if currents:
                ir = analysis.estimate_ir(currents, voltages, args.min_range)
            if ir and args.cell_ir and cells_array and \
                    ir["r_mohm"] is not None:
                for n in range(min(len(c) for c in cells)):
                    fit = analysis.estimate_ir(
                        currents, [c[n] for c in cells], args.min_range)
                    cell_ir.append((n + 1, fit["r_mohm"]))

    vals = [s.values for s in snaps]
    result = {
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "soc": median(v.get("soc") for v in vals),
        "delta_mv": median(delta_mv(v) for v in vals),
        "temp_max": median(v.get("batt_temp_max") for v in vals),
        "temp_min": median(v.get("batt_temp_min") for v in vals),
        "aux_12v": median(v.get("aux_12v") for v in vals),
        "soh": median(v.get("soh") for v in vals),
        "ir_mohm": ir["r_mohm"] if ir else None,
        "ir_r2": ir["r2"] if ir else None,
    }
    soc = result["soc"]
    result["zone"] = ("unknown" if soc is None else "top" if soc >= 95
                      else "low" if soc <= 25 else "mid")

    previous = None
    try:
        with open(args.history, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("zone") == result["zone"]:
                    previous = row
    except OSError:
        pass

    print("\n===== ผลตรวจสุขภาพแบต =====")
    print("SOC %s%%   SOH %s%%   อุณหภูมิแบต %s-%s°C   แบต 12V %s V" % (
        fmt(soc), fmt(result["soh"]), fmt(result["temp_min"]),
        fmt(result["temp_max"]), fmt(result["aux_12v"])))
    d = result["delta_mv"]
    print("ส่วนต่างแรงดันเซลล์: %s mV" % fmt(d))
    if cells_array and vals[-1].get("cells_min_no"):
        print("  เซลล์ต่ำสุด #%d  เซลล์สูงสุด #%d" % (
            vals[-1]["cells_min_no"], vals[-1]["cells_max_no"]))
    zone = result["zone"]
    if zone == "top" and d is not None:
        print("  ช่วงใกล้เต็ม: ช่วงที่ใช้ดูความไม่สมดุลได้ดีที่สุด")
        if d <= args.good_mv:
            print("  -> ส่วนต่างน้อย เซลล์สมดุลดี")
        elif d <= args.bad_mv:
            print("  -> ส่วนต่างปานกลาง ลองชาร์จ AC ให้เต็มแล้วเสียบทิ้งไว้"
                  " (คำสั่ง balance) แล้ววัดใหม่")
        else:
            print("  -> ส่วนต่างมาก ถ้าเสียบทิ้งไว้ที่ 100% แล้วไม่ลดลง"
                  " ควรให้ร้านตรวจและบาลานซ์")
        print("  (เกณฑ์ %g / %g mV เป็นแนวทางเบื้องต้น ไม่ใช่เกณฑ์ของผู้ผลิต"
              " ปรับได้ด้วย --good-mv / --bad-mv)" % (args.good_mv,
                                                    args.bad_mv))
    elif zone == "low":
        print("  ช่วงแบตเหลือน้อย: เซลล์ที่อ่อนจะเห็นชัดในช่วงนี้"
              " ให้เก็บค่าไว้เทียบในระยะยาว")
    elif zone == "mid":
        print("  SOC ช่วงกลาง: แบต LFP ส่วนต่างจะน้อยเสมอในช่วงนี้"
              " ใช้ตัดสินไม่ได้ ให้วัดตอน SOC >= 95% หรือ <= 25%")
    if previous:
        print("  ครั้งก่อนในช่วงเดียวกัน (%s): %s mV" % (
            previous["time"], previous.get("delta_mv") or "-"))

    if ir:
        if ir["r_mohm"] is None:
            print("IR แพ็ก: วัดไม่ได้ กระแสเปลี่ยนแค่ %.0f A (ต้องการอย่างน้อย"
                  " %g A)" % (ir["i_range"], args.min_range))
        else:
            print("IR แพ็ก (ประมาณ): %.0f mΩ  จาก %d จุด กระแสเปลี่ยน %.0f A"
                  "  ความแม่นของเส้น R² %.2f" % (
                      ir["r_mohm"], ir["n"], ir["i_range"], ir["r2"]))
            if ir["r2"] < 0.5:
                print("  ** R² ต่ำ ผลนี้ยังไม่น่าเชื่อถือ ลองวัดนานขึ้นหรือให้กระแส"
                      "เปลี่ยนมากขึ้น")
            print("  ค่านี้ใช้เทียบกับครั้งก่อนของรถคันเดิมได้ดีที่สุด"
                  " ค่าสูงขึ้นเรื่อยๆ แปลว่าแบตเสื่อมลง")
            if previous and previous.get("ir_mohm"):
                print("  ครั้งก่อน: %.0f mΩ" % float(previous["ir_mohm"]))
        if cell_ir:
            ranked = sorted((c for c in cell_ir if c[1] is not None),
                            key=lambda c: -c[1])
            print("  IR รายเซลล์สูงสุด 5 อันดับ: " + ", ".join(
                "#%d %.2f mΩ" % c for c in ranked[:5]))
    if result["aux_12v"] is not None:
        print("แบต 12V: ตอนรถ READY ควรประมาณ 13.5-14.5 V (DC-DC กำลังชาร์จ)"
              " ถ้าต่ำกว่า 13 V ตอน READY ให้ตรวจแบต 12V และ DC-DC")

    exists = False
    try:
        with open(args.history, encoding="utf-8") as f:
            exists = bool(f.readline())
    except OSError:
        pass
    with open(args.history, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, HEALTH_FIELDS)
        if not exists:
            w.writeheader()
        w.writerow({k: ("" if result[k] is None else
                        round(result[k], 3) if isinstance(result[k], float)
                        else result[k]) for k in HEALTH_FIELDS})
    print("\nเก็บผลไว้ใน %s" % args.history)
    return 0


def cmd_report(args):
    from . import report
    out = report.write_report(args.files, args.out, args.capacity)
    print("สร้างรายงานแล้ว: %s (เปิดด้วยเบราว์เซอร์)" % out)
    return 0


def cmd_dashboard(args):
    from . import dashboard
    with connect(args) as elm:
        dashboard.serve(elm, live_signals(args), args.arrays, args.capacity,
                        host=args.host, port=args.http_port,
                        interval=args.interval, csv_path=args.csv)
    return 0


def cmd_discover(args):
    start, end = int(args.start, 16), int(args.end, 16)
    found = []
    with connect(args) as elm:
        if args.extended:
            elm.start_session(args.header, extended=True)
            print("เข้าโหมดวินิจฉัยขยาย (10 03) ที่ %s แล้ว" % args.header)
        last_tp = time.monotonic()
        print("\nสแกน DID %04X-%04X ที่ header %s (อ่านอย่างเดียว)"
              " กด Ctrl+C เพื่อหยุด\n" % (start, end, args.header))
        try:
            for did in range(start, end + 1):
                if args.extended and time.monotonic() - last_tp > 2:
                    elm.tester_present(args.header)
                    last_tp = time.monotonic()
                if did % 64 == 0:
                    print("  ...%04X" % did, file=sys.stderr)
                try:
                    raw = elm.read_did(args.header, did)
                except (NoData, NegativeResponse):
                    continue
                run = analysis.find_cell_run(raw)
                found.append((did, raw, run))
                text = raw.hex(" ").upper()
                if len(text) > 72:
                    text = text[:72] + " ..."
                print("22%04X  %3d ไบต์  %s" % (did, len(raw), text))
                if run:
                    print("        ** " + analysis.describe_cell_run(run))
        except KeyboardInterrupt:
            print("\nหยุดแล้ว")
        finally:
            if args.extended:
                try:
                    elm.start_session(args.header, extended=False)
                except ElmError:
                    pass
    print("\nพบ %d DID" % len(found))
    cells = [f for f in found if f[2]]
    if cells:
        print("DID ที่อาจเป็นแรงดันรายเซลล์: " +
              ", ".join("22%04X" % d for d, _, _ in cells))
    if args.save:
        with open(args.save, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["header", "did", "length", "raw", "cell_like"])
            for did, raw, run in found:
                w.writerow([args.header, "%04X" % did, len(raw),
                            raw.hex().upper(),
                            analysis.describe_cell_run(run) if run else ""])
        print("บันทึกไว้ที่ %s" % args.save)
    return 0


# Identification DIDs (ISO 14229) read from every ECU found
ID_DIDS = [
    (0xF187, "part_number", "รหัสชิ้นส่วน"),
    (0xF18A, "supplier", "ผู้ผลิต"),
    (0xF197, "system_name", "ชื่อระบบ"),
    (0xF191, "hw_number", "เลขฮาร์ดแวร์"),
    (0xF193, "hw_version", "เวอร์ชันฮาร์ดแวร์"),
    (0xF195, "sw_version", "เวอร์ชันซอฟต์แวร์"),
]


def as_text(raw):
    text = raw.decode("ascii", "replace").strip("\x00 ")
    if raw and all(32 <= b < 127 or b == 0 for b in raw):
        return text
    return raw.hex(" ").upper()


def cmd_ecus(args):
    start, end = int(args.start, 16), int(args.end, 16)
    found = []
    with connect(args) as elm:
        print("\nสแกนหา ECU ที่ header %03X-%03X (ใช้คำสั่งอ่าน 22F187)"
              " กด Ctrl+C เพื่อหยุด\n" % (start, end))
        try:
            for h in range(start, end + 1):
                header = "%03X" % h
                if h % 16 == 0:
                    print("  ...%s" % header, file=sys.stderr)
                try:
                    elm.read_did(header, 0xF187)
                except NegativeResponse:
                    pass  # it answered, so the ECU exists
                except NoData:
                    continue
                info = {"header": header,
                        "response": response_header(header)}
                for did, key, _ in ID_DIDS:
                    try:
                        info[key] = as_text(elm.read_did(header, did))
                    except (NoData, NegativeResponse):
                        info[key] = ""
                found.append(info)
                print("พบ ECU  %s -> %s  %s" % (
                    header, info["response"], "  ".join(
                        "%s: %s" % (label, info[key])
                        for _, key, label in ID_DIDS if info[key])))
        except KeyboardInterrupt:
            print("\nหยุดแล้ว")
    print("\nพบ %d ECU" % len(found))
    if args.save:
        with open(args.save, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, ["header", "response"] +
                               [k for _, k, _ in ID_DIDS])
            w.writeheader()
            w.writerows(found)
        print("บันทึกไว้ที่ %s" % args.save)
    return 0


def cmd_sniff(args):
    out = open(args.out, "w", encoding="utf-8")
    count = 0
    with connect(args) as elm:
        for warning in elm.setup_monitor(args.filter, args.mask):
            print("คำเตือน: " + warning)
        print("\nกำลังดักฟัง (ฟังอย่างเดียว ไม่ส่งอะไรเข้ารถ)"
              " filter=%s mask=%s" % (args.filter, args.mask))
        print("ให้ช่างเปิดหน้าข้อมูลในเครื่องสแกนได้เลย กด Ctrl+C เมื่อเสร็จ\n")
        start = time.monotonic()
        out.write("# deepal_s05 sniff %s\n" % datetime.datetime.now()
                  .isoformat(timespec="seconds"))

        def on_line(line):
            nonlocal count
            out.write("%.3f\t%s\n" % (time.monotonic() - start, line))
            count += 1
            if count % 50 == 0:
                out.flush()
                print("\r  รับแล้ว %d เฟรม" % count, end="", file=sys.stderr)

        try:
            overflows = elm.monitor(on_line, duration=args.duration)
        except KeyboardInterrupt:
            overflows = 0
        finally:
            out.close()
    print("\nบันทึก %d เฟรมไว้ที่ %s" % (count, args.out))
    if overflows:
        print("กล่องรับไม่ทัน %d ครั้ง ลองแคบ filter เช่น --filter 7A1 "
              "--mask 7F7" % overflows)
    print("ดูสรุปด้วย: python -m deepal_s05 analyze %s" % args.out)
    return 0


def cmd_analyze(args):
    frames = analysis.read_sniff_log(args.log)
    messages = analysis.reassemble(frames)
    groups = analysis.summarise(messages)
    print("อ่าน %d เฟรม รวมเป็น %d ข้อความ พบคำสั่ง %d แบบ\n" % (
        len(frames), len(messages), len(groups)))
    for g in groups:
        resp = g["last_response"]
        text = resp.hex(" ").upper()
        if len(text) > 60:
            text = text[:60] + " ..."
        print("%s  %-14s x%-4d -> %s  %3d ไบต์%s  %s" % (
            g["request_id"], g["request"].hex(" ").upper(), g["count"],
            g["response_id"] or "---", len(resp),
            "  ค่าเปลี่ยน" if g["changes"] else "", text))
        print("     %s" % g["service"])
        if g["cell_run"]:
            print("     ** " + analysis.describe_cell_run(g["cell_run"]))
    writes = [g for g in groups if g["request"][0] in (0x2E, 0x31, 0x27,
                                                         0x11, 0x14, 0x34)]
    if writes:
        print("\nหมายเหตุ: เครื่องสแกนส่งคำสั่งเขียน/สั่งงานด้วย (%s)"
              " โปรแกรมนี้จะไม่ส่งคำสั่งแบบนี้" % ", ".join(
                  sorted({g["service"] for g in writes})))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m deepal_s05",
        description="อ่านค่าแบตเตอรี่ Deepal S05 ผ่านกล่อง ELM327 "
                    "(อ่านอย่างเดียว ไม่เขียนค่าใดๆ ลงรถ)")
    p.add_argument("--port",
                   help="พอร์ตกล่อง เช่น COM5, /dev/rfcomm0, /dev/ttyUSB0 "
                        "หรือ socket://192.168.0.10:35000 (กล่อง WiFi)")
    p.add_argument("--baud", type=int, default=38400,
                   help="baud rate ของกล่อง USB/บลูทูธ (ค่าเริ่มต้น 38400)")
    p.add_argument("--capacity", type=float,
                   default=pids.DEFAULT_CAPACITY_KWH,
                   help="ความจุแบต kWh (ค่าเริ่มต้น 56.1)")
    p.add_argument("--timeout", type=float, default=2.0,
                   help="วินาทีที่รอคำตอบแต่ละคำสั่ง")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="แสดงคำสั่งและคำตอบดิบจากกล่อง")
    p.add_argument("--signals",
                   help="ไฟล์ JSON ค่าที่เพิ่มเอง (เช่น อุณหภูมิ OBC, แรงดันรายเซลล์)"
                        " ดูตัวอย่างใน examples/")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="ลองอ่านทุกค่าครั้งเดียว ใช้ตรวจว่ารถตอบไหม")
    c.add_argument("--save", help="บันทึกผลเป็นไฟล์ JSON")
    c.set_defaults(func=cmd_check)

    l = sub.add_parser("live", help="แสดงค่าสดต่อเนื่อง")
    l.add_argument("--interval", type=float, default=2.0,
                   help="วินาทีระหว่างรอบ")
    l.add_argument("--count", type=int, default=0,
                   help="จำนวนรอบ (0 = ไม่จำกัด)")
    l.add_argument("--csv", help="บันทึกต่อท้ายไฟล์ CSV")
    l.set_defaults(func=cmd_live)

    d = sub.add_parser("discover", help="สแกนหา DID ที่ ECU ตอบ (อ่านอย่างเดียว)")
    d.add_argument("--header", default=pids.BMS_HEADER)
    d.add_argument("--start", default="F200")
    d.add_argument("--end", default="F2FF")
    d.add_argument("--extended", action="store_true",
                   help="เข้าโหมดวินิจฉัยขยาย (10 03) ก่อนอ่าน ใช้ตอนจอดเท่านั้น")
    d.add_argument("--save", help="บันทึกผลเป็นไฟล์ CSV")
    d.set_defaults(func=cmd_discover)

    e = sub.add_parser("ecus", help="สแกนหา ECU ทั้งหมดในรถ (อ่านอย่างเดียว)")
    e.add_argument("--start", default="700")
    e.add_argument("--end", default="7FF")
    e.add_argument("--save", help="บันทึกผลเป็นไฟล์ CSV")
    e.set_defaults(func=cmd_ecus)

    s = sub.add_parser("sniff", help="ดักฟังตอนเครื่องสแกนอื่นคุยกับรถ")
    s.add_argument("--out", default="sniff.log", help="ไฟล์บันทึก")
    s.add_argument("--filter", default="700",
                   help="CAN id filter (ค่าเริ่มต้น 700 คู่กับ mask 700 = 7xx)")
    s.add_argument("--mask", default="700")
    s.add_argument("--duration", type=float,
                   help="หยุดเองหลังกี่วินาที (ไม่ใส่ = จนกด Ctrl+C)")
    s.set_defaults(func=cmd_sniff)

    a = sub.add_parser("analyze", help="สรุปไฟล์จาก sniff (ไม่ต้องใส่ --port)")
    a.add_argument("log", help="ไฟล์ที่ได้จากคำสั่ง sniff")
    a.set_defaults(func=cmd_analyze)

    def charge_options(cp, interval, until_balanced):
        cp.add_argument("--interval", type=float, default=interval,
                        help="วินาทีระหว่างการบันทึก")
        cp.add_argument("--csv", help="ไฟล์ CSV (ค่าเริ่มต้นตั้งชื่อตามเวลา)")
        cp.add_argument("--label", help="ชื่อรอบ เช่น 32A หรือ 16A ใช้ในกราฟ")
        cp.add_argument("--duration", type=float,
                        help="หยุดเองหลังกี่นาที")
        cp.add_argument("--window", type=float, default=30,
                        help="นาทีที่ใช้ดูว่าส่วนต่างเซลล์นิ่งหรือยัง")
        cp.add_argument("--stable-mv", type=float, default=1.0,
                        help="ส่วนต่างเปลี่ยนไม่เกินกี่ mV ถึงถือว่านิ่ง")
        cp.set_defaults(until_balanced=until_balanced)

    ch = sub.add_parser("charge", help="บันทึกข้อมูลระหว่างชาร์จ")
    charge_options(ch, 30, False)
    ch.add_argument("--until-balanced", action="store_true",
                    help="หยุดเองเมื่อแบตเต็มและส่วนต่างเซลล์นิ่ง")
    ch.set_defaults(func=cmd_charge)

    b = sub.add_parser("balance",
                       help="เฝ้าดูการบาลานซ์ตอนชาร์จเต็มแล้วเสียบทิ้งไว้")
    charge_options(b, 60, True)
    b.set_defaults(func=cmd_balance)

    h = sub.add_parser("health", help="ตรวจสุขภาพแบตและเก็บประวัติ")
    h.add_argument("--samples", type=int, default=5,
                   help="จำนวนรอบที่อ่านแล้วเอาค่ากลาง")
    h.add_argument("--ir", type=int, metavar="SECONDS",
                   help="วัด IR ของแพ็กกี่วินาที (ต้องให้กระแสเปลี่ยนมากๆ)")
    h.add_argument("--cell-ir", action="store_true",
                   help="วัด IR รายเซลล์ด้วย (ต้องมี array cells ใน --signals)")
    h.add_argument("--min-range", type=float, default=20.0,
                   help="กระแสต้องเปลี่ยนอย่างน้อยกี่ A ถึงจะคำนวณ IR")
    h.add_argument("--good-mv", type=float, default=30.0,
                   help="ส่วนต่างตอนเต็มไม่เกินนี้ถือว่าดี (แนวทางเบื้องต้น)")
    h.add_argument("--bad-mv", type=float, default=100.0,
                   help="ส่วนต่างตอนเต็มเกินนี้ถือว่ามาก (แนวทางเบื้องต้น)")
    h.add_argument("--history", default="health_history.csv",
                   help="ไฟล์เก็บประวัติผลตรวจ")
    h.set_defaults(func=cmd_health)

    r = sub.add_parser("report",
                       help="ทำกราฟจากไฟล์ CSV ของ charge/live (ไม่ต้องใส่ --port)")
    r.add_argument("files", nargs="+", help="ไฟล์ CSV หนึ่งไฟล์หรือมากกว่า")
    r.add_argument("-o", "--out", default="report.html")
    r.set_defaults(func=cmd_report)

    w = sub.add_parser("dashboard", help="หน้าจอแสดงผลสดในเบราว์เซอร์")
    w.add_argument("--host", default="127.0.0.1",
                   help="ใช้ 0.0.0.0 เพื่อเปิดดูจากมือถือในวง WiFi เดียวกัน")
    w.add_argument("--http-port", type=int, default=8000)
    w.add_argument("--interval", type=float, default=2.0)
    w.add_argument("--csv", help="บันทึกค่าลง CSV ไปด้วย")
    w.set_defaults(func=cmd_dashboard)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func not in (cmd_analyze, cmd_report) and not args.port:
        parser.error("ต้องใส่ --port")
    try:
        args.custom, args.arrays = config.load(args.signals)
    except config.ConfigError as e:
        parser.error(str(e))
    try:
        return args.func(args)
    except ElmError as e:
        print("ผิดพลาด: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("เปิดพอร์ต %s ไม่ได้: %s" % (args.port, e), file=sys.stderr)
        return 2
