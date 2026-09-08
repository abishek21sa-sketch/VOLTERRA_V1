"""Tests for volterra_ml.rl_baselines -- the static-plan and greedy policies, plus the shared
episode runner. Self-contained (conftest.py fixtures)."""

from __future__ import annotations

import numpy as np
import pytest

from volterra_ml.rl_baselines import (
    _all_simple_paths,
    greedy_policy,
    make_static_policy,
    plan_static_path,
    run_episode,
)
from volterra_ml.rl_env import ChargingGuidanceEnv
from volterra_ml.rl_routing import RealSite, build_corridor_graph


def test_all_simple_paths_finds_exactly_the_two_real_routes(diamond_adjacency):
    paths = _all_simple_paths(diamond_adjacency, "A", "C")
    assert {tuple(p) for p in paths} == {("A", "B", "C"), ("A", "D", "C")}


def test_all_simple_paths_no_route_returns_empty():
    adjacency = {"A": [], "C": []}
    assert _all_simple_paths(adjacency, "A", "C") == []


def test_plan_static_path_prefers_the_higher_capacity_real_waypoint(diamond_sites, diamond_adjacency,
                                                                     synthetic_models, long_range_vehicle):
    # B has 8 real stalls, D has 4 -- the synthetic training data's mean_wait_min is inversely
    # proportional to stall_count at fixed utilization, so the real pooling-effect direction this
    # mirrors (more stalls -> shorter real queue) should make the B route strictly cheaper.
    path = plan_static_path(diamond_sites, diamond_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    assert path == ["A", "B", "C"]


def test_make_static_policy_ignores_realized_congestion(diamond_sites, diamond_adjacency, synthetic_models, long_range_vehicle):
    env = ChargingGuidanceEnv(diamond_sites, diamond_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    env.reset(np.random.default_rng(0))
    # Force D to look far cheaper than B in this episode's realized congestion -- the static
    # policy must still follow its precomputed plan (A-B-C) rather than reacting to it.
    env.realized_utilization["B"] = 0.95
    env.realized_utilization["D"] = 0.50

    policy = make_static_policy(["A", "B", "C"])
    b_idx = next(i for i, s in enumerate(diamond_sites) if s.site_id == "B")
    assert policy(env, None) == b_idx


def test_greedy_policy_reacts_to_realized_congestion(synthetic_models, long_range_vehicle):
    # B and D sit on the SAME longitude as A (pure latitude offsets, +-1.75deg), which makes their
    # real great-circle distance from A EXACTLY equal (haversine reduces to distance = R*|delta_lat|
    # when delta_lon=0, independent of hemisphere/base latitude) -- unlike this file's other tests'
    # diamond_sites fixture, whose B/D longitude differs from A's, which through the cos(latitude)
    # term makes A-B and A-D subtly unequal in real miles. That real ~2mi/~0.03h gap was enough to
    # swamp the deliberately small congestion-driven wait-time gap in an earlier version of this
    # test (a genuine bug in the TEST, not in greedy_policy) -- exact symmetry here isolates
    # realized congestion as the only thing that can differ between the two candidate hops.
    # C's own placement doesn't matter for this test: greedy_policy is myopic, so it never
    # considers the B->C or D->C leg when choosing the very first hop out of A.
    sites = [
        RealSite("A", "A", 8, 150.0, 36.0, -115.0),
        RealSite("B", "B", 8, 150.0, 37.75, -115.0),
        RealSite("C", "C", 8, 150.0, 36.0, -111.0),
        RealSite("D", "D", 8, 150.0, 34.25, -115.0),
    ]
    adjacency = build_corridor_graph(sites, threshold_mi=200.0)
    env = ChargingGuidanceEnv(sites, adjacency, synthetic_models, long_range_vehicle, "A", "C")
    obs = env.reset(np.random.default_rng(0))
    env.realized_utilization["B"] = 0.95  # very busy -> should be avoided
    env.realized_utilization["D"] = 0.50  # quiet -> should be preferred

    action = greedy_policy(env, obs)
    d_idx = next(i for i, s in enumerate(sites) if s.site_id == "D")
    assert action == d_idx  # picks the currently-cheaper real neighbor, unlike the static policy


def test_run_episode_reaches_destination_with_a_reasonable_policy(diamond_sites, diamond_adjacency,
                                                                    synthetic_models, long_range_vehicle):
    env = ChargingGuidanceEnv(diamond_sites, diamond_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    result = run_episode(env, greedy_policy, np.random.default_rng(1))

    assert result.success
    assert result.path[0] == "A"
    assert result.path[-1] == "C"
    assert result.total_time_hours > 0.0


def test_run_episode_matched_seeds_give_identical_realized_congestion(diamond_sites, diamond_adjacency,
                                                                       synthetic_models, long_range_vehicle):
    # The evaluation harness in examples/train_rl_agent.py relies on re-seeding the same rng value
    # to give every compared policy the identical sequence of episodes -- verify that property
    # holds rather than assuming numpy's Generator behaves that way.
    env = ChargingGuidanceEnv(diamond_sites, diamond_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    r1 = run_episode(env, greedy_policy, np.random.default_rng(42))
    r2 = run_episode(env, greedy_policy, np.random.default_rng(42))
    assert r1.path == r2.path
    assert r1.total_time_hours == pytest.approx(r2.total_time_hours)
