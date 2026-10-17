import json
import os

from deepal_s05 import tyres, vehicles
from deepal_s05.elm327 import NoData


class WheelElm:
    def __init__(self, kmh):
        self.kmh = kmh  # four wheel speeds
        self.asked = []

    def read_did(self, header, did):
        self.asked.append((header, did))
        if header != "780" or not 0xFD0C <= did <= 0xFD0F:
            raise NoData("NO DATA")
        return round(self.kmh[did - 0xFD0C] * 10).to_bytes(2, "big")


def feed(m, speeds, n):
    for _ in range(n):
        assert m.add(speeds)


def test_samples_only_count_when_straight_and_fast():
    assert tyres.shares([20, 20, 20, 20]) is None  # too slow
    assert tyres.shares([80, 80, 80, None]) is None
    assert tyres.shares([81, 79, 81, 79]) is None  # bend: 1.25 % off
    assert tyres.shares([80, 80, 80, 80]) == [1, 1, 1, 1]
    m = tyres.Monitor()
    assert not m.add([80, 80, 80, 80], torque=150)  # hard acceleration


def test_learns_then_warns_about_the_fast_wheel(tmp_path):
    m = tyres.Monitor(str(tmp_path))
    base = [80.2, 80.0, 79.8, 80.0]  # wheels differ a little anyway
    feed(m, base, tyres.BASELINE_N)
    assert m.state["baseline"] is not None
    assert m.summary()["state"] == "collecting"
    assert m.values() == {}
    feed(m, base, tyres.MIN_RECENT)
    s = m.summary()
    assert s["state"] == "ok" and max(abs(d) for d in s["dev"]) < 0.01
    # rear right loses pressure: 0.8 % faster
    feed(m, [80.2, 80.0, 79.8, 80.64], 3 * tyres.RECENT_N)
    s = m.summary()
    assert s["state"] == "soft" and s["worst"] == 3
    assert "หลังขวา" in s["text"]
    v = m.values()
    assert v["tyre_soft_wheel"] == "หลังขวา" and v["tyre_soft_pct"] > 0.5
    rule = [r for r in vehicles.DEEPAL_RULES if r["key"] == "tyre_soft_pct"]
    assert rule[0]["warn_above"] == tyres.WARN_PCT
    # kept on disk, and a reset from the command line is picked up
    m.save()
    again = tyres.Monitor(str(tmp_path))
    assert again.summary()["state"] == "soft"
    tyres.Monitor(str(tmp_path)).reset()
    os.utime(m.path, (1, 1))  # a different mtime even on coarse clocks
    m.add(base)
    assert m.state["baseline"] is None and m.state["learn_n"] == 1
    assert "กำลังเรียนรู้" in tyres.report(str(tmp_path))
    assert json.load(open(m.path))["reset"]


def test_round_reads_wheels_only_while_driving(tmp_path):
    m = tyres.Monitor(str(tmp_path))
    elm = WheelElm([60, 60, 60, 60])
    values = {"speed_kmh": 10}
    assert m.round(elm, values)["state"] == "learning"
    assert elm.asked == []
    values = {"speed_kmh": 62, "motor_torque": 20}
    m.round(elm, values)
    assert len(elm.asked) == 4
    assert values["wheel_rr_kmh"] == 60 and m.state["learn_n"] == 1
