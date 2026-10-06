"""Command line tool: python -m deepal_s05 {check,live,discover} --port ..."""

import argparse
import csv
import datetime
import json
import sys
import time

from . import pids
from .elm327 import Elm327, ElmError, NegativeResponse, NoData

STATUS_TH = {
    pids.VALIDATED: "ยืนยันแล้ว",
    pids.CANDIDATE: "รอยืนยัน",
    pids.EXPERIMENTAL: "ทดลอง",
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


def read_signals(elm, signals, capacity):
    """Read each DID once and decode every signal that uses it.
    Returns {key: (raw_bytes_or_None, value_or_None, error_or_None)}."""
    cache = {}
    results = {}
    for sig in signals:
        if sig.did not in cache:
            try:
                cache[sig.did] = (elm.read_did(sig.header, sig.did), None)
            except NegativeResponse as e:
                cache[sig.did] = (None, str(e))
            except NoData as e:
                cache[sig.did] = (None, "ไม่ตอบ (%s)" % e)
        raw, err = cache[sig.did]
        value = sig.value(raw, capacity) if raw is not None else None
        if raw is not None and value is None and sig.decode is not None:
            err = "ข้อมูลสั้นเกินไป (%d ไบต์)" % len(raw)
        results[sig.key] = (raw, value, err)
    return results


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


def cmd_check(args):
    with connect(args) as elm:
        results = read_signals(elm, pids.SIGNALS, args.capacity)

    print("\nความจุแบตที่ตั้งไว้: %.2f kWh\n" % args.capacity)
    print("%-3s %-8s %-26s %12s %-7s %-11s %s" % (
        "", "คำสั่ง", "ค่า", "ผล", "หน่วย", "สถานะสูตร", "raw / หมายเหตุ"))
    ok = 0
    report = []
    for sig in pids.SIGNALS:
        raw, value, err = results[sig.key]
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
        report.append({
            "key": sig.key, "request": sig.request, "header": sig.header,
            "raw": raw.hex().upper() if raw is not None else None,
            "value": value, "unit": sig.unit, "error": err,
        })

    values = {k: v for k, (_, v, _) in results.items()}
    for key, value in pids.derived(values).items():
        label, unit = DERIVED_LABELS[key]
        print("%-3s %-8s %-26s %12s %s" % ("", "", label, fmt(value), unit))

    print("\nตอบกลับ %d จาก %d รายการ" % (ok, len(pids.SIGNALS)))
    print("เทียบกับรถ: SOC ต้องตรงกับหน้าจอรถ, จอดนิ่งกระแสควรใกล้ 0 A")

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump({
                "time": datetime.datetime.now().isoformat(timespec="seconds"),
                "capacity_kwh": args.capacity,
                "signals": report,
            }, f, ensure_ascii=False, indent=2)
        print("บันทึกผลไว้ที่ %s" % args.save)
    return 0 if ok else 1


def cmd_live(args):
    signals = [pids.SIGNALS_BY_KEY[k] for k in pids.LIVE_KEYS]
    writer = None
    csv_file = None
    with connect(args) as elm:
        try:
            if args.csv:
                csv_file = open(args.csv, "a", newline="", encoding="utf-8")
                keys = [s.key for s in signals] + list(DERIVED_LABELS)
                writer = csv.DictWriter(csv_file, ["time"] + keys)
                if csv_file.tell() == 0:
                    writer.writeheader()
            print("กด Ctrl+C เพื่อหยุด\n")
            count = 0
            while args.count == 0 or count < args.count:
                results = read_signals(elm, signals, args.capacity)
                values = {k: v for k, (_, v, _) in results.items()}
                values.update(pids.derived(values))
                now = datetime.datetime.now().strftime("%H:%M:%S")
                print("%s  SOC %s%%  %sV  %sA  %skW  เซลล์ %s-%sV (Δ%smV)"
                      "  แบต %s-%s°C  SOH %s%%" % (
                          now, fmt(values.get("soc")),
                          fmt(values.get("pack_voltage")),
                          fmt(values.get("pack_current")),
                          fmt(values.get("pack_power_kw")),
                          fmt(values.get("cell_v_min")),
                          fmt(values.get("cell_v_max")),
                          fmt(values.get("cell_delta_mv")),
                          fmt(values.get("batt_temp_min")),
                          fmt(values.get("batt_temp_max")),
                          fmt(values.get("soh"))))
                if writer:
                    writer.writerow({"time": datetime.datetime.now()
                                     .isoformat(timespec="seconds"),
                                     **values})
                    csv_file.flush()
                count += 1
                if args.count == 0 or count < args.count:
                    time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nหยุดแล้ว")
        finally:
            if csv_file:
                csv_file.close()
    return 0


def cmd_discover(args):
    start, end = int(args.start, 16), int(args.end, 16)
    found = []
    with connect(args) as elm:
        print("\nสแกน DID %04X-%04X ที่ header %s (อ่านอย่างเดียว)\n" % (
            start, end, args.header))
        try:
            for did in range(start, end + 1):
                try:
                    raw = elm.read_did(args.header, did)
                except (NoData, NegativeResponse):
                    continue
                found.append((did, raw))
                print("22%04X  %s" % (did, raw.hex(" ").upper()))
        except KeyboardInterrupt:
            print("\nหยุดแล้ว")
    print("\nพบ %d DID" % len(found))
    if args.save:
        with open(args.save, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["header", "did", "raw"])
            for did, raw in found:
                w.writerow([args.header, "%04X" % did, raw.hex().upper()])
        print("บันทึกไว้ที่ %s" % args.save)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m deepal_s05",
        description="อ่านค่าแบตเตอรี่ Deepal S05 ผ่านกล่อง ELM327 "
                    "(อ่านอย่างเดียว ไม่เขียนค่าใดๆ ลงรถ)")
    p.add_argument("--port", required=True,
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
    d.add_argument("--save", help="บันทึกผลเป็นไฟล์ CSV")
    d.set_defaults(func=cmd_discover)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ElmError as e:
        print("ผิดพลาด: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("เปิดพอร์ต %s ไม่ได้: %s" % (args.port, e), file=sys.stderr)
        return 2
