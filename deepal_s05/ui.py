"""The shared look of every dashboard page: colours, the top bar with a
way back, the bottom tab bar, and what a phone needs to keep the site as
an app on its home screen (manifest + icon).

Saved from a phone's home screen the site runs without Safari's own back
button, so every page carries the top bar (back) and the tab bar (home,
files, trends, menu), and downloads open in a new view (target=_blank)
instead of replacing the app.
"""

import html
import json
import re
import struct
import zlib

FONT = ('"ThaiUI",-apple-system,BlinkMacSystemFont,"Segoe UI",'
        '"Noto Sans Thai","IBM Plex Sans Thai","Leelawadee UI",Tahoma,'
        'sans-serif')

# colour tokens: hex only, the home page mixes them in JavaScript
TOKENS = """
@font-face { font-family:"ThaiUI"; font-weight:400; unicode-range:U+0E00-0E7F;
  src:local("SukhumvitSet-Text"),local("Sukhumvit Set"),local("Noto Sans Thai"),
  local("Leelawadee UI"); }
@font-face { font-family:"ThaiUI"; font-weight:500; unicode-range:U+0E00-0E7F;
  src:local("SukhumvitSet-Medium"),local("Noto Sans Thai Medium"),
  local("Leelawadee UI"); }
@font-face { font-family:"ThaiUI"; font-weight:600 800; unicode-range:U+0E00-0E7F;
  src:local("SukhumvitSet-SemiBold"),local("SukhumvitSet-Bold"),
  local("Noto Sans Thai SemiBold"),local("Leelawadee UI Bold"); }
:root { --bg:#f3f5f8; --surface:#ffffff; --surface2:#eef1f5; --ink:#101828;
  --muted:#667085; --line:#e3e8ef; --accent:#0a9488; --accent-ink:#ffffff;
  --accent-soft:#ddf3f0; --ok:#15803d; --warn:#b45309; --bad:#d92d20;
  --none:#8a94a6; --okbg:#e6f6ec; --warnbg:#fdf2e1; --badbg:#fdecea;
  --nonebg:#eef1f5; --s1:#0a9488; --s2:#e8590c; --s3:#2f6fdb; --s4:#8b5cf6;
  --s5:#ca8a04; --bar:rgba(243,245,248,.86);
  --shadow:0 1px 2px rgba(16,24,40,.05),0 6px 20px rgba(16,24,40,.06);
  --radius:18px; color-scheme:light; }
@media (prefers-color-scheme: dark) { :root { --bg:#0a0e13; --surface:#121921;
  --surface2:#1a222d; --ink:#e8edf3; --muted:#8c98a9; --line:#232d3a;
  --accent:#2dd4bf; --accent-ink:#04201c; --accent-soft:#0f2e2b;
  --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; --none:#6b7686;
  --okbg:#10291c; --warnbg:#2d2311; --badbg:#311819; --nonebg:#1a222d;
  --s1:#2dd4bf; --s2:#fb923c; --s3:#60a5fa; --s4:#c084fc; --s5:#facc15;
  --bar:rgba(10,14,19,.84); --shadow:0 1px 2px rgba(0,0,0,.4);
  color-scheme:dark; } }
"""

# the bars: also injected into saved report pages
NAV_CSS = """
.appbar { position:sticky; top:0; z-index:20; display:flex; align-items:center;
  gap:12px; margin:0; padding:calc(10px + env(safe-area-inset-top))
  max(16px,env(safe-area-inset-right)) 10px max(16px,env(safe-area-inset-left));
  background:var(--bar); border-bottom:1px solid var(--line);
  -webkit-backdrop-filter:saturate(180%) blur(18px);
  backdrop-filter:saturate(180%) blur(18px); font-family:""" + FONT + """; }
.appbar .ttl { flex:1; min-width:0; line-height:1.3; }
.appbar .ttl b { display:block; font-size:1.05rem; font-weight:700;
  color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.appbar .ttl small { display:block; font-size:.78rem; color:var(--muted);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.iconbtn { flex:none; display:inline-grid; place-items:center; width:40px;
  height:40px; border-radius:13px; background:var(--surface); color:var(--ink);
  border:1px solid var(--line); text-decoration:none; }
.iconbtn svg, .brand svg { width:22px; height:22px; }
.brand { flex:none; display:grid; place-items:center; width:40px; height:40px;
  border-radius:13px; color:#fff;
  background:linear-gradient(135deg,#14b8a6,#0e7490); }
.tabbar { position:fixed; z-index:30; left:50%; transform:translateX(-50%);
  bottom:calc(10px + env(safe-area-inset-bottom)); width:min(520px,calc(100% - 24px));
  display:grid; grid-template-columns:repeat(4,1fr); gap:4px; padding:6px;
  border-radius:24px; background:var(--bar); border:1px solid var(--line);
  box-shadow:0 10px 30px rgba(0,0,0,.14);
  -webkit-backdrop-filter:saturate(180%) blur(18px);
  backdrop-filter:saturate(180%) blur(18px); font-family:""" + FONT + """; }
.tabbar a { display:grid; justify-items:center; gap:1px; padding:7px 0 6px;
  border-radius:18px; color:var(--muted); font-size:.7rem; font-weight:600;
  text-decoration:none; line-height:1.2; }
.tabbar a svg { width:23px; height:23px; }
.tabbar a.on { color:var(--accent); background:var(--accent-soft); }
.tabbar a:active, .iconbtn:active, .btn:active, .item:active, .tile:active {
  transform:scale(.97); }
"""

