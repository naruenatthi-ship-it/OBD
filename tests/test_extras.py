"""SOH from charging, charging cost, CAN listening, battery modules and
the password-protected server copy."""

import base64
import datetime
import json
import threading
import urllib.error
import urllib.request

import pytest

from deepal_s05 import canscan, cost, dashboard, predict, remote, soh

T0 = datetime.datetime(2026, 10, 12, 20, 0)  # a Monday evening


def charge_rows(minutes, amps, soc0=20, soc_per_min=0.5, v=400, start=T0):
    return [{"_t": start + datetime.timedelta(seconds=30 * k),
             "soc": str(soc0 + soc_per_min * k / 2),
             "pack_current": str(-amps), "pack_voltage": str(v)}
            for k in range(minutes * 2 + 1)]


def test_soh_measure():
    # 73 A for 120 min = 146 Ah while SOC goes 20 -> 80 %: 60 % of the
    # pack took 146 Ah, so the full pack holds 243 Ah
    m = soh.measure(charge_rows(120, 73, soc0=20, soc_per_min=0.5))
    assert m["soc_start"] == 20 and m["soc_end"] == 80
    assert m["ah"] == pytest.approx(146, rel=0.01)
    assert m["capacity_ah"] == pytest.approx(146 / 0.6, rel=0.01)
    assert m["kind"] == "DC"
    small = charge_rows(20, 16, soc_per_min=0.5)        # only +10 %
    assert soh.measure(small) is None
    assert soh.nominal_ah(56.1) == pytest.approx(146.1, abs=0.1)


def test_soh_finding_from_files(tmp_path):
    rows = charge_rows(240, 16, soc0=30, soc_per_min=0.29)
    with open(tmp_path / "charge_20261012_2000.csv", "w") as f:
        f.write("time,soc,pack_current,pack_voltage\n")
        for r in rows:
            f.write("%s,%s,%s,%s\n" % (r["_t"].isoformat(), r["soc"],
                                       r["pack_current"], r["pack_voltage"]))
    ms = soh.measurements(str(tmp_path))
    assert len(ms) == 1 and ms[0]["kind"] == "AC"
    f = soh.check_soh(str(tmp_path))
    assert "ครั้งล่าสุด" in f.summary and f.points


def test_cost_flat_tou_and_dc():
    flat = dict(cost.DEFAULTS, mode="flat", flat=4.0, ac_efficiency=0.9)
    c = cost.charge_cost(charge_rows(60, 10), flat)   # 4 kW for 1 h
    assert c["kind"] == "AC" and c["kwh"] == pytest.approx(4, rel=0.01)
    assert c["grid_kwh"] == pytest.approx(4 / 0.9, rel=0.01)
    assert c["baht"] == pytest.approx(4 / 0.9 * 4.0, rel=0.01)
    tou = dict(cost.DEFAULTS, mode="tou", on_peak=6, off_peak=2, ft=0,
               vat=0, ac_efficiency=1.0)
    # 21:00-23:00 Monday: first hour on-peak, second off-peak
    c = cost.charge_cost(charge_rows(120, 10, start=T0.replace(hour=21)),
                         tou)
    assert c["baht"] == pytest.approx(4 * 6 + 4 * 2, rel=0.02)
    saturday = T0.replace(day=17, hour=12)
    assert not cost.on_peak(saturday) and cost.on_peak(T0.replace(hour=10))
    dc = cost.charge_cost(charge_rows(30, 125), dict(cost.DEFAULTS,
                                                    dc_price=7.5))
    assert dc["kind"] == "DC" and dc["grid_kwh"] is None
    assert dc["baht"] == pytest.approx(dc["kwh"] * 7.5)
    months = cost.monthly([c, dc])
    assert months["2026-10"]["count"] == 2


def test_cost_tariff_file_and_page(tmp_path):
    (tmp_path / "tariff.json").write_text('{"flat": 5}')
    assert cost.load_tariff(str(tmp_path))["flat"] == 5
    assert "ยังไม่มีการชาร์จ" in cost.cost_html(str(tmp_path))


