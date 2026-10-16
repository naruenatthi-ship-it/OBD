"""Live dashboard in the browser: a small HTTP server on the laptop that
polls the adapter in a background thread and serves /data as JSON."""

import html
import json
import os
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import alerts, analysis, identity, pids, snapshot
from .elm327 import ElmError

LABELS = {
    "soc": ("SOC", "%"), "pack_voltage": ("แรงดันแพ็ก", "V"),
    "pack_current": ("กระแส", "A"), "pack_power_kw": ("กำลัง", "kW"),
    "cell_v_max": ("เซลล์สูงสุด", "V"), "cell_v_min": ("เซลล์ต่ำสุด", "V"),
    "cell_delta_mv": ("ส่วนต่างเซลล์", "mV"),
    "cells_delta_mv": ("ส่วนต่างเซลล์", "mV"),
    "batt_temp_max": ("แบตร้อนสุด", "°C"),
    "batt_temp_min": ("แบตเย็นสุด", "°C"), "soh": ("SOH", "%"),
    "aux_12v": ("แบต 12V", "V"),
    "insulation_ohm_per_v": ("ฉนวน", "Ω/V"),
    "speed_kmh": ("ความเร็ว", "km/h"),
    "odometer_km": ("เลขไมล์", "km"),
}


CELL_IR_FILE = "cell_ir.json"  # written by health --cell-ir


class Poller(threading.Thread):
    def __init__(self, elm, signals, arrays, capacity, interval,
                 csv_log=None, rules=None, notifier=None, info=None,
                 vehicle=None, baselines=None, dc_price=7.5):
        super().__init__(daemon=True)
        self.dc = analysis.DcSession(capacity, dc_price)
        self.baselines = baselines or {}
        self.vehicle = vehicle  # to read the BMS identification once
        self.slow = snapshot.GroupCache()  # per-cell values every 30 s
        self.rules = alerts.DEFAULT_RULES if rules is None else rules
        self.notifier = notifier
        self.elm, self.signals, self.arrays = elm, signals, arrays
        self.capacity, self.interval, self.csv_log = capacity, interval, \
            csv_log
        self.stop = threading.Event()
        self.lock = threading.Lock()
        labels = dict(LABELS)
        labels.update({s.key: (s.label, s.unit) for s in signals
                       if s.status == pids.CUSTOM})
        labels.update({a.key: (a.label, a.unit) for a in arrays})
        self.data = {"time": None, "values": {}, "arrays": {}, "errors": {},
                     "alerts": [],
                     "error": "กำลังอ่านค่าครั้งแรก...", "labels": labels,
                     "custom": [s.key for s in signals
                                if s.status == pids.CUSTOM],
                     "info": info or {}}

    def run(self):
        if self.vehicle is not None and not self.data["info"].get("bms"):
            try:
                _, bms = identity.bms_identity(self.elm, self.vehicle)
                with self.lock:
                    self.data["info"]["bms"] = bms
            except (ElmError, OSError):
                pass
        while not self.stop.is_set():
            try:
                snap = snapshot.take(self.elm, self.signals, self.arrays,
                                     self.capacity, slow=self.slow)
                if self.csv_log:
                    self.csv_log.write(snap)
                values = dict(snap.values)
                values["delta_mv"] = values.get(
                    "cells_delta_mv", values.get("cell_delta_mv"))
                found = alerts.evaluate(
                    alerts.add_baselines(values, self.baselines), self.rules)
                if self.notifier and found:
                    self.notifier.notify(found)
                with self.lock:
                    self.data.update(time=snap.time, values=snap.values,
                                     arrays=snap.arrays, errors=snap.errors,
                                     error=None,
                                     dc=self.dc.update(snap.time,
                                                       snap.values),
                                     alerts=[
                                         {"level": a.level, "text": a.text()}
                                         for a in found])
            except (ElmError, OSError) as e:
                with self.lock:
                    self.data["error"] = "อ่านค่าไม่ได้: %s" % e
            except Exception:  # keep serving; show the problem on the page
                with self.lock:
                    self.data["error"] = traceback.format_exc(limit=1)
            self.stop.wait(self.interval)

    def json(self):
        with self.lock:
            return json.dumps(self.data, ensure_ascii=False)


def list_files(data_dir):
    """Data files offered for download: (name, size), newest first."""
    from . import datafiles
    return [(e["name"], e["size"]) for e in datafiles.entries(data_dir)]


KIND_NOTE = {"history": "ประวัติ ห้ามลบ", "page": "หน้ารายงาน",
             "log": "log", "snapshot": "", "data": ""}


