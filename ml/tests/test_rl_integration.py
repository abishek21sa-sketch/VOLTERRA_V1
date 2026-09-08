"""Guarded integration test against the real warehouse and the real trained queue-risk models --
skipped if either isn't present (mirrors the pattern optimization/graph/routing's Julia test
suites use for their own real-warehouse integration testsets)."""

from __future__ import annotations

import numpy as np
import pytest

from volterra_ml.queue_risk import MODEL_DIR
from volterra_ml.rl_routing import DEFAULT_WAREHOUSE_PATH


def _real_prereqs_present() -> bool:
    return DEFAULT_WAREHOUSE_PATH.exists() and MODEL_DIR.exists()


@pytest.mark.skipif(not _real_prereqs_present(), reason="real warehouse or trained queue-risk models not present")
def test_real_corridor_end_to_end():
    from volterra_ml.queue_risk import load_models
    from volterra_ml.rl_baselines import greedy_policy, make_static_policy, plan_static_path, run_episode
    from volterra_ml.rl_env import ChargingGuidanceEnv
    from volterra_ml.rl_routing import RealVehicle, build_corridor_graph, load_corridor_sites

    sites = load_corridor_sites()
    assert len(sites) == 4  # the real Las Vegas/St.George/Beaver/Nephi corridor

    adjacency = build_corridor_graph(sites, threshold_mi=250.0)
    models = load_models()
    vehicle = RealVehicle(battery_kwh=100.0, consumption_kwh_per_mile=0.30)

    origin = next(s.site_id for s in sites if "Las Vegas" in s.name)
    destination = next(s.site_id for s in sites if "Nephi" in s.name)

    static_path = plan_static_path(sites, adjacency, models, vehicle, origin, destination)
    assert static_path[0] == origin
    assert static_path[-1] == destination

    env = ChargingGuidanceEnv(sites, adjacency, models, vehicle, origin, destination)
    static_result = run_episode(env, make_static_policy(static_path), np.random.default_rng(0))
    greedy_result = run_episode(env, greedy_policy, np.random.default_rng(0))

    assert static_result.success
    assert greedy_result.success
    assert static_result.total_time_hours > 0.0
    assert greedy_result.total_time_hours > 0.0