CSS = TOKENS + NAV_CSS + """
* { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
[hidden] { display:none !important; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; min-height:100vh; background:var(--bg); color:var(--ink);
  font:15px/1.5 """ + FONT + """;
  padding:0 0 calc(100px + env(safe-area-inset-bottom)); }
a { color:var(--accent); }
main.page { max-width:1120px; margin:0 auto; display:grid; gap:14px;
  padding:16px max(16px,env(safe-area-inset-right)) 0 max(16px,env(safe-area-inset-left)); }
h1 { margin:0; font-size:1.4rem; } h2 { margin:0 0 8px; font-size:1rem; font-weight:700; }
.muted { color:var(--muted); font-size:.86rem; }
code, .mono { font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
.card { background:var(--surface); border:1px solid var(--line);
  border-radius:var(--radius); padding:16px; min-width:0; box-shadow:var(--shadow); }
.btnrow { display:flex; flex-wrap:wrap; gap:8px; }
.btn { display:inline-flex; align-items:center; justify-content:center; gap:8px;
  min-height:46px; padding:10px 18px; border-radius:14px; border:0;
  font:inherit; font-size:.95rem; font-weight:700; cursor:pointer;
  background:var(--accent); color:var(--accent-ink); text-decoration:none; }
.btn svg { width:19px; height:19px; flex:none; }
.btn.tonal { background:var(--accent-soft); color:var(--accent); }
.btn.plain { background:var(--surface); color:var(--ink); border:1px solid var(--line); }
.label { margin:10px 4px -6px; font-size:.78rem; font-weight:700;
  color:var(--muted); letter-spacing:.02em; }
.list { background:var(--surface); border:1px solid var(--line);
  border-radius:var(--radius); overflow:hidden; box-shadow:var(--shadow); }
.item { display:flex; align-items:center; gap:12px; padding:12px 14px;
  color:var(--ink); text-decoration:none; border-top:1px solid var(--line); }
.item:first-child { border-top:0; }
.item .tx { flex:1; min-width:0; }
.item .tx b { display:block; font-weight:600; font-size:.93rem;
  overflow-wrap:anywhere; }
.item .tx small { display:block; color:var(--muted); font-size:.78rem; }
.item .go { flex:none; color:var(--muted); display:grid; }
.item .go svg { width:18px; height:18px; }
.badge { flex:none; display:grid; place-items:center; width:42px; height:42px;
  border-radius:13px; font:700 .66rem ui-monospace,Menlo,Consolas,monospace;
  letter-spacing:.03em; color:var(--c); background:var(--cb); }
.badge svg { width:22px; height:22px; }
.k-csv { --c:#15803d; --cb:#e6f6ec; } .k-json { --c:#7c3aed; --cb:#f1ebfe; }
.k-log { --c:#475467; --cb:#eef1f5; } .k-html { --c:#2f6fdb; --cb:#e8f0fd; }
.k-txt { --c:#b45309; --cb:#fdf2e1; }
@media (prefers-color-scheme: dark) {
  .k-csv { --c:#4ade80; --cb:#10291c; } .k-json { --c:#c084fc; --cb:#251a36; }
  .k-log { --c:#a3adbb; --cb:#1a222d; } .k-html { --c:#60a5fa; --cb:#13233b; }
  .k-txt { --c:#fbbf24; --cb:#2d2311; } }
.tiles { display:grid; gap:12px;
  grid-template-columns:repeat(auto-fill,minmax(min(100%,155px),1fr)); }
.tile { display:grid; align-content:start; gap:10px; padding:14px;
  background:var(--surface); border:1px solid var(--line);
  border-radius:var(--radius); color:var(--ink); text-decoration:none;
  box-shadow:var(--shadow); }
.tile .badge { width:44px; height:44px; }
.tile b { display:block; font-weight:700; font-size:.98rem; }
.tile small { display:block; color:var(--muted); font-size:.8rem; line-height:1.4; }
.chip { display:inline-flex; align-items:center; gap:6px; flex:none;
  padding:5px 11px; border-radius:999px; font-size:.78rem; font-weight:700;
  background:var(--nonebg); color:var(--muted); white-space:nowrap; }
.chip::before { content:""; width:7px; height:7px; border-radius:50%;
  background:currentColor; }
.chip.live { background:var(--okbg); color:var(--ok); }
.chip.live::before { animation:pulse 1.6s ease-in-out infinite; }
.chip.err { background:var(--badbg); color:var(--bad); }
@keyframes pulse { 50% { opacity:.25; } }
.table { overflow-x:auto; -webkit-overflow-scrolling:touch; }
table { border-collapse:collapse; width:100%; font-size:.86rem;
  font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-weight:600; font-size:.8rem; }
tr:last-child td { border-bottom:0; }
pre.text { margin:0; white-space:pre-wrap; overflow-wrap:anywhere;
  font:12px/1.5 ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
.empty { text-align:center; padding:28px 16px; color:var(--muted); }
.empty .badge { margin:0 auto 10px; width:52px; height:52px; }
"""

