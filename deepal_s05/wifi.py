"""The Pi's known WiFi networks, in the owner's order (`obd wifi`).

NetworkManager keeps whatever network it joined, even when one the owner
prefers comes into range; it only uses the autoconnect priorities when
nothing is connected. `auto` (run every minute by deepal-hotspot.timer)
fills that gap: when a network ranked higher than the current one is in
range, the Pi moves to it. With the default order (the phone hotspot
first) this is the old pi/hotspot-first.sh behaviour.

Run as: python3 -m deepal_s05.wifi [list|add|remove|first|order|use|auto]
"""

import os
import subprocess
import sys
import time

NMCLI = os.environ.get("OBD_NMCLI", "nmcli")
WIFI_TYPE = "802-11-wireless"
HOTSPOT = "hotspot"     # the connection pi/pair.sh made for the phone
TOP = 100               # priority of the first network, then -10 each
MIN_SIGNAL = 25         # weaker networks are not worth leaving one for


class WifiError(Exception):
    pass


def nm(*args, root=False):
    cmd = [NMCLI] + list(args)
    if root and os.geteuid() != 0:
        # -n: never ask for a password (the dashboard has no terminal);
        # pi/install.sh lets the Pi's user run nmcli as root
        cmd = ["sudo", "-n"] + cmd
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise WifiError("เรียก nmcli ไม่ได้: %s" % e)
    if p.returncode != 0:
        raise WifiError((p.stderr or p.stdout).strip() or "nmcli ผิดพลาด")
    return p.stdout


