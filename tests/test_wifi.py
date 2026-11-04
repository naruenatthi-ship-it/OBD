"""`obd wifi`: the Pi's known networks in the owner's order, against a
fake nmcli that keeps its state in a JSON file."""

import json
import sys

import pytest

from deepal_s05 import wifi

FAKE = r'''#!%s
import json, os, sys
path = os.environ["NM_STATE"]
st = json.load(open(path))
a = sys.argv[1:]
log = st.setdefault("log", [])
def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:")
if a[:2] == ["-g", "802-11-wireless.ssid"]:
    print(st["nets"][a[-1]]["ssid"])
elif "connection" in a and a[-1] == "show":
    for name, n in st["nets"].items():
        print(":".join([esc(name), "802-11-wireless", str(n["prio"]),
                        "yes" if st["active"] == name else "no"]))
    print("Wired connection 1:802-3-ethernet:-999:no")
elif "device" in a and "list" in a:
    for ssid, sig in st["air"].items():
        print("%%s:%%d" %% (esc(ssid), sig))
elif a[:2] == ["connection", "modify"]:
    st["nets"][a[2]]["prio"] = int(a[4])
elif a[:2] == ["connection", "up"]:
    st["active"] = a[2]
    log.append("up " + a[2])
elif a[:2] == ["connection", "delete"]:
    del st["nets"][a[2]]
elif a[:2] == ["connection", "add"]:
    i = a.index("con-name")
    st["nets"][a[i + 1]] = {"ssid": a[a.index("ssid") + 1],
                            "prio": int(a[-1])}
else:
    sys.exit("unknown " + " ".join(a))
json.dump(st, open(path, "w"))
''' % sys.executable

HOTSPOT_SSID = "Nat’s iPhone"


@pytest.fixture
def nm(tmp_path, monkeypatch):
    fake = tmp_path / "nmcli"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    state = tmp_path / "state.json"
    monkeypatch.setattr(wifi, "NMCLI", str(fake))
    monkeypatch.setenv("NM_STATE", str(state))

    def setup(nets, active, air):
        state.write_text(json.dumps({"nets": nets, "active": active,
                                     "air": air}))

    def read():
        return json.loads(state.read_text())
    return setup, read


DEFAULT = {"hotspot": {"ssid": HOTSPOT_SSID, "prio": 10},
           "Home:5G": {"ssid": "Home:5G", "prio": 5},
           "Office": {"ssid": "Office", "prio": 5}}


def test_list_shows_order_and_the_network_in_use(nm, capsys):
    setup, _ = nm
    setup(DEFAULT, "Home:5G", {"Home:5G": 70, "Neighbour": 40})
    nets = wifi.known()
    assert [n["name"] for n in nets] == ["hotspot", "Home:5G", "Office"]
    assert nets[0]["ssid"] == HOTSPOT_SSID and nets[1]["active"]
    assert wifi.main([]) == 0
    out = capsys.readouterr().out
    assert "2. Home:5G  · ★ ต่ออยู่ สัญญาณ 70%" in out
    assert "3. Office  · ไม่อยู่ในระยะ" in out


def test_first_and_order(nm):
    setup, read = nm
    setup(DEFAULT, "hotspot", {})
    assert wifi.main(["first", "3"]) == 0
    prio = {k: v["prio"] for k, v in read()["nets"].items()}
    assert prio == {"Office": 100, "hotspot": 90, "Home:5G": 80}
    # by name, the hotspot's WiFi name with a straight apostrophe works too
    assert wifi.main(["order", "Nat's iPhone", "Home:5G"]) == 0
    prio = {k: v["prio"] for k, v in read()["nets"].items()}
    assert prio == {"hotspot": 100, "Home:5G": 90, "Office": 80}
    assert wifi.main(["first", "Nowhere"]) == 1


def test_add_at_a_position_and_remove(nm):
    setup, read = nm
    setup(DEFAULT, "hotspot", {})
    assert wifi.main(["add", "Cafe", "short"]) == 1          # < 8 chars
    assert wifi.main(["add", "Cafe", "password1", "2"]) == 0
    order = [n["name"] for n in wifi.known()]
    assert order == ["hotspot", "Cafe", "Home:5G", "Office"]
    assert wifi.main(["add", "Cafe", "password2"]) == 1      # already known
    assert wifi.main(["remove", "1"]) == 1                   # never the hotspot
    assert wifi.main(["remove", "Cafe"]) == 0
    assert "Cafe" not in read()["nets"]


