import pytest

from deepal_s05.pids import SIGNALS, SIGNALS_BY_KEY, derived


def value(key, hexdata, capacity=56.1):
    return SIGNALS_BY_KEY[key].value(bytes.fromhex(hexdata), capacity)


def test_formulas():
    assert value("soc", "48") == 72
    assert value("pack_voltage", "0E02") == pytest.approx(358.6)
    assert value("pack_current", "1773") == pytest.approx(-1.2)
    assert value("cell_v_max", "0D03") == pytest.approx(3.331)
    assert value("batt_temp_max", "47") == 31
    assert value("charge_temp", "56") == pytest.approx(30.0)  # formula only
    assert value("soh", "0F0F78") == pytest.approx(98.7)
    assert value("soc_alt", "02D0") == pytest.approx(72.0)


def test_capacity_dependent_formulas():
    assert value("energy_available", "0FA0", 56.1) == pytest.approx(52.1)
    assert value("energy_available", "0FA0", 68.0) == pytest.approx(64.0)
    assert value("efc", "310DC8F8", 56.1) == pytest.approx(14.67, abs=1e-3)


def test_short_data_returns_none():
    assert value("soh", "0F") is None


def test_raw_only_signal():
    assert value("pack_power_raw", "00152A") is None


def test_all_requests_are_read_only():
    assert all(s.request.startswith("22") for s in SIGNALS)


def test_derived():
    out = derived({"pack_voltage": 350.0, "pack_current": 10.0,
                   "cell_v_max": 3.331, "cell_v_min": 3.328,
                   "batt_temp_max": 31, "batt_temp_min": 29})
    assert out["pack_power_kw"] == pytest.approx(3.5)
    assert out["cell_delta_mv"] == pytest.approx(3.0)
    assert out["temp_delta_c"] == 2


def test_odometer_from_gateway():
    from deepal_s05 import vehicles
    sig = vehicles.deepal_s05().signal("odometer_km")
    assert sig.header == "700" and sig.did == 0xF1AE
    assert sig.value(bytes.fromhex("8E880600")) == 42817.4


def test_trip_compares_with_the_odometer():
    from deepal_s05 import analysis
    rows = [{"time": "2026-10-17T08:%02d:00" % m, "speed_kmh": "60",
             "pack_power_kw": "10", "odometer_km": str(42817.4 + m)}
            for m in range(11)]
    (trip,) = analysis.trip_summaries(rows)
    assert trip["km"] == 10.0 and trip["km_odometer"] == 10.0
