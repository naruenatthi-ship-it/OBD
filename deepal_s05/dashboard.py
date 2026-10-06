"""Live dashboard in the browser: a small HTTP server on the laptop that
polls the adapter in a background thread and serves /data as JSON."""

import html
import json
import os
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import alerts, pids, snapshot
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
}


class Poller(threading.Thread):
    def __init__(self, elm, signals, arrays, capacity, interval,
                 csv_log=None, rules=None, notifier=None):
        super().__init__(daemon=True)
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
                                if s.status == pids.CUSTOM]}

    def run(self):
        while not self.stop.is_set():
            try:
                snap = snapshot.take(self.elm, self.signals, self.arrays,
                                     self.capacity)
                if self.csv_log:
                    self.csv_log.write(snap)
                values = dict(snap.values)
                values["delta_mv"] = values.get(
                    "cells_delta_mv", values.get("cell_delta_mv"))
                found = alerts.evaluate(values, self.rules)
                if self.notifier and found:
                    self.notifier.notify(found)
                with self.lock:
                    self.data.update(time=snap.time, values=snap.values,
                                     arrays=snap.arrays, errors=snap.errors,
                                     error=None, alerts=[
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
    out = []
    for name in os.listdir(data_dir):
        path = os.path.join(data_dir, name)
        if os.path.isfile(path) and name.endswith((".csv", ".json", ".log",
                                                   ".html")):
            out.append((name, os.path.getsize(path), os.path.getmtime(path)))
    out.sort(key=lambda f: -f[2])
    return [(n, size) for n, size, _ in out]


def files_page(data_dir):
    rows = "".join(
        '<li><a href="/files/%s">%s</a> <small>%.1f KB</small></li>' % (
            urllib.parse.quote(n), html.escape(n), size / 1024)
        for n, size in list_files(data_dir))
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1"><title>Deepal S05 '
            'Files</title><body style="font-family:sans-serif;padding:16px">'
            '<h1>ไฟล์ข้อมูล</h1><p><a href="/">กลับหน้าหลัก</a> · '
            '<a href="/trends">กราฟแนวโน้ม</a></p><ul>%s</ul></body>'
            % (rows or "<li>ยังไม่มีไฟล์</li>"))


def make_server(poller, host="127.0.0.1", port=8000, data_dir=None,
                car_name="Deepal S05"):
    """`poller` only needs a json() method. With `data_dir`, /files lists
    and serves the data files and /trends draws the trend charts."""
    page = PAGE.replace("Deepal S05", html.escape(car_name))

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

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path.startswith("/data"):
                self.send_body(poller.json().encode("utf-8"),
                               "application/json; charset=utf-8")
            elif path in ("/", "/index.html"):
                self.send_body(page.encode("utf-8"),
                               "text/html; charset=utf-8")
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
                self.send_body(body, "application/octet-stream", [
                    ("Content-Disposition",
                     "attachment; filename*=UTF-8''%s"
                     % urllib.parse.quote(name))])
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
          car_name="Deepal S05"):
    csv_log = snapshot.CsvLog(csv_path) if csv_path else None
    poller = Poller(elm, signals, arrays, capacity, interval, csv_log,
                    rules, notifier)
    server = make_server(poller, host, port, car_name=car_name)
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
  --mid:#e6ecea; --bad:#b3261e; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#0e1514; --surface:#16201e;
  --ink:#e3ebe9; --muted:#93a4a0; --line:#2a3835; --accent:#3fc0b2;
  --low:#6ea5ec; --high:#f0954f; --mid:#22302d; --bad:#f2827a;
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
  background:var(--surface); border:1px solid var(--line); border-radius:12px; }
.stat { padding:12px 14px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); min-width:0; }
.k { font-size:.78rem; color:var(--muted); }
.v { font:600 1.25rem ui-monospace,Menlo,Consolas,monospace; font-variant-numeric:tabular-nums; }
.v small { font-size:.78rem; font-weight:400; color:var(--muted); }
.s { font:.76rem ui-monospace,Menlo,Consolas,monospace; color:var(--muted); }
.panel { background:var(--surface); border:1px solid var(--line); border-radius:12px;
  padding:14px; display:grid; gap:10px; min-width:0; }
.cells { display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:3px; }
.cell { border-radius:4px; padding:5px 0 4px; text-align:center; background:var(--mid);
  font:500 .76rem ui-monospace,Menlo,Consolas,monospace; line-height:1.15; }