# 24x24 line icons, drawn with currentColor
ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>'
            '<path d="M10 21v-6h4v6"/>',
    "folder": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 '
              '21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>',
    "trend": '<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="2"/>'
            '<rect x="14" y="3" width="7" height="7" rx="2"/>'
            '<rect x="3" y="14" width="7" height="7" rx="2"/>'
            '<rect x="14" y="14" width="7" height="7" rx="2"/>',
    "back": '<path d="m15 18-6-6 6-6"/>',
    "next": '<path d="m9 18 6-6-6-6"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/>'
                '<path d="M5 21h14"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/>'
            '<path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    "list": '<path d="M9 4h6v3H9z"/><path d="M15 5h3a1 1 0 0 1 1 1v14a1 1 0 0 '
            '1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h3"/><path d="M9 12h6M9 16h4"/>',
    "ecu": '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/>'
           '<circle cx="19" cy="19" r="2.5"/><path d="M12 7.5v4m0 0L6.5 17M12 '
           '11.5l5.5 5.5"/>',
    "gauge": '<path d="M3.5 17a8.5 8.5 0 1 1 17 0"/><path d="m12 17 4.5-6"/>'
             '<circle cx="12" cy="17" r="1.5"/>',
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "pulse": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "coin": '<circle cx="12" cy="12" r="9"/><path d="M14.5 9.5c-.4-.9-1.4-1.5-2.5'
            '-1.5-1.4 0-2.5.8-2.5 2s1 1.6 2.5 2 2.5.9 2.5 2-1.1 2-2.5 2c-1.1 '
            '0-2.1-.6-2.5-1.5M12 6.5V8m0 8v1.5"/>',
    "file": '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/>'
            '<path d="M14 3v5h5"/>',
    "battery": '<rect x="2" y="7" width="17" height="10" rx="2.5"/>'
               '<path d="M22 11v2M6 10.5v3M9.5 10.5v3"/>',
}


def icon(name):
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            'aria-hidden="true">%s</svg>' % ICONS[name])


TABS = (("home", "/", "home", "หน้าหลัก"),
        ("files", "/files", "folder", "ไฟล์"),
        ("trends", "/trends", "trend", "แนวโน้ม"),
        ("tools", "/tools", "grid", "เมนู"))


def head(title):
    return ('<meta name="viewport" content="width=device-width, '
            'initial-scale=1, viewport-fit=cover">'
            '<meta name="theme-color" content="#f3f5f8" '
            'media="(prefers-color-scheme: light)">'
            '<meta name="theme-color" content="#0a0e13" '
            'media="(prefers-color-scheme: dark)">'
            '<meta name="apple-mobile-web-app-capable" content="yes">'
            '<meta name="mobile-web-app-capable" content="yes">'
            '<meta name="apple-mobile-web-app-status-bar-style" '
            'content="default">'
            '<link rel="manifest" href="/manifest.webmanifest">'
            '<link rel="apple-touch-icon" href="/icon.png">'
            '<link rel="icon" type="image/png" href="/icon.png">'
            '<title>%s</title>' % html.escape(title))