def split_terse(line):
    """Fields of one `nmcli -t -e yes` line: ':' separates, '\\' escapes."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def known():
    """Saved WiFi connections, the one the Pi picks first first:
    [{"name", "ssid", "priority", "active"}]."""
    nets = []
    text = nm("-t", "-e", "yes", "-f",
              "NAME,TYPE,AUTOCONNECT-PRIORITY,ACTIVE", "connection", "show")
    for line in text.splitlines():
        f = split_terse(line)
        if len(f) < 4 or f[1] != WIFI_TYPE:
            continue
        try:
            prio = int(f[2])
        except ValueError:
            prio = 0
        try:
            ssid = ":".join(split_terse(nm(
                "-g", "802-11-wireless.ssid", "connection", "show",
                f[0]).strip()))
        except WifiError:
            ssid = ""
        nets.append({"name": f[0], "ssid": ssid or f[0], "priority": prio,
                     "active": f[3] == "yes"})
    nets.sort(key=lambda n: (-n["priority"], n["name"] != HOTSPOT,
                             n["name"].lower()))
    return nets


def in_range(rescan=True):
    """{ssid: signal %} of the networks the Pi can hear."""
    found = {}
    text = nm("-t", "-e", "yes", "-f", "SSID,SIGNAL", "device", "wifi",
              "list", "--rescan", "yes" if rescan else "no")
    for line in text.splitlines():
        f = split_terse(line)
        if len(f) >= 2 and f[0]:
            try:
                found[f[0]] = max(found.get(f[0], 0), int(f[1]))
            except ValueError:
                pass
    return found


def find(nets, key):
    """A network by its number in `list`, connection name or WiFi name."""
    if key.isdigit() and 1 <= int(key) <= len(nets):
        return nets[int(key) - 1]
    for field in ("name", "ssid"):
        for n in nets:
            if n[field] == key:
                return n
    for n in nets:   # forgiving about case and the curly apostrophe
        if norm(n["name"]) == norm(key) or norm(n["ssid"]) == norm(key):
            return n
    raise WifiError('ไม่รู้จัก WiFi "%s" ดูชื่อหรือหมายเลขด้วย: obd wifi' % key)


def norm(s):
    return s.replace("’", "'").replace("‘", "'").strip().lower()


def set_order(nets):
    """Give the networks priorities TOP, TOP-10, ... in this order."""
    for i, n in enumerate(nets):
        prio = TOP - 10 * i
        if n["priority"] != prio:
            nm("connection", "modify", n["name"],
               "connection.autoconnect-priority", str(prio), root=True)
            n["priority"] = prio


def current():
    """(connection name, WiFi name, signal %) of the network in use, or
    None. Light enough for the dashboard: no rescan."""
    for n in known():
        if n["active"]:
            sig = in_range(rescan=False).get(n["ssid"])
            return n["name"], n["ssid"], sig
    return None


def ip_address():
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True,
                             text=True, timeout=5).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out[0] if out else None


# -- changes, shared by `obd wifi` and the dashboard's /wifi page ---------
def heard_networks():
    try:
        return in_range()
    except WifiError:   # a fresh scan may need root: use the last one
        try:
            return in_range(rescan=False)
        except WifiError:
            return {}


def status():
    """Everything the /wifi page shows."""
    nets = known()
    heard = heard_networks()
    names = {n["ssid"] for n in nets}
    for n in nets:
        n["signal"] = heard.get(n["ssid"])
    nearby = sorted(({"ssid": s, "signal": g} for s, g in heard.items()
                     if s not in names), key=lambda x: -x["signal"])
    return {"networks": nets, "nearby": nearby[:15], "ip": ip_address()}


def on_hotspot():
    """True when the Pi is on the phone's hotspot: only the owner's own
    devices share that network, so the dashboard lets them change the
    WiFi list there and nowhere else (no password on the dashboard)."""
    return any(n["active"] and n["name"] == HOTSPOT for n in known())


def add_network(ssid, password, position=None):
    """Save a WPA network at `position` (1 = first; None = last).
    Returns its position."""
    ssid = ssid.strip()
    nets = known()
    if not ssid:
        raise WifiError("ใส่ชื่อ WiFi ด้วย")
    if any(n["name"] == ssid or n["ssid"] == ssid for n in nets):
        raise WifiError('จำ "%s" ไว้แล้ว ลบก่อนถ้าจะเปลี่ยนรหัส' % ssid)
    if len(password) < 8:
        raise WifiError("รหัส WiFi ต้องยาวอย่างน้อย 8 ตัว")
    nm("connection", "add", "type", "wifi", "ifname", "wlan0",
       "con-name", ssid, "ssid", ssid, "wifi-sec.key-mgmt", "wpa-psk",
       "wifi-sec.psk", password, "connection.autoconnect-priority",
       str(TOP - 10 * len(nets)), root=True)
    new = {"name": ssid, "ssid": ssid, "priority": None, "active": False}
    pos = len(nets) if position is None else max(0, min(position - 1,
                                                        len(nets)))
    nets.insert(pos, new)
    set_order(nets)
    return pos + 1


def remove_network(key):
    n = find(known(), key)
    if n["name"] == HOTSPOT:
        raise WifiError("ไม่ลบ Hotspot มือถือ: เป็นทางกู้เวลาหา Pi ไม่เจอ"
                        " (ถ้าเปลี่ยนชื่อ Hotspot ให้รัน obd pair ใหม่)")
    nm("connection", "delete", n["name"], root=True)
    return n


def reorder(keys):
    """Put these networks first, in this order; the rest keep theirs."""
    nets = known()
    picked = []
    for k in keys:
        n = find(nets, k)
        if n not in picked:
            picked.append(n)
    new = picked + [n for n in nets if n not in picked]
    set_order(new)
    return new


def use_network(key):
    n = find(known(), key)
    if not n["active"]:
        nm("connection", "up", n["name"], root=True)
    return n


HOTSPOT_NOT_FIRST = ("Hotspot มือถือไม่ได้อยู่อันดับ 1 แล้ว: ถ้าวงที่อยู่เหนือกว่าอยู่ในระยะ"
                     " Pi จะไม่ย้ายมา Hotspot เอง (มือถือต้องอยู่ WiFi วงเดียวกับ Pi"
                     " ถึงจะเปิด dashboard ได้)")


# -- commands ---------------------------------------------------------------
def cmd_list():
    st = status()
    nets = st["networks"]
    if not nets:
        print("ยังไม่มี WiFi ที่จำไว้")
        return 0
    print("== WiFi ที่จำไว้ (เรียงตามที่ Pi เลือกก่อน) ==")
    for i, n in enumerate(nets, 1):
        label = n["name"]
        if n["ssid"] != n["name"]:
            label += " (%s)" % n["ssid"]
        sig = n["signal"]
        if n["active"]:
            state = "★ ต่ออยู่" + (" สัญญาณ %d%%" % sig if sig else "")
        elif sig is not None:
            state = "อยู่ในระยะ สัญญาณ %d%%" % sig
        else:
            state = "ไม่อยู่ในระยะ"
        print("%2d. %s  · %s" % (i, label, state))
    if not any(n["active"] for n in nets):
        print("ตอนนี้ไม่ได้ต่อ WiFi")
    if st["ip"]:
        print("\nIP: %s   dashboard: http://%s:8000" % (st["ip"], st["ip"]))
    print("\nเรียงใหม่: obd wifi first หมายเลข   ย้ายไปวงนี้เดี๋ยวนี้: obd wifi use หมายเลข")
    print("(จัดการบนหน้า dashboard ได้ด้วย: เมนู → WiFi ของ Pi)")
    return 0


def cmd_add(ssid, password, position=None):
    pos = add_network(ssid, password, position)
    print('เพิ่ม "%s" แล้ว อยู่ลำดับที่ %d' % (ssid, pos))
    print("ระวัง: WiFi ที่ทำงาน/ร้านค้ามักกันไม่ให้มือถือมองเห็น Pi")
    print("ถ้าหา Pi ไม่เจอ เปิด Hotspot มือถือ Pi จะย้ายกลับมาเองใน 1-2 นาที")
    return cmd_list()


def cmd_remove(key):
    n = remove_network(key)
    print('ลบ "%s" แล้ว' % n["name"])
    if n["active"]:
        print("Pi กำลังหา WiFi วงอื่นที่จำไว้ (ถ้าหลุดจาก Termius ให้เปิด Hotspot)")
    return 0


def cmd_order(keys):
    new = reorder(keys)
    if new[0]["name"] != HOTSPOT:
        print("หมายเหตุ: " + HOTSPOT_NOT_FIRST)
    return cmd_list()


def cmd_use(key):
    n = find(known(), key)
    if n["active"]:
        print('ต่อ "%s" อยู่แล้ว' % n["name"])
        return 0
    print('กำลังย้ายไป "%s" · ถ้าใช้ Termius ผ่านวงเดิมอยู่ จะหลุด ให้มือถือไปอยู่วงใหม่แล้วต่อใหม่'
          % n["name"])
    print("(ถ้าวงอื่นอยู่ลำดับสูงกว่าและอยู่ในระยะ Pi จะย้ายกลับไปเองในไม่กี่นาที:"
          " ใช้ obd wifi first แทนถ้าจะให้อยู่ถาวร)")
    sys.stdout.flush()
    time.sleep(1)   # let the message reach Termius before the link drops
    use_network(key)
    return 0


def auto():
    """Move to the highest-ranked known network in range, if it is ranked
    above the current one. Returns the connection it moved to, or None."""
    nets = known()
    if not nets:
        return None
    heard = in_range()
    active = next((n for n in nets if n["active"]), None)
    for n in nets:          # best first
        if n is active:
            return None     # nothing better in range
        if heard.get(n["ssid"], 0) >= MIN_SIGNAL:
            if active is not None and active["priority"] >= n["priority"]:
                return None
            print("%s อยู่ในระยะ ย้ายจาก %s" % (
                n["name"], active["name"] if active else "(ไม่ได้ต่อ)"))
            nm("connection", "up", n["name"], root=True)
            return n["name"]
    return None


USAGE = """ใช้แบบนี้:
  obd wifi                        ดูว่าต่อวงไหนอยู่ + ลำดับ
  obd wifi add "ชื่อ" "รหัส" [ลำดับ]  เพิ่ม WiFi (ไม่ใส่ลำดับ = ต่อท้าย)
  obd wifi first หมายเลข           ย้ายวงนี้ขึ้นอันดับ 1
  obd wifi order 2 1 3            เรียงใหม่ทั้งหมด (หมายเลขหรือชื่อ)
  obd wifi use หมายเลข             ย้ายไปวงนี้เดี๋ยวนี้
  obd wifi remove หมายเลข          ลบ"""


def main(argv):
    action = argv[0] if argv else "list"
    args = argv[1:]
    try:
        if action == "list":
            return cmd_list()
        if action == "add" and len(args) in (2, 3):
            pos = int(args[2]) if len(args) == 3 and args[2].isdigit() \
                else None
            return cmd_add(args[0], args[1], pos)
        if action in ("remove", "delete") and len(args) == 1:
            return cmd_remove(args[0])
        if action == "first" and len(args) == 1:
            return cmd_order(args)
        if action == "order" and args:
            return cmd_order(args)
        if action == "use" and len(args) == 1:
            return cmd_use(args[0])
        if action == "auto":
            auto()
            return 0
    except WifiError as e:
        print(str(e))
        return 1
    print(USAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
