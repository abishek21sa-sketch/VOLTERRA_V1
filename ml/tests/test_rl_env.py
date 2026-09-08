"""Tests for volterra_ml.rl_env -- ChargingGuidanceEnv's reset/step/action_mask mechanics.
Self-contained: synthetic sites/topology (conftest.py) and synthetic trained queue-risk models, no
real warehouse or real trained model files needed."""

from __future__ import annotations

import numpy as np
import pytest

from volterra_ml.rl_env import ChargingGuidanceEnv, REALIZED_UTILIZATION_RANGE
from volterra_ml.rl_routing import (
    AVG_HIGHWAY_SPEED_MPH,
    CHARGE_TARGET_SOC_MAX,
    bucket_soc,
    driving_distance_mi,
)


def test_reset_starts_at_origin_capped_at_charge_target(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle):
    env = ChargingGuidanceEnv(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    obs = env.reset(np.random.default_rng(0))

    assert env.current_site_id == "A"
    assert bucket_soc(env.soc_bucket_value) == CHARGE_TARGET_SOC_MAX  # origin SOC capped at the charge target, not 0.90
    assert obs.shape == (env.observation_dim,)
    for site in chain_sites:
        assert REALIZED_UTILIZATION_RANGE[0] <= env.realized_utilization[site.site_id] <= REALIZED_UTILIZATION_RANGE[1]


def test_action_mask_excludes_self_loop_and_non_edges(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle):
    env = ChargingGuidanceEnv(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    env.reset(np.random.default_rng(0))
    mask = env.action_mask()
    a_idx = next(i for i, s in enumerate(chain_sites) if s.site_id == "A")
    assert not mask[a_idx]  # self-loop never valid
    assert mask.sum() >= 1  # at least one real reachable neighbor from A


def test_driving_directly_to_the_destination_needs_no_charging(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle):
    env = ChargingGuidanceEnv(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    env.reset(np.random.default_rng(0))
    c_idx = next(i for i, s in enumerate(chain_sites) if s.site_id == "C")

    result = env.step(c_idx)

    assert result.valid
    assert result.done
    a, c = chain_sites[0], chain_sites[2]
    expected_drive_hours = driving_distance_mi(a, c) / AVG_HIGHWAY_SPEED_MPH
    assert result.reward == pytest.approx(-expected_drive_hours)  # no charging cost at the destination


def test_driving_to_an_intermediate_stop_charges_back_to_target(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle):
    env = ChargingGuidanceEnv(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    env.reset(np.random.default_rng(0))
    b_idx = next(i for i, s in enumerate(chain_sites) if s.site_id == "B")

    result = env.step(b_idx)

    assert result.valid
    assert not result.done
    a, b = chain_sites[0], chain_sites[1]
    drive_hours = driving_distance_mi(a, b) / AVG_HIGHWAY_SPEED_MPH
    assert result.reward < -drive_hours  # charging/waiting adds real cost beyond just driving
    assert bucket_soc(env.soc_bucket_value) == CHARGE_TARGET_SOC_MAX  # topped back up


def test_invalid_action_ends_episode_with_penalty(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle):
    env = ChargingGuidanceEnv(chain_sites, chain_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    env.reset(np.random.default_rng(0))
    a_idx = next(i for i, s in enumerate(chain_sites) if s.site_id == "A")  # self-loop -- always invalid

    result = env.step(a_idx)

    assert not result.valid
    assert result.reward < -50.0  # a real, large penalty, not a small one that could be confused with normal cost


def test_soc_infeasible_hop_is_masked_out(chain_sites, chain_adjacency, synthetic_models, short_range_vehicle):
    env = ChargingGuidanceEnv(chain_sites, chain_adjacency, synthetic_models, short_range_vehicle, "A", "C")
    env.reset(np.random.default_rng(0))
    c_idx = next(i for i, s in enumerate(chain_sites) if s.site_id == "C")

    # A-C's real driving distance must exceed short_range_vehicle's real range for this to be a
    # meaningful test -- verified here rather than assumed.
    a, c = chain_sites[0], chain_sites[2]
    ac_driving_mi = driving_distance_mi(a, c)
    assert ac_driving_mi * short_range_vehicle.consumption_kwh_per_mile > short_range_vehicle.battery_kwh * CHARGE_TARGET_SOC_MAX

    mask = env.action_mask()
    assert not mask[c_idx]  # direct A->C isn't offered as a valid action for this vehicle