.cell b { display:block; font-size:.62rem; font-weight:400; opacity:.7; }
.cell.ext { outline:2px solid var(--ink); outline-offset:-2px; }
.temps { display:grid; grid-template-columns:repeat(auto-fill,minmax(72px,1fr)); gap:6px; }
.temp { border-radius:6px; padding:6px 8px; background:var(--mid);
  font:600 .95rem ui-monospace,Menlo,Consolas,monospace; }
.temp b { display:block; font-size:.68rem; font-weight:400; color:var(--muted); }
.chart { overflow-x:auto; } .chart svg { width:100%; min-width:540px; height:auto; display:block; }
.chart text { fill:var(--muted); font:11px ui-monospace,Menlo,Consolas,monospace; }
.note { font-size:.86rem; color:var(--muted); max-width:70ch; }
.alerts { display:grid; gap:6px; }
.alert { border-radius:10px; padding:10px 12px; border:1px solid var(--line);
  background:var(--surface); font-size:.92rem; border-left:5px solid var(--high); }
.alert.crit { border-left-color:var(--bad); font-weight:600; }
@media (max-width:620px) { .cells { grid-template-columns:repeat(6,minmax(0,1fr)); } }
</style></head><body><div class="wrap">
<header><h1>Deepal S05 · แบตเตอรี่</h1><div class="status" id="status">กำลังเชื่อมต่อ...</div>
<nav class="status" id="links" hidden><a href="/files">ไฟล์ข้อมูล</a> · <a href="/trends">กราฟแนวโน้ม</a></nav></header>
<div class="alerts" id="alerts"></div>
<div class="summary" id="summary"></div>
<section class="panel" id="cellsPanel" hidden><h2 id="cellsTitle">แรงดันรายเซลล์</h2>
<div class="cells" id="cells"></div>
<div class="chart"><svg id="bars" viewBox="0 0 1100 170"></svg></div></section>
<section class="panel" id="tempsPanel" hidden><h2>เซนเซอร์อุณหภูมิ</h2><div class="temps" id="temps"></div></section>
<p class="note" id="note">ยังไม่ได้ตั้งค่าแรงดันรายเซลล์ แผนผังเซลล์จะขึ้นเมื่อเพิ่ม array ชื่อ "cells" ในไฟล์ --signals</p>
</div>
<script>
var css = getComputedStyle(document.documentElement);
function tok(n) { return css.getPropertyValue(n).trim(); }
function rgb(h) { h = h.replace('#',''); return [0,2,4].map(function(p){ return parseInt(h.substr(p,2),16); }); }
function mix(a,b,t) { var A=rgb(a),B=rgb(b); return 'rgb('+A.map(function(v,i){ return Math.round(v+(B[i]-v)*t); }).join(',')+')'; }
function f(v,d) { return v===undefined||v===null ? '-' : (typeof v==='number' ? v.toFixed(d) : v); }
function stat(k,v,u,s) { return '<div class="stat"><div class="k">'+k+'</div><div class="v">'+v+' <small>'+u+'</small></div><div class="s">'+(s||'&nbsp;')+'</div></div>'; }

