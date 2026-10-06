import json

import pytest

from deepal_s05 import alerts, analysis, config, dtc, report


def test_evaluate_levels_and_requires():
    rules = alerts.DEFAULT_RULES
    found = alerts.evaluate({"soc": 80, "aux_12v": 11.5,
                             "batt_temp_max": 47}, rules)
    assert [(a.key, a.level) for a in found] == [
        ("aux_12v", alerts.CRIT), ("batt_temp_max", alerts.WARN)]
    # 12 V is only judged when the BMS answered (car awake)
    assert alerts.evaluate({"aux_12v": 11.5}, rules) == []
    assert alerts.evaluate({"soc": 80, "aux_12v": 14.0,
                            "batt_temp_max": 30}, rules) == []


def test_merge_rules():
    rules = alerts.merge_rules(alerts.DEFAULT_RULES, [
        {"key": "batt_temp_max", "warn_above": 40.0},
        {"key": "obc_temp", "label": "OBC", "unit": "C",
         "warn_above": 70.0, "critical_above": 85.0}])
    by_key = {r.key: r for r in rules}
    assert by_key["batt_temp_max"].warn_above == 40
    assert by_key["batt_temp_max"].critical_above == 55
    found = alerts.evaluate({"obc_temp": 90, "batt_temp_max": 41}, rules)
    assert {(a.key, a.level) for a in found} == {
        ("obc_temp", alerts.CRIT), ("batt_temp_max", alerts.WARN)}


def test_load_alerts(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"alerts": [
        {"key": "obc_temp", "warn_above": "70"}]}), encoding="utf-8")
    assert config.load_alerts(str(path)) == [
        {"key": "obc_temp", "warn_above": 70.0}]
    path.write_text(json.dumps({"alerts": [{"warn_above": 1}]}),
                    encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_alerts(str(path))


def alert(key, level):
    return alerts.Alert(level, key, key, 1, "", key)


def test_notifier_debounce():
    sent = []
    now = [0]
    n = alerts.Notifier(lambda t, m, p: sent.append((m, p)), cooldown=600,
                        clock=lambda: now[0])
    assert len(n.notify([alert("a", alerts.WARN)])) == 1
    assert n.notify([alert("a", alerts.WARN)]) == []          # repeat
    assert len(n.notify([alert("a", alerts.CRIT)])) == 1      # worse
    assert sent[-1][1] == "high"
    now[0] = 700
    assert len(n.notify([alert("a", alerts.CRIT)])) == 1      # cooldown
    assert n.notify([alert("b", alerts.INFO)]) == []          # info only
    n.notify([])                                              # cleared
    assert len(n.notify([alert("a", alerts.WARN)])) == 1      # back again


def test_notifier_survives_network_errors():
    def fail(*a):
        raise OSError("offline")
    n = alerts.Notifier(fail)
    assert n.notify([alert("a", alerts.WARN)]) == []
    assert n.error == "offline"


def scan(answered, codes, scanned=None):
    return dtc.make_scan(answered, [
        {"header": h, "code": c, "status": s} for h, c, s in codes],
        "t", scanned or answered)


def test_dtc_diff():
    prev = scan(["761", "7A1", "7C4"], [("7A1", "P0A80-00", 0x08),
                                         ("761", "P0562-16", 0x04),
                                         ("7C4", "B1000-00", 0x08)])
    cur = scan(["761", "7A1", "7E0"], [("7A1", "P0A80-00", 0x08),
                                        ("761", "P0562-16", 0x09),
                                        ("7A1", "U0111-00", 0x20)],
               ["761", "7A1", "7C4", "7E0"])
    d = dtc.diff(prev, cur)
    assert d["new"] == [("7A1", "U0111-00")]
    assert d["changed"] == [(("761", "P0562-16"), 0x04, 0x09)]
    assert d["cleared"] == []  # 7C4 did not answer, so nothing is cleared
    assert d["lost_ecus"] == ["7C4"] and d["new_ecus"] == ["7E0"]
    assert dtc.diff(None, cur) is None


def test_dtc_diff_ignores_ecus_not_scanned():
    prev = scan(["761", "7A1"], [])
    cur = scan(["7A1"], [], ["7A1"])
    assert dtc.diff(prev, cur)["lost_ecus"] == []


def test_dtc_history_file(tmp_path):
    path = str(tmp_path / "h.json")
    assert dtc.load_history(path) == []
    for i in range(3):
        dtc.save_scan(path, {"time": str(i)}, keep=2)
    assert [s["time"] for s in dtc.load_history(path)] == ["1", "2"]


def test_charge_power_status():
    ok = [(50, 6.2), (51, 6.1), (52, 6.2)]
    assert analysis.charge_power_status(ok, 6.6)[0] == "ok"
    low = [(50, 4.5), (51, 4.4), (52, 4.4)]
    assert analysis.charge_power_status(low, 6.6)[0] == "low"
    drop = [(50, -6.5), (51, -6.4), (52, -4.6), (53, -4.5), (54, -4.4)]
    assert analysis.charge_power_status(drop)[0] == "drop"
    assert analysis.charge_power_status([(97, 1.0)])[0] == "taper"
    assert analysis.charge_power_status([])[0] == "no_data"
    assert analysis.power_drop(drop) == pytest.approx(1 - 4.4 / 6.5)


def test_trends_report(tmp_path):
    checkup = tmp_path / "c.csv"
    checkup.write_text(
        "time,level,zone,soc,soh,delta_mv,aux_12v,dtc_active\n"
        "2026-06-01T09:00:00,ปกติ,top,98,99.2,18,14.05,0\n"
        "2026-06-08T09:00:00,ปกติ,top,97,99.1,25,14.0,1\n",
        encoding="utf-8")
    out = tmp_path / "t.html"
    report.write_trends(str(checkup), str(tmp_path / "none.csv"), str(out))
    page = out.read_text(encoding="utf-8")
    assert "<h2>SOH</h2>" in page and "SOC ≥ 95%" in page
    assert "สรุปแต่ละรอบ" not in page and "%%" not in page
    with pytest.raises(SystemExit):
        report.write_trends(str(tmp_path / "x"), str(tmp_path / "y"),
                            str(out))