def test_canscan_parse_and_collect():
    assert canscan.parse_frame("3A1 00 11 22 33 44 55 66 77") == (
        0x3A1, bytes(range(0, 0x88, 0x11)))
    assert canscan.parse_frame("3A1 8 00 11") == (0x3A1, b"\x00\x11")
    assert canscan.parse_frame("3A10011") == (0x3A1, b"\x00\x11")
    assert canscan.parse_frame("SEARCHING...") is None
    col = canscan.Collector()
    for k in range(10):
        col.add(k * 0.1, 0x3A1, bytes([1, k, 3]))
    (row,) = col.rows({0x3A1: 2.0})
    assert row["moving_bytes"] == "1" and row["hz"] == 5.0
    assert canscan.blocks(0x000, 0x07F) == [(0, 31), (32, 63), (64, 95),
                                            (96, 127)]


def test_canscan_scan_listens_only():
    class Elm:
        def __init__(self):
            self.filters = []

        def setup_monitor(self, f, m):
            self.filters.append((f, m))
            return ["no CSM"]

        def monitor(self, on_line, duration=None):
            if self.filters[-1][0] == "3A0":
                on_line("3A1 00 11 22")
                on_line("3A1 00 12 22")
            return 0
    elm = Elm()
    rows, overflows, warnings = canscan.scan(elm, 1.0, 0x380, 0x3BF)
    assert elm.filters == [("380", "7E0"), ("3A0", "7E0")]
    assert [r["id"] for r in rows] == ["3A1"] and warnings == ["no CSM"]


def test_canscan_compare():
    def row(moving, sample):
        return {"moving_bytes": moving, "sample": sample}
    a = {"100": row("", "01"), "200": row("1", "00 10"), "300": row("", "AA")}
    b = {"100": row("", "02"), "200": row("1", "00 20"), "400": row("", "")}
    d = canscan.compare(a, b)
    assert d == {"only_a": ["300"], "only_b": ["400"], "changed": ["100"]}


def test_module_groups_from_behaviour():
    import random
    random.seed(1)
    devs = []
    for _ in range(200):
        mods = [random.gauss(0, 5) for _ in range(3)]  # 3 modules x 8
        d = {c: mods[(c - 1) // 8] + random.gauss(0, 0.5)
             for c in range(1, 25)}
        mean = sum(d.values()) / len(d)
        devs.append({c: v - mean for c, v in d.items()})
    assert predict.module_groups(devs) == [(1, 8), (9, 16), (17, 24)]
    assert predict.module_groups(devs[:5]) is None


def test_serve_needs_password_and_shows_latest_row(tmp_path):
    (tmp_path / "drive_20261016.csv").write_text(
        "time,soc,cells_1,cells_2\n2026-10-16T15:00:00,80,3.3,3.31\n",
        encoding="utf-8")
    data = json.loads(remote.LatestFile(str(tmp_path)).json())
    assert data["values"]["soc"] == 80
    assert data["arrays"]["cells"] == [3.3, 3.31]
    server = dashboard.make_server(remote.LatestFile(str(tmp_path)),
                                   "127.0.0.1", 0, str(tmp_path),
                                   password="s3cret")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/files")
        assert e.value.code == 401
        for pw, ok in (("wrong", False), ("s3cret", True)):
            req = urllib.request.Request(base + "/data", headers={
                "Authorization": "Basic " + base64.b64encode(
                    ("x:" + pw).encode()).decode()})
            if ok:
                assert json.loads(urllib.request.urlopen(req).read())[
                    "values"]["soc"] == 80
            else:
                with pytest.raises(urllib.error.HTTPError):
                    urllib.request.urlopen(req)
    finally:
        server.shutdown()
        server.server_close()


def test_serve_refuses_open_host_without_password(monkeypatch):
    from deepal_s05 import cli
    monkeypatch.delenv("OBD_WEB_PASSWORD", raising=False)
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 2


def test_canfind_odometer():
    rows = {"3F0": {"id": "3F0", "sample": "00 06 87 B1 00 00 00 00"},
            "120": {"id": "120", "sample": "12 34"}}
    hits = canscan.find_value(rows, 42795, 1)
    assert ("3F0", 1, 3, "big", 10, 42795.3) in hits
    assert all(h[0] == "3F0" for h in hits)