def files_page(data_dir):
    import shutil
    from . import datafiles
    items = datafiles.entries(data_dir)
    total = sum(e["size"] for e in items)
    try:
        free = datafiles.human_size(shutil.disk_usage(data_dir).free)
    except OSError:
        free = "-"
    parts, day = [], None
    for e in items:
        d = e["time"].date()
        if d != day:
            if day is not None:
                parts.append("</ul>")
            parts.append("<h2>%s</h2><ul>" % d.strftime("%d/%m/%Y"))
            day = d
        note = KIND_NOTE[e["kind"]]
        parts.append(
            '<li><span class="t">%s</span> <a href="/files/%s">%s</a> '
            '<small>%s%s</small></li>' % (
                e["time"].strftime("%H:%M"), urllib.parse.quote(e["name"]),
                html.escape(e["name"]), datafiles.human_size(e["size"]),
                " · " + note if note else ""))
    if parts:
        parts.append("</ul>")
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1"><title>Deepal S05 '
            'Files</title><style>body{font-family:sans-serif;padding:16px;'
            'line-height:1.5}h2{font-size:1rem;margin:18px 0 4px}ul{margin:0;'
            'padding-left:18px}.t{font-family:monospace;color:#667}small{'
            'color:#667}</style><body>'
            '<h1>ไฟล์ข้อมูล</h1><p><a href="/">กลับหน้าหลัก</a> · '
            '<b><a href="/digest">สรุปผลสำรวจ (ส่งให้ Claude)</a></b> · '
            '<a href="/trends">กราฟแนวโน้ม</a> · '
            '<a href="/topology">แผนผัง ECU</a> · <a href="/dyno">ไดโน</a> · '
            '<a href="/charge">กราฟชาร์จ</a> · <a href="/freeze">ภาพนิ่งโค้ด</a> · '
            '<a href="/health">เตือนล่วงหน้า</a> · <a href="/cost">ค่าไฟชาร์จ</a></p>'
            '<p><small>เรียงตามเวลาที่บันทึก ใหม่สุดอยู่บน · %d ไฟล์ รวม %s · '
            'การ์ดเหลือที่ว่าง %s · ลบไฟล์ที่ไม่จำเป็นด้วยคำสั่ง obd clean'
            '</small></p>%s</body>'
            % (len(items), datafiles.human_size(total), free,
               "".join(parts) or "<p>ยังไม่มีไฟล์</p>"))


def read_cell_ir(path):
    """The last per-cell IR measurement saved by `health --cell-ir`."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _health_page(data_dir):
    from . import predict
    return predict.predict_html(predict.assess(data_dir))


def _cost_page(data_dir):
    from . import cost
    return cost.cost_html(data_dir)


# scanner-style pages built on request from the files in data_dir
VISUAL_PAGES = {
    "/topology": lambda v, d, car: v.topology_html(v.load_topology(d), car),
    "/dyno": lambda v, d, car: v.dyno_html(v.drive_files(d)),
    "/charge": lambda v, d, car: v.charge_curve_html(v.charge_files(d)),
    "/health": lambda v, d, car: _health_page(d),
    "/cost": lambda v, d, car: _cost_page(d),
    "/freeze": lambda v, d, car: v.freeze_html(
        (v.freeze_files(d) or [None])[-1]),
}


def text_page(name, body, download, sent=None, more=""):
    """A text with a copy-all button and a download link. The page is
    plain http, where the clipboard API is off, so the copy selects a
    textarea and uses execCommand. With `sent`, copying or downloading
    tells the server (GET `sent`) so the next digest starts after it."""
    text = html.escape(body)
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title><style>
body{font-family:-apple-system,sans-serif;margin:12px;background:#fff;color:#111}
button,a.b{font-size:18px;padding:12px 16px;margin:0 8px 10px 0;border-radius:10px;
border:0;background:#0a84ff;color:#fff;text-decoration:none;display:inline-block}
a.b{background:#555}
textarea{width:100%%;height:70vh;font:12px ui-monospace,monospace;box-sizing:border-box}
@media (prefers-color-scheme:dark){body{background:#111;color:#eee}
textarea{background:#222;color:#eee}}
</style></head><body>
<button id="c">คัดลอกทั้งหมด</button>
<a class="b" id="d" href="%s">ดาวน์โหลดไฟล์</a>
<a class="b" href="/files">กลับ</a>%s
<div id="m"></div>
<textarea id="t" readonly>%s</textarea>
<script>
var sent = %s;
function done() { if (sent) { fetch(sent); sent = null; } }
document.getElementById('d').onclick = done;
document.getElementById('c').onclick = function () {
  var t = document.getElementById('t');
  t.removeAttribute('readonly'); t.focus(); t.select();
  t.setSelectionRange(0, t.value.length);
  var ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  t.setAttribute('readonly', ''); t.blur();
  if (ok) done();
  document.getElementById('m').textContent = ok ?
    'คัดลอกแล้ว ไปวางในแชตได้เลย' : 'คัดลอกเองไม่ได้: แตะค้างในกล่อง เลือกทั้งหมด แล้วคัดลอก';
};
</script></body></html>""" % (html.escape(name), html.escape(download),
                              more, text, json.dumps(sent))


def digest_page(data_dir, query):
    """/digest: the survey digest made when the page opens (nothing to
    type on the Pi); copying it marks it sent."""
    from . import survey
    q = urllib.parse.parse_qs(query)
    all_rows = "all" in q
    since = q["since"][0] if "since" in q else None
    text, newest = survey.digest_text(data_dir, all_rows, since=since)
    if text is None:
        text = ("ยังไม่มีผล survey" if not newest else
                "ไม่มีผลใหม่ตั้งแต่ส่งครั้งก่อน (%s)" % newest)
        newest = None
    sent = None if all_rows or not newest else \
        "/digest?sent=" + urllib.parse.quote(newest)
    return text, sent