def test_auto_moves_up_only(nm):
    setup, read = nm
    # on the home WiFi, the hotspot (ranked first) comes into range: move
    setup(DEFAULT, "Home:5G", {"Home:5G": 70, HOTSPOT_SSID: 60})
    assert wifi.auto() == "hotspot" and read()["active"] == "hotspot"
    # on the hotspot, home in range: stay
    setup(DEFAULT, "hotspot", {"Home:5G": 70, HOTSPOT_SSID: 60})
    assert wifi.auto() is None and read()["log"] == []
    # home put first by the owner: the hotspot no longer pulls the Pi away
    nets = {"hotspot": {"ssid": HOTSPOT_SSID, "prio": 90},
            "Home:5G": {"ssid": "Home:5G", "prio": 100}}
    setup(nets, "Home:5G", {"Home:5G": 70, HOTSPOT_SSID: 60})
    assert wifi.auto() is None
    # a barely audible better network is not worth leaving for
    setup(DEFAULT, "Office", {"Office": 50, HOTSPOT_SSID: 10})
    assert wifi.auto() is None
    # nothing connected: join the best one in range
    setup(DEFAULT, "", {"Office": 50})
    assert wifi.auto() == "Office"


def test_dashboard_wifi_info_is_cached(monkeypatch):
    from deepal_s05 import dashboard
    calls = []
    monkeypatch.setattr(wifi, "current",
                        lambda: calls.append(1) or ("hotspot", "X", 55))
    t = [0.0]
    info = dashboard.WifiInfo(ttl=30, clock=lambda: t[0])
    assert info.get() == {"name": "X", "signal": 55}
    t[0] = 10
    info.get()
    assert len(calls) == 1
    t[0] = 31
    info.get()
    assert len(calls) == 2


def serve_local(tmp_path):
    import threading
    from deepal_s05 import autopilot, dashboard
    server = dashboard.make_server(autopilot.State([], []), "127.0.0.1", 0,
                                   str(tmp_path), local=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def post(base, body, header=True):
    import urllib.request
    req = urllib.request.Request(
        base + "/wifi", json.dumps(body).encode(),
        {"Content-Type": "application/json"} |
        ({"X-Requested-With": "deepal"} if header else {}))
    return json.loads(urllib.request.urlopen(req).read())


def test_dashboard_wifi_page(nm, tmp_path, monkeypatch):
    import urllib.error
    import urllib.request
    monkeypatch.setattr(wifi.time, "sleep", lambda s: None)
    setup, read = nm
    setup(DEFAULT, "hotspot", {HOTSPOT_SSID: 80, "Office": 40, "Cafe": 60})
    server, base = serve_local(tmp_path)
    try:
        assert "WiFi ของ Pi" in urllib.request.urlopen(base + "/wifi").read() \
            .decode()
        assert "/wifi" in urllib.request.urlopen(base + "/tools").read() \
            .decode()
        d = json.loads(urllib.request.urlopen(base + "/wifi.json").read())
        assert d["editable"] and d["networks"][0]["signal"] == 80
        assert [x["ssid"] for x in d["nearby"]] == ["Cafe"]
        # without the custom header (another web page posting): refused
        with pytest.raises(urllib.error.HTTPError):
            post(base, {"action": "remove", "name": "Office"}, header=False)
        r = post(base, {"action": "add", "ssid": "Cafe",
                        "password": "password1", "position": 1})
        assert "ลำดับที่ 1" in r["message"]
        assert r["status"]["networks"][0]["name"] == "Cafe"
        r = post(base, {"action": "order",
                        "names": ["hotspot", "Cafe", "Home:5G", "Office"]})
        assert "Hotspot" not in r["message"]
        assert "error" in post(base, {"action": "remove", "name": "hotspot"})
        assert "ลบ" in post(base, {"action": "remove", "name": "Office"})[
            "message"]
        # on another WiFi: look only
        st = read()
        setup(st["nets"], "Home:5G", st["air"])
        d = json.loads(urllib.request.urlopen(base + "/wifi.json").read())
        assert not d["editable"] and "Hotspot" in d["reason"]
        assert "error" in post(base, {"action": "remove", "name": "Cafe"})
        assert "Cafe" in read()["nets"]
    finally:
        server.shutdown()


def test_vps_copy_has_no_wifi_page(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from deepal_s05 import autopilot, dashboard
    server = dashboard.make_server(autopilot.State([], []), "127.0.0.1", 0,
                                   str(tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/wifi")
        with pytest.raises(urllib.error.HTTPError):
            post(base, {"action": "remove", "name": "x"})
        assert "/wifi" not in urllib.request.urlopen(base + "/tools").read() \
            .decode()
    finally:
        server.shutdown()
