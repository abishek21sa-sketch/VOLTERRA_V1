"""Tests for volterra_ml.rl_routing -- the ported real geography/charging-curve formulas and real
network-topology helpers. Self-contained, no warehouse dependency."""

from __future__ import annotations

import pytest

from volterra_ml.rl_routing import (
    CIRCUITY_FACTOR,
    RealSite,
    bucket_soc,
    build_corridor_graph,
    charge_duration_hours,
    charging_power_fraction,
    driving_distance_mi,
    haversine_miles,
    soc_bucket,
    temperature_derate,
)


def test_haversine_miles_known_distance():
    assert haversine_miles(40.0, -74.0, 40.0, -74.0) == pytest.approx(0.0, abs=1e-9)
    nyc_la = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
    assert 2400.0 < nyc_la < 2500.0  # real, well-known NYC-LA great-circle distance
    assert haversine_miles(10.0, 20.0, 30.0, 40.0) == pytest.approx(haversine_miles(30.0, 40.0, 10.0, 20.0))


def test_driving_distance_applies_circuity_factor():
    a = RealSite("A", "A", 8, 150.0, 36.0, -115.0)
    b = RealSite("B", "B", 8, 150.0, 37.0, -114.0)
    d = driving_distance_mi(a, b)
    gc = haversine_miles(a.latitude, a.longitude, b.latitude, b.longitude)
    assert d == pytest.approx(gc * CIRCUITY_FACTOR)
    assert d > gc


def test_soc_bucket_bucket_soc():
    assert soc_bucket(0.0) == 0
    assert soc_bucket(0.049) == 0
    assert soc_bucket(0.05) == 1
    assert bucket_soc(soc_bucket(0.37)) <= 0.37  # flooring is always conservative


def test_charging_power_fraction_tapers_above_breakpoint():
    assert charging_power_fraction(0.0) == 1.0
    assert charging_power_fraction(0.5) == 1.0
    assert charging_power_fraction(0.75) < 1.0
    assert charging_power_fraction(1.0) == pytest.approx(0.2)  # TAPER_FLOOR_FRACTION


def test_temperature_derate_bounds():
    assert temperature_derate(20.0) == 1.0  # above start temp -- no derate
    assert temperature_derate(-30.0) == 0.5  # at/below floor -- floor fraction
    mid = temperature_derate(-5.0)
    assert 0.5 < mid < 1.0  # real linear interpolation in between


def test_charge_duration_hours_increases_with_less_power_and_more_energy():
    fast = charge_duration_hours(0.2, 0.5, 80.0, 250.0)
    slow = charge_duration_hours(0.2, 0.5, 80.0, 100.0)
    assert slow > fast  # less real rated power -> longer real charge time

    small_gain = charge_duration_hours(0.2, 0.3, 80.0, 150.0)
    big_gain = charge_duration_hours(0.2, 0.8, 80.0, 150.0)
    assert big_gain > small_gain  # more SOC to add -> longer real charge time

    with pytest.raises(AssertionError):
        charge_duration_hours(0.5, 0.5, 80.0, 150.0)  # soc_target must exceed soc_start


def test_build_corridor_graph_matches_known_real_topology():
    # Real coordinates for the same 4-site corridor Phase 9 validated -- confirms this Python port
    # of the edge rule reproduces the same real 250mi-threshold topology graph/'s Julia code found.
    lv = RealSite("lv", "Las Vegas, NV Supercharger", 8, 150.0, 36.1658953, -115.1389825)
    sg = RealSite("sg", "St. George, UT Supercharger", 8, 150.0, 37.1272069, -113.6033535)
    bv = RealSite("bv", "Beaver, UT Supercharger", 8, 150.0, 38.2490352, -112.6525174)
    npp = RealSite("np", "Nephi, UT Supercharger", 8, 150.0, 39.6778097, -111.8410686)

    adjacency = build_corridor_graph([lv, sg, bv, npp], threshold_mi=250.0)
    assert set(adjacency["lv"]) == {"sg", "bv"}       # LV-SG (~108mi) and LV-BV (~199mi) both real edges
    assert set(adjacency["sg"]) == {"lv", "bv", "np"}  # SG-BV (~93mi) and SG-NP (~200mi) too
    assert set(adjacency["bv"]) == {"lv", "sg", "np"}  # BV-NP (~108mi)
    assert "lv" not in adjacency["np"]                 # LV-NP direct (~302mi) exceeds the threshold