def make_server(poller, host="127.0.0.1", port=8000, data_dir=None,
                car_name="Deepal S05", ir_path=None, car_key="deepal-s05",
                password=None):
    """`poller` only needs a json() method. With `data_dir`, /files lists
    and serves the data files and /trends draws the trend charts. /ir
    serves the per-cell IR file at `ir_path` (default: in data_dir).
    With `password`, every page asks for it (HTTP basic auth, any user
    name) - for a copy on a server reachable from the internet."""
    import base64
    import hmac
    page = PAGE.replace("Deepal S05", html.escape(car_name))
    if ir_path is None and data_dir:
        ir_path = os.path.join(data_dir, CELL_IR_FILE)

    class Handler(BaseHTTPRequestHandler):
        def send_body(self, body, ctype, extra=()):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if not password:
                return True
            got = self.headers.get("Authorization", "")
            if got.startswith("Basic "):
                try:
                    user_pw = base64.b64decode(got[6:]).decode("utf-8")
                except ValueError:
                    user_pw = ""
                if hmac.compare_digest(user_pw.partition(":")[2].encode(),
                                       password.encode()):
                    return True
            self.send_response(401)
            self.send_header("WWW-Authenticate",
                             'Basic realm="deepal-obd", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def do_GET(self):
            if not self.authorized():
                return
            path = urllib.parse.urlparse(self.path).path
            if path.startswith("/data"):
                self.send_body(poller.json().encode("utf-8"),
                               "application/json; charset=utf-8")
            elif path == "/ir":
                self.send_body(json.dumps(read_cell_ir(ir_path),
                                          ensure_ascii=False).encode("utf-8"),
                               "application/json; charset=utf-8")
            elif path in ("/", "/index.html"):
                self.send_body(page.encode("utf-8"),
                               "text/html; charset=utf-8")
            elif data_dir and path == "/digest":
                from . import survey
                query = urllib.parse.urlparse(self.path).query
                q = urllib.parse.parse_qs(query)
                if "sent" in q:
                    survey.mark_sent(data_dir, q["sent"][0])
                    self.send_body(b"ok", "text/plain; charset=utf-8")
                    return
                text, sent = digest_page(data_dir, query)
                if "dl" in q:
                    self.send_body(text.encode("utf-8"),
                                   "application/octet-stream", [(
                                       "Content-Disposition",
                                       "attachment; filename=%s"
                                       % survey.DIGEST_FILE)])
                    return
                self.send_body(text_page(
                    "สรุปผลสำรวจ", text,
                    "/digest?dl=1" + ("&all=1" if "all" in q else ""), sent,
                    ' <a class="b" href="/digest?all=1">ดูทั้งหมด</a>'
                ).encode("utf-8"), "text/html; charset=utf-8")
            elif data_dir and path in ("/files", "/files/"):
                self.send_body(files_page(data_dir).encode("utf-8"),
                               "text/html; charset=utf-8")
            elif data_dir and path.startswith("/files/"):
                name = urllib.parse.unquote(path[len("/files/"):])
                if name not in dict(list_files(data_dir)):
                    self.send_error(404)
                    return
                with open(os.path.join(data_dir, name), "rb") as f:
                    body = f.read()
                if name.endswith(".html"):  # open saved pages in place
                    self.send_body(body, "text/html; charset=utf-8")
                    return
                query = urllib.parse.urlparse(self.path).query
                if name.endswith(".txt") and "dl" not in query:
                    # one tap to copy on a phone (plain text in Safari
                    # can only be selected line by line)
                    self.send_body(text_page(
                        name, body.decode("utf-8", "replace"),
                        "/files/%s?dl=1" % urllib.parse.quote(name)
                    ).encode("utf-8"), "text/html; charset=utf-8")
                    return
                self.send_body(body, "application/octet-stream", [
                    ("Content-Disposition",
                     "attachment; filename*=UTF-8''%s"
                     % urllib.parse.quote(name))])
            elif data_dir and path in VISUAL_PAGES:
                from . import visual
                try:
                    body = VISUAL_PAGES[path](visual, data_dir, car_key)
                except (OSError, ValueError) as e:
                    body = visual.page("อ่านไฟล์ไม่ได้", html.escape(str(e)))
                self.send_body(body.encode("utf-8"),
                               "text/html; charset=utf-8")
            elif data_dir and path == "/trends":
                from . import report
                out = os.path.join(data_dir, "trends.html")
                try:
                    report.write_trends(
                        os.path.join(data_dir, "checkup_history.csv"),
                        os.path.join(data_dir, "health_history.csv"), out)
                except SystemExit as e:
                    self.send_body(str(e).encode("utf-8"),
                                   "text/plain; charset=utf-8")
                    return
                with open(out, "rb") as f:
                    self.send_body(f.read(), "text/html; charset=utf-8")
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve(elm, signals, arrays, capacity, host="127.0.0.1", port=8000,
          interval=2.0, csv_path=None, rules=None, notifier=None,
          car_name="Deepal S05", info=None, vehicle=None,
          ir_path=CELL_IR_FILE, baselines=None, dc_price=7.5):
    csv_log = snapshot.CsvLog(csv_path) if csv_path else None
    poller = Poller(elm, signals, arrays, capacity, interval, csv_log,
                    rules, notifier, info, vehicle, baselines, dc_price)
    server = make_server(poller, host, port, car_name=car_name,
                         ir_path=ir_path)
    poller.start()
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print("เปิดเบราว์เซอร์ไปที่ http://%s:%d  กด Ctrl+C เพื่อหยุด" % (
        shown, port))
    if host == "0.0.0.0":
        print("ดูจากมือถือในวง WiFi เดียวกันได้ที่ http://<IP ของโน้ตบุ๊ก>:%d"
              % port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nหยุดแล้ว")
    finally:
        poller.stop.set()
        poller.join(timeout=interval + 5)
        server.server_close()
        if csv_log:
            csv_log.__exit__()


PAGE = r"""<!doctype html>
<html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deepal S05 Cell Monitor</title>
<style>
:root { --bg:#eef2f1; --surface:#fff; --ink:#15211f; --muted:#5b6b68;
  --line:#d3dcda; --accent:#0d7a70; --low:#2c68b0; --high:#c2601b;
  --mid:#e6ecea; --bad:#b3261e; --on-bad:#fff; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#0e1514; --surface:#16201e;
  --ink:#e3ebe9; --muted:#93a4a0; --line:#2a3835; --accent:#3fc0b2;
  --low:#6ea5ec; --high:#f0954f; --mid:#22302d; --bad:#e5484d; --on-bad:#fff;
  color-scheme: dark; } }
* { box-sizing: border-box; }
[hidden] { display:none !important; }
body { margin:0; background:var(--bg); color:var(--ink); padding:18px 16px 40px;
  font-family:"IBM Plex Sans Thai","Noto Sans Thai",Tahoma,sans-serif; line-height:1.5; }
.wrap { max-width:1120px; margin:0 auto; display:grid; gap:16px; }
header { display:flex; flex-wrap:wrap; justify-content:space-between; gap:8px; align-items:baseline; }
h1 { margin:0; font-size:1.35rem; } h2 { margin:0; font-size:1rem; font-weight:600; }
.status { font-size:.85rem; color:var(--muted); }
.status.err { color:var(--bad); font-weight:600; }
.summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
  background:var(--surface); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
.stat { padding:12px 14px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); min-width:0; }
.k { font-size:.78rem; color:var(--muted); }
.v { font:600 1.25rem ui-monospace,Menlo,Consolas,monospace; font-variant-numeric:tabular-nums; }
.v small { font-size:.78rem; font-weight:400; color:var(--muted); }
.s { font:.76rem ui-monospace,Menlo,Consolas,monospace; color:var(--muted); }
.stat.bad .v { color:var(--bad); }
.tabs { display:flex; gap:4px; overflow-x:auto; border-bottom:1px solid var(--line); }
.tabs button { font:inherit; font-size:.92rem; padding:8px 14px; border:0; background:none;
  color:var(--muted); cursor:pointer; border-bottom:3px solid transparent; white-space:nowrap; }
.tabs button.on { color:var(--ink); font-weight:600; border-bottom-color:var(--accent); }
.tabs button:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
.panel { background:var(--surface); border:1px solid var(--line); border-radius:12px;
  padding:14px; display:grid; gap:10px; min-width:0; }
#cellsBody, #irBody { display:grid; gap:10px; min-width:0; }
.meta { font-size:.85rem; color:var(--muted); }
.meta b { color:var(--ink); font-weight:600; }
.meta .bad { color:var(--bad); font-weight:600; }
.cells { display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:3px; }
.cell { border-radius:4px; padding:5px 0 4px; text-align:center; background:var(--mid);
  font:500 .76rem ui-monospace,Menlo,Consolas,monospace; line-height:1.15; }
.cell b { display:block; font-size:.62rem; font-weight:400; opacity:.7; }
.cell.ext { outline:2px solid var(--ink); outline-offset:-2px; }
.cell.abn { background:var(--bad) !important; color:var(--on-bad); }
.cell.abn b { opacity:.9; }
.legend { display:flex; flex-wrap:wrap; gap:6px 16px; font-size:.78rem; color:var(--muted); }
.legend i { display:inline-block; width:12px; height:12px; border-radius:3px; vertical-align:-1px; margin-right:5px; }
.temps { display:grid; grid-template-columns:repeat(auto-fill,minmax(72px,1fr)); gap:6px; }
.temp { border-radius:6px; padding:6px 8px; background:var(--mid);
  font:600 .95rem ui-monospace,Menlo,Consolas,monospace; }
.temp b { display:block; font-size:.68rem; font-weight:400; color:var(--muted); }
.chart { overflow-x:auto; } .chart svg { width:100%; min-width:540px; height:auto; display:block; }
.chart text { fill:var(--muted); font:11px ui-monospace,Menlo,Consolas,monospace; }
.note { font-size:.86rem; color:var(--muted); max-width:70ch; margin:0; }
.note code { font-size:.8rem; }
.facts { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:0; margin:0; }
.facts div { padding:8px 0; border-bottom:1px solid var(--line); }
.facts dt { font-size:.78rem; color:var(--muted); }
.facts dd { margin:0; font:600 .95rem ui-monospace,Menlo,Consolas,monospace; overflow-wrap:anywhere; }
.alerts { display:grid; gap:6px; }
.alert { border-radius:10px; padding:10px 12px; border:1px solid var(--line);
  background:var(--surface); font-size:.92rem; border-left:5px solid var(--high); }
.alert.crit { border-left-color:var(--bad); font-weight:600; }
.dc { border:1px solid var(--line); border-left:6px solid var(--mid); border-radius:10px; padding:10px 14px; margin:10px 0; background:var(--surface); }
.dc.station { border-left-color:var(--bad); } .dc.battery_hot { border-left-color:var(--bad); }
.dc .big { display:flex; gap:20px; flex-wrap:wrap; font-size:1.5rem; font-weight:600; } .dc .big small { font-size:.8rem; font-weight:400; display:block; }
.dc p { margin:6px 0 0; }
@media (max-width:620px) { .cells { grid-template-columns:repeat(6,minmax(0,1fr)); } }
</style></head><body><div class="wrap">
<header><h1>Deepal S05 · แบตเตอรี่</h1><div class="status" id="status">กำลังเชื่อมต่อ...</div>
<nav class="status" id="links" hidden><a href="/files">ไฟล์ข้อมูล</a> · <a href="/digest">สรุปผลสำรวจ</a> · <a href="/trends">กราฟแนวโน้ม</a> · <a href="/topology">แผนผัง ECU</a> · <a href="/dyno">ไดโน</a> · <a href="/charge">กราฟชาร์จ</a> · <a href="/freeze">ภาพนิ่งโค้ด</a> · <a href="/health">เตือนล่วงหน้า</a> · <a href="/cost">ค่าไฟชาร์จ</a></nav></header>
<div class="alerts" id="alerts"></div>
<section class="dc" id="dcCard" hidden></section>
<div class="summary" id="summary"></div>
<nav class="tabs" role="tablist" aria-label="รายละเอียด">
<button role="tab" data-tab="cells" class="on">แรงดันเซลล์</button>
<button role="tab" data-tab="temps">อุณหภูมิ</button>
<button role="tab" data-tab="ir">IR เซลล์</button>
<button role="tab" data-tab="pack">ข้อมูลแพ็ก</button>
</nav>
<section class="panel" role="tabpanel" id="tab-cells">
<h2>แรงดันรายเซลล์</h2>
<div id="cellsBody" hidden>
<div class="meta" id="cellsMeta"></div>
<div class="cells" id="cells"></div>
<div class="legend" id="cellsLegend"></div>
<div class="chart"><svg id="bars" viewBox="0 0 1100 170" role="img" aria-label="ส่วนต่างของแต่ละเซลล์จากค่าเฉลี่ย"></svg></div>
</div>
<p class="note" id="cellsNote">ยังอ่านแรงดันรายเซลล์ไม่ได้ ตอนนี้แสดงได้แค่เซลล์สูงสุด/ต่ำสุดด้านบน แผนผังจะขึ้นเมื่อเพิ่ม array ชื่อ "cells" ในไฟล์ --signals</p>
</section>
<section class="panel" role="tabpanel" id="tab-temps" hidden>
<h2>อุณหภูมิแบต</h2>
<div class="meta" id="tempsMeta"></div>
<div class="temps" id="temps"></div>
<p class="note" id="tempsNote">ยังอ่านอุณหภูมิรายจุดไม่ได้ แผนผังจะขึ้นเมื่อเพิ่ม array ชื่อ "temps" ในไฟล์ --signals</p>
</section>
<section class="panel" role="tabpanel" id="tab-ir" hidden>
<h2>ความต้านทานภายใน (IR) รายเซลล์</h2>
<div id="irBody"></div>
<p class="note">วัดด้วย <code>python -m deepal_s05 health --ir 60 --cell-ir</code> ระหว่างที่กระแสเปลี่ยนมากๆ เช่น ให้คนอื่นขับแล้วเร่งสลับกับปล่อยคันเร่ง
ค่าที่สูงกว่าเซลล์อื่นชัดเจนคือเซลล์ที่เริ่มเสื่อม ค่าเดี่ยวๆ ไม่แม่นนัก ให้ดูเทียบหลายครั้ง</p>
</section>
<section class="panel" role="tabpanel" id="tab-pack" hidden>
<h2>ข้อมูลแพ็กและ BMS</h2>
<dl class="facts" id="facts"></dl>
<p class="note">แรงดัน/ความจุพิกัดเป็นค่าประมาณจากจำนวนเซลล์ × แรงดันระบุของเซลล์ ไม่ใช่ค่าที่ BMS รายงาน</p>
</section>
</div>
<script>
var css = getComputedStyle(document.documentElement);
function tok(n) { return css.getPropertyValue(n).trim(); }
function rgb(h) { h = h.replace('#',''); return [0,2,4].map(function(p){ return parseInt(h.substr(p,2),16); }); }
function mix(a,b,t) { var A=rgb(a),B=rgb(b); return 'rgb('+A.map(function(v,i){ return Math.round(v+(B[i]-v)*t); }).join(',')+')'; }
function f(v,d) { return v===undefined||v===null ? '-' : (typeof v==='number' ? v.toFixed(d) : v); }
function esc(t) { var e = document.createElement('div'); e.textContent = t; return e.innerHTML; }
function stat(k,v,u,s,bad) { return '<div class="stat'+(bad?' bad':'')+'"><div class="k">'+k+'</div><div class="v">'+v+' <small>'+u+'</small></div><div class="s">'+(s||'&nbsp;')+'</div></div>'; }
function median(a) { var b = a.filter(function(x){ return typeof x==='number'; }).sort(function(x,y){ return x-y; }); var n=b.length; return n ? (n%2 ? b[(n-1)/2] : (b[n/2-1]+b[n/2])/2) : null; }

var last = {}, ir = null, tab = 'cells';
try { tab = localStorage.getItem('tab') || 'cells'; } catch (e) {}
function show(name) {
  tab = name;
  document.querySelectorAll('.tabs button').forEach(function(b){ var on = b.dataset.tab===name; b.className = on ? 'on' : ''; b.setAttribute('aria-selected', on); });
  ['cells','temps','ir','pack'].forEach(function(t){ document.getElementById('tab-'+t).hidden = t!==name; });
  try { localStorage.setItem('tab', name); } catch (e) {}
  if (name==='ir') loadIr();
  if (name==='cells' && last.arrays) render(last);
}
document.querySelectorAll('.tabs button').forEach(function(b){ b.addEventListener('click', function(){ show(b.dataset.tab); }); });

function render(d) {
  last = d;
  var st = document.getElementById('status');
  var age = d.time ? (Date.now()/1000 - d.time) : null;
  if (d.error) { st.textContent = d.error; st.className = 'status err'; }
  else if (!d.time) { st.textContent = d.status || 'รอข้อมูล...'; st.className = 'status'; }
  else { st.textContent = (d.status ? d.status + ' · ' : '') + 'อัปเดต ' + new Date(d.time*1000).toLocaleTimeString('th-TH') + (age > 15 && !d.status ? ' (ข้อมูลเก่า)' : ''); st.className = 'status' + (age > 15 && !d.status ? ' err' : ''); }
  document.getElementById('links').hidden = !d.files;
  document.getElementById('alerts').innerHTML = (d.alerts||[]).map(function(a){
    return '<div class="alert'+(a.level>=3?' crit':'')+'">'+esc(a.text)+'</div>';
  }).join('');
  var v = d.values || {}, info = d.info || {}, cells = (d.arrays||{}).cells, temps = (d.arrays||{}).temps;
  var abnMv = info.abnormal_mv || 30;
  var hi = cells ? v.cells_max : v.cell_v_max, lo = cells ? v.cells_min : v.cell_v_min;
  var delta = v.cells_delta_mv !== undefined ? v.cells_delta_mv : v.cell_delta_mv;
  var mean = cells ? cells.reduce(function(a,b){return a+b;},0)/cells.length : null;
  var abn = cells ? cells.map(function(c,i){ return Math.abs(c-mean)*1000 > abnMv ? i : -1; }).filter(function(i){ return i>=0; }) : [];
  var html = stat('SOC', f(v.soc,1), '%') + stat('แรงดันแพ็ก', f(v.pack_voltage,1), 'V') +
    stat('กระแส', f(v.pack_current,1), 'A') + stat('กำลัง', f(v.pack_power_kw,2), 'kW') +
    stat('เซลล์สูงสุด', f(hi,3), 'V', cells ? 'เซลล์ #'+v.cells_max_no : '') +
    stat('เซลล์ต่ำสุด', f(lo,3), 'V', cells ? 'เซลล์ #'+v.cells_min_no : '') +
    stat('ส่วนต่าง', f(delta,1), 'mV', abn.length ? 'ผิดปกติ '+abn.length+' เซลล์' : '', abn.length>0) +
    stat('อุณหภูมิแบต', f(v.batt_temp_min,0)+'–'+f(v.batt_temp_max,0), '°C');
  if ('soh' in v) html += stat('SOH', f(v.soh,1), '%');
  html += stat('แบต 12V', f(v.aux_12v,2), 'V');
  if (v.motor_temp !== undefined) html += stat('มอเตอร์', f(v.motor_temp,0), '°C', 'อินเวอร์เตอร์ '+f(v.inverter_temp,0)+' °C', v.motor_temp > 110 || v.inverter_temp > 85);
  if (v.motor_rpm !== undefined) html += stat('ความเร็ว', f(v.speed_kmh,0), 'km/h', f(v.motor_rpm,0)+' rpm · '+f(v.motor_torque,0)+' Nm');
  if (v.insulation_ohm_per_v !== undefined) html += stat('ฉนวน', f(v.insulation_ohm_per_v,0), 'Ω/V', f(v.insulation_kohm,0)+' kΩ', v.insulation_ohm_per_v < 500);
  (d.custom||[]).forEach(function(k){ if (k==='insulation_kohm') return; var l = d.labels[k]||[k,'']; html += stat(esc(l[0]), f(v[k],1), esc(l[1]), d.errors[k] ? 'ไม่ตอบ' : ''); });
  document.getElementById('summary').innerHTML = html;

  document.getElementById('cellsBody').hidden = !cells;
  document.getElementById('cellsNote').hidden = !!cells;
  if (cells && tab==='cells') {
    var box = document.getElementById('cells');
    if (box.children.length !== cells.length) {
      box.innerHTML = cells.map(function(_,i){ return '<div class="cell"><b>'+(i+1)+'</b><span></span></div>'; }).join('');
    }
    cells.forEach(function(c,i){
      var el = box.children[i], dv = (c-mean)*1000, t = Math.max(-1,Math.min(1,dv/5));
      el.style.background = t<0 ? mix(tok('--mid'),tok('--low'),-t*.85) : mix(tok('--mid'),tok('--high'),t*.85);
      el.lastChild.textContent = c.toFixed(3);
      el.title = 'เซลล์ #'+(i+1)+' '+c.toFixed(3)+' V ('+(dv>=0?'+':'')+dv.toFixed(1)+' mV จากค่าเฉลี่ย)';
      el.className = 'cell' + (i+1===v.cells_max_no || i+1===v.cells_min_no ? ' ext' : '') + (abn.indexOf(i)>=0 ? ' abn' : '');
    });
    document.getElementById('cellsMeta').innerHTML = '<b>'+cells.length+'</b> เซลล์ · เฉลี่ย <b>'+mean.toFixed(3)+'</b> V · สูงสุด #'+v.cells_max_no+' <b>'+f(hi,3)+'</b> · ต่ำสุด #'+v.cells_min_no+' <b>'+f(lo,3)+'</b> · ส่วนต่าง <b>'+f(delta,1)+'</b> mV' +
      (abn.length ? ' · <span class="bad">ผิดปกติ '+abn.length+' เซลล์: '+abn.map(function(i){ return '#'+(i+1); }).join(', ')+'</span>' : '') +
      (v.soc >= 95 && info.cell_nominal_v && info.cell_nominal_v < 3.3 ? '<br>SOC ใกล้เต็ม: แบต LFP แรงดันชันมากช่วงนี้ เซลล์จะดูห่างกันกว่าปกติ (โดยเฉพาะตอนเพิ่งชาร์จ) ให้ดูเทียบหลังพักรถ และดูว่าเซลล์ชุดเดิมต่ำซ้ำทุกครั้งหรือไม่' : '');
    document.getElementById('cellsLegend').innerHTML =
      '<span><i style="background:'+mix(tok('--mid'),tok('--low'),.85)+'"></i>ต่ำกว่าค่าเฉลี่ย</span>' +
      '<span><i style="background:'+mix(tok('--mid'),tok('--high'),.85)+'"></i>สูงกว่าค่าเฉลี่ย</span>' +
      '<span><i style="outline:2px solid '+tok('--ink')+';outline-offset:-2px"></i>สูงสุด/ต่ำสุด</span>' +
      '<span><i style="background:'+tok('--bad')+'"></i>ผิดปกติ (ห่างค่าเฉลี่ยเกิน '+abnMv+' mV)</span>';
    bars(cells.map(function(c){ return (c-mean)*1000; }), v.cells_max_no-1, v.cells_min_no-1, abn);
  }
  document.getElementById('temps').hidden = !temps;
  document.getElementById('tempsNote').hidden = !!temps;
  document.getElementById('tempsMeta').innerHTML = 'ต่ำสุด <b>'+f(v.batt_temp_min,1)+'</b> °C · สูงสุด <b>'+f(v.batt_temp_max,1)+'</b> °C · ต่างกัน <b>'+f(v.temp_delta_c,1)+'</b> °C';
  if (temps) {
    var tmin = Math.min.apply(null,temps), tmax = Math.max.apply(null,temps);
    document.getElementById('temps').innerHTML = temps.map(function(t,i){
      var k = tmax>tmin ? (t-tmin)/(tmax-tmin) : 0;
      return '<div class="temp" style="background:'+mix(tok('--mid'),tok('--high'),.15+k*.6)+'"><b>T'+(i+1)+(t===tmax&&tmax>tmin?' · สูงสุด':t===tmin&&tmax>tmin?' · ต่ำสุด':'')+'</b>'+t.toFixed(1)+'°</div>';
    }).join('');
  }
  renderPack(v, info, cells);
  renderDc(d.dc);
}

function renderDc(dc) {
  var box = document.getElementById('dcCard');
  box.hidden = !dc;
  if (!dc) return;
  box.className = 'dc ' + dc.state;
  var eta = dc.reached ? 'ถึง '+dc.target+'% แล้ว' : (dc.eta_min===null ? 'ยังประเมินไม่ได้' : 'ถึง '+dc.target+'% อีก ~'+Math.round(dc.eta_min)+' นาที');
  box.innerHTML = '<b>⚡ ชาร์จเร็ว DC</b><div class="big"><div>'+f(dc.accept_kw,1)+' kW<small>แบตรับได้ตอนนี้ (F233)</small></div><div>'+f(dc.supply_kw,1)+' kW<small>ตู้จ่ายจริง</small></div></div>'+
    '<p>'+esc(dc.text)+'</p><p>'+eta+' · ชาร์จแล้ว '+f(dc.kwh,1)+' kWh ≈ <b>'+f(dc.baht,0)+'</b> บาท (ที่ '+f(dc.price,2)+' บาท/kWh)</p>'+
    '<p class="note">เวลาประเมินจากกำลังเฉลี่ย 2 นาทีล่าสุด จริงจะนานกว่านี้เมื่อ SOC สูงเพราะกำลังลดลง · F233 ยังเป็นค่าที่รอยืนยัน</p>';
}

function renderPack(v, info, cells) {
  var b = info.bms || {}, rows = [['รถ', info.car], ['ความจุที่ตั้งไว้', f(info.capacity_kwh,2)+' kWh']];
  if (cells) {
    rows.push(['จำนวนเซลล์', cells.length]);
    if (info.cell_nominal_v) {
      var rv = cells.length*info.cell_nominal_v;
      rows.push(['แรงดันพิกัด (ประมาณ)', rv.toFixed(1)+' V ('+cells.length+' × '+info.cell_nominal_v+' V)']);
      rows.push(['ความจุพิกัด (ประมาณ)', (info.capacity_kwh*1000/rv).toFixed(1)+' Ah']);
    }
  }
  rows.push(['แรงดันแพ็กตอนนี้', f(v.pack_voltage,1)+' V']);
  if ('soh' in v) rows.push(['SOH', f(v.soh,1)+' %']);
  if (v.insulation_kohm !== undefined) rows.push(['ความต้านทานฉนวน', f(v.insulation_kohm,0)+' kΩ ('+f(v.insulation_ohm_per_v,0)+' Ω/V)']);
  [['part_number','รหัสชิ้นส่วน BMS'],['supplier','ผู้ผลิต BMS'],['system_name','ชื่อระบบ'],['hw_number','เลขฮาร์ดแวร์'],['hw_version','เวอร์ชันฮาร์ดแวร์'],['sw_version','เวอร์ชันซอฟต์แวร์']].forEach(function(r){ if (b[r[0]]) rows.push([r[1], b[r[0]]]); });
  if (!b.part_number) rows.push(['รหัส BMS', 'ยังอ่านไม่ได้']);
  document.getElementById('facts').innerHTML = rows.map(function(r){ return '<div><dt>'+esc(r[0])+'</dt><dd>'+esc(String(r[1]===undefined?'-':r[1]))+'</dd></div>'; }).join('');
}

function loadIr() {
  fetch('/ir', {cache:'no-store'}).then(function(r){ return r.json(); }).then(function(d){ ir = d; renderIr(); }).catch(function(){ ir = {}; renderIr(); });
}
function renderIr() {
  var body = document.getElementById('irBody'), cells = (ir||{}).cells;
  if (!cells || !cells.length) { body.innerHTML = '<p class="note">ยังไม่มีผลวัด IR รายเซลล์</p>'; return; }
  var med = median(cells);
  var high = cells.map(function(c,i){ return typeof c==='number' && med && c > med*1.5 ? i : -1; }).filter(function(i){ return i>=0; });
  var html = '<div class="meta">วัดเมื่อ <b>'+esc(String(ir.time||'-').replace('T',' '))+'</b> · IR แพ็ก <b>'+f(ir.pack_mohm,0)+'</b> mΩ (R² '+f(ir.r2,2)+') · มัธยฐานรายเซลล์ <b>'+f(med,2)+'</b> mΩ' +
    (high.length ? ' · <span class="bad">สูงผิดปกติ '+high.length+' เซลล์: '+high.map(function(i){ return '#'+(i+1); }).join(', ')+'</span>' : '') + '</div><div class="cells">';
  cells.forEach(function(c,i){
    var t = typeof c==='number' && med ? Math.max(-1,Math.min(1,(c-med)/med*2)) : 0;
    var bg = t<0 ? mix(tok('--mid'),tok('--low'),-t*.7) : mix(tok('--mid'),tok('--high'),t*.85);
    html += '<div class="cell'+(high.indexOf(i)>=0?' abn':'')+'" style="background:'+bg+'" title="เซลล์ #'+(i+1)+'"><b>'+(i+1)+'</b>'+f(c,2)+'</div>';
  });
  body.innerHTML = html + '</div><div class="legend"><span><i style="background:'+tok('--bad')+'"></i>สูงกว่ามัธยฐานเกิน 1.5 เท่า</span><span>หน่วย mΩ</span></div>';
}

function bars(dev, max, min, abn) {
  var W=1100,H=170,L=40,R=8,T=10,B=24,n=dev.length;
  var lim = Math.max(3, Math.ceil(Math.max.apply(null, dev.map(Math.abs))));
  var ih=H-T-B, bw=(W-L-R)/n, out='';
  function y(v){ return T+ih/2-(v/lim)*(ih/2); }
  [-lim,0,lim].forEach(function(g){
    out += '<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y(g)+'" y2="'+y(g)+'" stroke="'+tok('--line')+'"/>';
    out += '<text x="'+(L-6)+'" y="'+(y(g)+4)+'" text-anchor="end">'+(g>0?'+':'')+g+'</text>';
  });
  dev.forEach(function(d,i){
    var y0=y(0), y1=y(d), fill = abn.indexOf(i)>=0 ? tok('--bad') : i===max||i===min ? tok('--ink') : (d<0 ? tok('--low') : tok('--high'));
    out += '<rect x="'+(L+i*bw+1).toFixed(1)+'" y="'+Math.min(y0,y1).toFixed(1)+'" width="'+Math.max(1,bw-2).toFixed(1)+'" height="'+Math.max(.8,Math.abs(y1-y0)).toFixed(1)+'" fill="'+fill+'"/>';
  });
  out += '<text x="'+L+'" y="'+(H-6)+'">mV จากค่าเฉลี่ย · เซลล์ 1–'+n+'</text>';
  document.getElementById('bars').innerHTML = out;
}

function poll() {
  fetch('/data', {cache:'no-store'}).then(function(r){ return r.json(); }).then(render)
    .catch(function(){ var st=document.getElementById('status'); st.textContent='ติดต่อโปรแกรมไม่ได้ (ปิดไปแล้ว?)'; st.className='status err'; });
}
show(['cells','temps','ir','pack'].indexOf(tab)>=0 ? tab : 'cells');
poll(); setInterval(poll, 2000);
</script></body></html>
"""
