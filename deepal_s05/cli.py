"""Command line tool: python -m deepal_s05 {check,live,discover} --port ..."""

import argparse
import csv
import datetime
import json
import sys
import time

from . import analysis, pids
from .elm327 import (
    Elm327, ElmError, NegativeResponse, NoData, response_header)

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

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func is not cmd_analyze and not args.port:
        parser.error("ต้องใส่ --port")
    try:
        return args.func(args)
    except ElmError as e:
        print("ผิดพลาด: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("เปิดพอร์ต %s ไม่ได้: %s" % (args.port, e), file=sys.stderr)
        return 2
