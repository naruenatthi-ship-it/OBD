"""Honda CR-V e:HEV support, checked against responses OBDb recorded from
real cars (tests/fixtures/obdb, CC BY-SA 4.0)."""

import json
import os

import pytest

from deepal_s05 import obdb, snapshot, vehicles
from deepal_s05.cli import main
from deepal_s05.elm327 import (
    Elm327, assemble_isotp, parse_can_frames, response_header)

from fake_elm import FakeElm

HERE = os.path.join(os.path.dirname(__file__), "fixtures", "obdb")
SIGNALSET = os.path.join(HERE, "Honda-CR-V-Hybrid.json")
RESPONSES = json.load(open(os.path.join(HERE, "responses.json")))


def message(response):
    frames = [f for _, f in parse_can_frames(response, 8)]
    return assemble_isotp(frames)[0]


def command_for(command_id):
    hdr, _, rest = command_id.partition(".")
    did = rest.split(".")[-1][2:]
    for c in obdb.load_commands(SIGNALSET):
        if c["hdr"] == hdr and c["cmd"] == {"22": did}:
            return c
    raise KeyError(command_id)


CASES = [(cid, year, i) for cid, years in sorted(RESPONSES.items())
         for year, cases in sorted(years.items()) for i in range(len(cases))]


@pytest.mark.parametrize("cid,year,i", CASES)
def test_decoder_matches_obdb_expectations(cid, year, i):
    case = RESPONSES[cid][year][i]
    data = message(case["response"])[3:]
    sigs = {s["id"]: s for s in command_for(cid)["signals"]}
    for sid, expected in case["expected"].items():
        got = obdb.extract(data, sigs[sid]["fmt"])
        if isinstance(expected, (int, float)):
            assert got == pytest.approx(expected, abs=1e-6), sid
        else:
            assert got == expected, sid


def test_year_filter():
    assert obdb.applies({"filter": {"to": 2025, "from": 2027}}, 2023)
    assert not obdb.applies({"filter": {"to": 2025, "from": 2027}}, 2026)
    assert obdb.applies({"filter": {"years": [2026]}}, 2026)
    assert not obdb.applies({"filter": {"years": [2026]}}, 2024)
    assert obdb.applies({}, 2024) and obdb.applies({"filter": {}}, None)


def test_29bit_headers():
    assert response_header("18DA01F1") == "18DAF101"
    assert response_header("18DBEFF1") is None
    assert response_header("7A1") == "7A9"


def test_vehicle_profile():
    v = vehicles.load("crv-hybrid", SIGNALSET, 2024)
    assert v.protocol == "7" and v.can29
    assert v.signal("soc").header == "18DA01F1"
    cells = next(g for g in v.groups if g.key == "cells")
    assert len(cells.members) == 168            # A1-A84 and B1-B84
    assert v.default_headers("00", "02") == [
        "18DA00F1", "18DA01F1", "18DA02F1"]
    with pytest.raises(obdb.ObdbError):
        vehicles.load("crv-hybrid", "/no/such/file.json")


def recorded(year="2023"):
    out = {}
    for cid, years in RESPONSES.items():
        if year not in years:
            continue
        hdr, _, rest = cid.partition(".")
        out[("18" + hdr + "F1", rest.split(".")[-1])] = \
            years[year][0]["response"]
    return out


def test_snapshot_groups_cells_and_temps():
    v = vehicles.load("crv-hybrid", SIGNALSET, 2023)
    with FakeElm(recorded()) as fake, \
            Elm327(fake.url, protocol=v.protocol) as elm:
        elm.initialize()
        snap = snapshot.take(elm, v.live_signals(), v.groups, v.capacity)
        sent = fake.requests
    assert snap.values["soc"] == 53
    assert snap.values["pack_voltage"] == pytest.approx(264.6)
    assert len(snap.arrays["cells"]) == 72
    assert 3.70 < snap.values["cell_v_min"] <= snap.values["cell_v_max"] < 3.72
    assert snap.values["cell_delta_mv"] == pytest.approx(
        (snap.values["cell_v_max"] - snap.values["cell_v_min"]) * 1000)
    assert len(snap.arrays["temps"]) >= 1
    assert "crvh_cel_v_a1" not in snap.values   # members folded into arrays
    # each DID is asked once per round even though it carries many signals
    assert sent.count(("18DA01F1", "222028")) == 1


def test_cli_check_and_dtc_on_crv(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responses = recorded()
    responses[("18DA01F1", "1902FF")] = "18DAF101075902FF09800008"
    with FakeElm(responses) as fake:
        base = ["--car", "crv-hybrid", "--obdb", SIGNALSET, "--year",
                "2023", "--port", fake.url]
        assert main(base + ["check"]) == 0
        out = capsys.readouterr().out
        assert "Honda CR-V e:HEV" in out
        assert "แรงดันรายเซลล์" in out and "72 ค่า" in out
        assert main(base + ["dtc", "--start", "00", "--end", "02"]) == 0
        out = capsys.readouterr().out
        assert "ECU 18DA01F1 -> 18DAF101" in out and "P0980-00" in out
        assert ("18DA00F1", "1902FF") in fake.requests


def test_fetch_obdb(tmp_path, monkeypatch):
    def fake_fetch(repo, path):
        assert repo == "Honda-CR-V-Hybrid"
        with open(path, "w") as f:
            f.write("{}")
        return 2
    monkeypatch.setattr(obdb, "fetch", fake_fetch)
    target = tmp_path / "x" / "crv.json"
    assert main(["--car", "crv-hybrid", "--obdb", str(target),
                 "fetch-obdb"]) == 0
    assert target.exists()


def test_car_name_in_alerts_and_dashboard():
    import threading
    import urllib.request
    from deepal_s05 import alerts, dashboard

    sent = []
    n = alerts.Notifier(lambda t, m, p: sent.append(t), name="Honda CR-V e:HEV")
    rule = alerts.Rule("dcdc_temp", "อุณหภูมิ DC-DC", "°C", warn_above=80)
    n.notify(alerts.evaluate({"dcdc_temp": 90}, [rule]))
    assert sent == ["Honda CR-V e:HEV"]

    class Poller:
        def json(self):
            return "{}"

    server = dashboard.make_server(Poller(), "127.0.0.1", 0,
                                   car_name="Honda CR-V e:HEV")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen(
            "http://127.0.0.1:%d/" % server.server_address[1]).read()
    finally:
        server.shutdown()
        server.server_close()
    assert "Honda CR-V e:HEV".encode() in page
    assert b"Deepal" not in page


def test_status_line_hides_missing_soh():
    from deepal_s05.cli import status_line
    assert "SOH" not in status_line({"soc": 50}, [])
    assert "SOH -%" in status_line({"soc": 50, "soh": None}, [])
