import csv
import datetime
import json
import math

from deepal_s05 import predict

T0 = datetime.datetime(2026, 7, 1)


def write_csv(path, rows):
    fields = []
    for r in rows:
        fields += [k for k in r if k not in fields]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)


def test_forecast_days_to_limit():
    pts = [(T0 + datetime.timedelta(days=d), 12.6 - 0.005 * d)
           for d in range(0, 60, 5)]
    days, slope, r2 = predict.forecast(pts, 12.2, rising=False)
    assert math.isclose(slope, -0.005) and r2 > 0.99
    assert math.isclose(days, (12.6 - 0.005 * 55 - 12.2) / 0.005)
    # moving away from the limit, or too little history
    assert predict.forecast(pts, 13.0, rising=True)[0] is None
    assert predict.forecast(pts[:3], 12.2, rising=False)[0] is None


def test_trend_finding_levels():
    falling = [(T0 + datetime.timedelta(days=d), 12.5 - 0.001 * d)
               for d in range(0, 70, 7)]  # ~8 months to 12.2 V
    f = predict.trend_finding("k", "p", falling, 12.2, False, "V")
    assert f.level == predict.WATCH and "เดือน" in f.summary
    fast = [(t, v - (t - T0).days * 0.003) for t, v in falling]  # 12 days
    assert predict.trend_finding("k", "p", fast, 12.2, False,
                                 "V").level == predict.WARN
    over = falling + [(T0 + datetime.timedelta(days=80), 12.0)]
    assert predict.trend_finding("k", "p", over, 12.2, False,
                                 "V").level == predict.WARN
    assert predict.trend_finding("k", "p", [], 12.2, False,
                                 "V").level == predict.NODATA


def test_compare_to_own():
    vals = [(T0 + datetime.timedelta(days=d), 10) for d in range(5)]
    assert predict.compare_to_own("k", "p", vals, "", True, .3, .6).level \
        == predict.OK
    worse = vals + [(T0 + datetime.timedelta(days=9), 17)]
    assert predict.compare_to_own("k", "p", worse, "", True, .3, .6).level \
        == predict.WARN
    assert predict.compare_to_own("k", "p", vals[:2], "", True, .3, .6
                                  ).level == predict.NODATA


def drive_rows(start, kind, n=30, temp_rate=0.1, cell_lead=19, lead_mv=20):
    rows = []
    for k in range(n):
        t = start + datetime.timedelta(seconds=10 * k)
        r = {"time": t.isoformat(), "pack_voltage": 400,
             "batt_temp_max": 30 + temp_rate * k}
        if kind == "drive":
            r.update(pack_current=100, motor_rpm=6000, motor_torque=60,
                     motor_temp=50 + 0.2 * k, soc=60)
        else:
            r.update(pack_current=-16, motor_rpm=0, motor_torque=0,
                     soc=90 + k * 0.4)
            for c in range(1, 13):
                r["cells_%d" % c] = 3.40 + (lead_mv / 1000 if c == cell_lead
                                            else 0)
        rows.append(r)
    return rows


def test_sessions_and_drive_checks(tmp_path):
    rows = []
    for d in range(6):
        day = T0 + datetime.timedelta(days=d)
        rows += drive_rows(day.replace(hour=8), "drive")
        rows += drive_rows(day.replace(hour=20), "charge",
                           lead_mv=20 if d < 5 else 45, cell_lead=7)
    write_csv(tmp_path / "drive_1.csv", rows)
    sess = predict.sessions(predict.read_rows([tmp_path / "drive_1.csv"]))
    assert [k for k, _ in sess] == ["drive", "charge"] * 6
    weak = predict.check_weak_cell(sess)
    assert "#7" in weak.summary and weak.level == predict.WARN
    eff = predict.check_drive_efficiency(sess)
    assert eff.level == predict.NODATA or eff.points  # tiny sessions
    assert predict.check_motor_heating(sess).level in (predict.OK,
                                                       predict.NODATA)


def test_12v_rest_uses_nights(tmp_path):
    rows = []
    for d in range(40):
        for h in (1, 2, 3, 14):
            t = T0 + datetime.timedelta(days=d, hours=h)
            rows.append({"time": t.isoformat(),
                         "aux_12v": 12.6 - 0.008 * d if h < 6 else 11.0})
    write_csv(tmp_path / "aux12v_202607.csv", rows)
    f = predict.check_12v_rest(str(tmp_path))
    assert len(f.points) == 40 and f.points[-1][1] > 12.2
    assert f.level == predict.WARN  # reaches 12.2 V within 3 months


def test_codes_and_assess(tmp_path):
    scans = [{"time": "t", "answered": ["7A1"], "codes": [
        {"header": "7A1", "code": "P1B27-05", "status": 0x89},
        {"header": "776", "code": "U0100-87", "status": 0x28}]}] * 3
    scans[-1] = {"time": "t", "answered": ["7A1"], "codes": scans[0][
        "codes"] + [{"header": "7A4", "code": "P1A89-00", "status": 0x04}]}
    (tmp_path / "dtc_history.json").write_text(json.dumps(scans))
    f = predict.check_codes(str(tmp_path))
    assert f.level == predict.WATCH
    assert "7A4 P1A89-00" in f.summary and "776" in f.summary
    found = predict.assess(str(tmp_path))
    assert found[0].key == "codes"  # worst first
    assert "เตือนล่วงหน้า" in predict.predict_html(found)


def test_notify_changes_once(tmp_path):
    sent = []
    state = str(tmp_path / "s.json")
    f = predict.Finding("k", "แบต 12V", predict.WATCH, "ลดลง")
    assert predict.notify_changes([f], state,
                                  lambda *a: sent.append(a)) == [f]
    assert predict.notify_changes([f], state, lambda *a: sent.append(a)) \
        == []
    f.level = predict.WARN
    assert predict.notify_changes([f], state, lambda *a: sent.append(a))
    assert len(sent) == 2 and sent[1][2] == "high"


def test_autopilot_sends_new_warnings_after_a_drive(tmp_path):
    from deepal_s05 import autopilot

    class Notifier:
        def __init__(self):
            self.sent = []

        def send(self, *a):
            self.sent.append(a)

    scans = [{"time": "t", "answered": ["7A4"], "codes": [
        {"header": "7A4", "code": "P1A89-00", "status": 0x04}]}]
    (tmp_path / "dtc_history.json").write_text(json.dumps(scans))
    n = Notifier()
    pilot = autopilot.Autopilot(lambda: None, [], [], str(tmp_path),
                                notifier=n)
    pilot.run_predict()
    pilot.run_predict()  # nothing new the second time
    assert len(n.sent) == 1 and "P1A89-00" in n.sent[0][1]
