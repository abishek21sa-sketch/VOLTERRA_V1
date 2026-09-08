"""Shared fixtures for the RL test modules -- synthetic sites/topology and synthetic trained
queue-risk models, so these tests run fast and don't need the real warehouse or the real trained
LightGBM models (mirrors test_queue_risk.py's own synthetic-data approach)."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from volterra_ml.queue_risk import train_all_targets
from volterra_ml.rl_routing import RealSite, RealVehicle, build_corridor_graph


def _synthetic_training_df(n: int = 800, seed: int = 11) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    stall_count = rng.integers(4, 40, n)
    max_power_kw = rng.uniform(100, 300, n)
    temp_c = rng.uniform(-20, 45, n)
    utilization = rng.uniform(0.5, 0.95, n)
    mean_wait_min = utilization * 10.0 / stall_count + rng.normal(0, 0.05, n)
    mean_wait_min = np.clip(mean_wait_min, 0.01, None)
    p_wait = np.clip(utilization, 0, 1)
    mean_queue_length = utilization * stall_count * 0.1
    p_wait_over_15min = np.clip(p_wait - 0.3, 0, 1)
    return pl.DataFrame({
        "stall_count": stall_count, "max_power_kw": max_power_kw, "temp_c": temp_c,
        "utilization": utilization, "mean_wait_min": mean_wait_min, "p_wait": p_wait,
        "mean_queue_length": mean_queue_length, "p_wait_over_15min": p_wait_over_15min,
    })


@pytest.fixture(scope="session")
def synthetic_models():
    df = _synthetic_training_df()
    models, _ = train_all_targets(df, seed=3)
    return models


@pytest.fixture
def chain_sites() -> list[RealSite]:
    # A-B-C: A-B and B-C are short real hops (~88mi each); A-C direct is longer (~177mi) but still
    # under the 200mi threshold used below, matching routing/'s own test-fixture design exactly.
    return [
        RealSite("A", "A", 8, 150.0, 36.0, -115.0),
        RealSite("B", "B", 8, 150.0, 37.0, -114.0),
        RealSite("C", "C", 8, 150.0, 38.0, -113.0),
    ]


@pytest.fixture
def chain_adjacency(chain_sites) -> dict[str, list[str]]:
    return build_corridor_graph(chain_sites, threshold_mi=200.0)


@pytest.fixture
def diamond_sites() -> list[RealSite]:
    # A real diamond verified the same way routing/test/runtests.jl's diamond_sites fixture is:
    # A-B, B-C, A-D, D-C each ~163.5mi (< 200mi); A-C ~220mi and B-D ~241.5mi (both > 200mi) -- so
    # exactly two real routes exist, A-B-C and A-D-C.
    return [
        RealSite("A", "A", 8, 150.0, 36.0, -115.0),
        RealSite("B", "B", 8, 150.0, 37.75, -113.0),
        RealSite("C", "C", 8, 150.0, 36.0, -111.0),
        RealSite("D", "D", 4, 150.0, 34.25, -113.0),
    ]


@pytest.fixture
def diamond_adjacency(diamond_sites) -> dict[str, list[str]]:
    return build_corridor_graph(diamond_sites, threshold_mi=200.0)


@pytest.fixture
def long_range_vehicle() -> RealVehicle:
    return RealVehicle(battery_kwh=100.0, consumption_kwh_per_mile=0.28)  # ~357mi true range


@pytest.fixture
def short_range_vehicle() -> RealVehicle:
    return RealVehicle(battery_kwh=50.0, consumption_kwh_per_mile=0.30)  # ~166.7mi true range