def appbar(title, sub="", back=None, right=""):
    lead = ('<a class="iconbtn" href="%s" aria-label="กลับ">%s</a>'
            % (html.escape(back), icon("back")) if back else
            '<a class="brand" href="/" aria-label="หน้าหลัก">%s</a>'
            % icon("bolt"))
    return ('<header class="appbar" data-appbar>%s<div class="ttl"><b>%s</b>'
            '%s</div>%s</header>' % (
                lead, html.escape(title),
                '<small>%s</small>' % sub if sub else "", right))


def tabbar(active=None, hidden=False, ident=""):
    return '<nav class="tabbar"%s%s aria-label="เมนูหลัก">%s</nav>' % (
        ' id="%s"' % ident if ident else "", " hidden" if hidden else "",
        "".join('<a href="%s"%s>%s<span>%s</span></a>' % (
            href, ' class="on" aria-current="page"' if key == active else "",
            icon(ic), label) for key, href, ic, label in TABS))


def document(title, body, css=""):
    return ('<!doctype html><html lang="th"><head><meta charset="utf-8">%s'
            '<style>%s%s</style></head><body>%s</body></html>'
            % (head(title), CSS, css, body))


def shell(title, body, back="/tools", sub="", active=None, css="", right=""):
    """A whole page: top bar (back, or the logo on a tab's own page),
    the body inside <main>, and the tab bar."""
    return document(title, appbar(title, sub, back, right) +
                    '<main class="page">%s</main>' % body +
                    tabbar(active), css)


def empty(text, ic="file"):
    return ('<section class="card empty"><span class="badge k-log">%s</span>'
            '%s</section>' % (icon(ic), text))


BODY_TAG = re.compile(r"<body[^>]*>", re.I)


def inject(doc, title, back=None, active=None):
    """Add the bars to a saved page (charge report, trends) that was
    written without them, so it never strands a phone app."""
    if "data-appbar" in doc:
        return doc
    bars = ('<style>%s%s body{padding-top:0!important;padding-bottom:calc(100px'
            ' + env(safe-area-inset-bottom))!important}</style>%s%s' % (
                TOKENS, NAV_CSS, appbar(title, back=back), tabbar(active)))
    m = BODY_TAG.search(doc)
    if not m:
        return bars + doc
    return doc[:m.end()] + bars + doc[m.end():]


def manifest(name):
    return json.dumps({
        "name": name, "short_name": name, "start_url": "/",
        "scope": "/", "display": "standalone", "theme_color": "#0a9488",
        "background_color": "#0a0e13", "lang": "th",
        "icons": [{"src": "/icon.png", "sizes": "192x192",
                   "type": "image/png", "purpose": "any"}]},
        ensure_ascii=False)


def _png(size, rows):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def icon_png(size=192):
    """The home screen icon: a white bolt on a teal gradient, drawn here
    (no image files to ship). The phone rounds the corners itself."""
    scale = 0.6 * size / 20
    ox, oy = size / 2 - 11.5 * scale, size / 2 - 12 * scale
    pts = [(ox + x * scale, oy + y * scale) for x, y in
           ((13, 2), (4, 14), (11, 14), (10, 22), (19, 10), (12, 10))]
    edges = list(zip(pts, pts[1:] + pts[:1]))
    sub = 4
    rows = []
    top, bottom = (20, 184, 166), (14, 100, 140)
    for py in range(size):
        cover = [0.0] * size
        for k in range(sub):
            y = py + (k + 0.5) / sub
            xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                        for (x0, y0), (x1, y1) in edges
                        if (y0 <= y) != (y1 <= y))
            for a, b in zip(xs[::2], xs[1::2]):
                for px in range(max(0, int(a)), min(size, int(b) + 1)):
                    cover[px] += (min(b, px + 1) - max(a, px)) / sub
        row = bytearray()
        for px in range(size):
            t = (px + py) / (2 * size - 2)
            a = min(1.0, max(0.0, cover[px]))
            row += bytes(round(c0 + (c1 - c0) * t + (255 - c0 - (c1 - c0) * t)
                               * a) for c0, c1 in zip(top, bottom))
        rows.append(bytes(row))
    return _png(size, rows)