function render(d) {
  var st = document.getElementById('status');
  var age = d.time ? (Date.now()/1000 - d.time) : null;
  if (d.error) { st.textContent = d.error; st.className = 'status err'; }
  else if (!d.time) { st.textContent = d.status || 'รอข้อมูล...'; st.className = 'status'; }
  else { st.textContent = (d.status ? d.status + ' · ' : '') + 'อัปเดต ' + new Date(d.time*1000).toLocaleTimeString('th-TH') + (age > 15 && !d.status ? ' (ข้อมูลเก่า)' : ''); st.className = 'status' + (age > 15 && !d.status ? ' err' : ''); }
  document.getElementById('links').hidden = !d.files;
  document.getElementById('alerts').innerHTML = (d.alerts||[]).map(function(a){
    var div = document.createElement('div'); div.textContent = a.text;
    return '<div class="alert'+(a.level>=3?' crit':'')+'">'+div.innerHTML+'</div>';
  }).join('');
  var v = d.values || {}, cells = (d.arrays||{}).cells, temps = (d.arrays||{}).temps;
  var hi = cells ? v.cells_max : v.cell_v_max, lo = cells ? v.cells_min : v.cell_v_min;
  var delta = v.cells_delta_mv !== undefined ? v.cells_delta_mv : v.cell_delta_mv;
  var html = stat('SOC', f(v.soc,1), '%') + stat('แรงดันแพ็ก', f(v.pack_voltage,1), 'V') +
    stat('กระแส', f(v.pack_current,1), 'A') + stat('กำลัง', f(v.pack_power_kw,2), 'kW') +
    stat('เซลล์สูงสุด', f(hi,3), 'V', cells ? 'เซลล์ #'+v.cells_max_no : '') +
    stat('เซลล์ต่ำสุด', f(lo,3), 'V', cells ? 'เซลล์ #'+v.cells_min_no : '') +
    stat('ส่วนต่าง', f(delta,1), 'mV') +
    stat('อุณหภูมิแบต', f(v.batt_temp_min,0)+'–'+f(v.batt_temp_max,0), '°C') +
    stat('SOH', f(v.soh,1), '%') + stat('แบต 12V', f(v.aux_12v,2), 'V');
  (d.custom||[]).forEach(function(k){ var l = d.labels[k]||[k,'']; html += stat(l[0], f(v[k],1), l[1], d.errors[k] ? 'ไม่ตอบ' : ''); });
  document.getElementById('summary').innerHTML = html;

  document.getElementById('cellsPanel').hidden = !cells;
  document.getElementById('note').hidden = !!cells;
  if (cells) {
    var mean = cells.reduce(function(a,b){return a+b;},0)/cells.length;
    var box = document.getElementById('cells');
    if (box.children.length !== cells.length) {
      box.innerHTML = cells.map(function(_,i){ return '<div class="cell"><b>'+(i+1)+'</b><span></span></div>'; }).join('');
    }
    cells.forEach(function(c,i){
      var el = box.children[i], dv = (c-mean)*1000, t = Math.max(-1,Math.min(1,dv/5));
      el.style.background = t<0 ? mix(tok('--mid'),tok('--low'),-t*.85) : mix(tok('--mid'),tok('--high'),t*.85);
      el.lastChild.textContent = c.toFixed(3);
      el.className = 'cell' + (i+1===v.cells_max_no || i+1===v.cells_min_no ? ' ext' : '');
    });
    bars(cells.map(function(c){ return (c-mean)*1000; }), v.cells_max_no-1, v.cells_min_no-1);
  }
  document.getElementById('tempsPanel').hidden = !temps;
  if (temps) {
    var tmin = Math.min.apply(null,temps), tmax = Math.max.apply(null,temps);
    document.getElementById('temps').innerHTML = temps.map(function(t,i){
      var k = tmax>tmin ? (t-tmin)/(tmax-tmin) : 0;
      return '<div class="temp" style="background:'+mix(tok('--mid'),tok('--high'),.15+k*.6)+'"><b>T'+(i+1)+'</b>'+t.toFixed(1)+'°</div>';
    }).join('');
  }
}

function bars(dev, max, min) {
  var W=1100,H=170,L=40,R=8,T=10,B=24,n=dev.length;
  var lim = Math.max(3, Math.ceil(Math.max.apply(null, dev.map(Math.abs))));
  var ih=H-T-B, bw=(W-L-R)/n, out='';
  function y(v){ return T+ih/2-(v/lim)*(ih/2); }
  [-lim,0,lim].forEach(function(g){
    out += '<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y(g)+'" y2="'+y(g)+'" stroke="'+tok('--line')+'"/>';
    out += '<text x="'+(L-6)+'" y="'+(y(g)+4)+'" text-anchor="end">'+(g>0?'+':'')+g+'</text>';
  });
  dev.forEach(function(d,i){
    var y0=y(0), y1=y(d), fill = i===max||i===min ? tok('--ink') : (d<0 ? tok('--low') : tok('--high'));
    out += '<rect x="'+(L+i*bw+1).toFixed(1)+'" y="'+Math.min(y0,y1).toFixed(1)+'" width="'+Math.max(1,bw-2).toFixed(1)+'" height="'+Math.max(.8,Math.abs(y1-y0)).toFixed(1)+'" fill="'+fill+'"/>';
  });
  out += '<text x="'+L+'" y="'+(H-6)+'">mV จากค่าเฉลี่ย · เซลล์ 1–'+n+'</text>';
  document.getElementById('bars').innerHTML = out;
}

function poll() {
  fetch('/data', {cache:'no-store'}).then(function(r){ return r.json(); }).then(render)
    .catch(function(){ var st=document.getElementById('status'); st.textContent='ติดต่อโปรแกรมไม่ได้ (ปิดไปแล้ว?)'; st.className='status err'; });
}
poll(); setInterval(poll, 2000);
</script></body></html>
"""
