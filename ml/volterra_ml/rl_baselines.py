"""The two real baselines RL is evaluated against, plus the shared episode-runner both they and
the trained agent use — see rl_routing.py's module docstring for why these are the right
comparison points.

- **Static**: Phase 9's kind of plan — a route chosen ONCE, using only `BASELINE_UTILIZATION` (no
  knowledge of today's realized congestion), then walked through the episode's actual realized
  conditions. Computed here via exhaustive real-path enumeration (the real corridor only has 4
  sites, so this is exact and simple, not an approximation) rather than reimplementing Phase 9's
  full label-setting Dijkstra a third time.
- **Greedy**: adaptive and observant like the RL agent (it DOES see realized congestion at
  candidate next stops) but myopic — no look-ahead past the immediate next hop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .rl_env import ChargingGuidanceEnv, real_wait_hours
from .rl_routing import (
    AVG_HIGHWAY_SPEED_MPH,
    BASELINE_UTILIZATION,
    CHARGE_TARGET_SOC_MAX,
    MIN_SOC,
    ORIGIN_SOC,
    RealSite,
    RealVehicle,
    bucket_soc,
    charge_duration_hours,
    driving_distance_mi,
    soc_bucket,
)


@dataclass
class EpisodeResult:
    success: bool
    total_time_hours: float
    steps: int
    path: list[str] = field(default_factory=list)


def run_episode(env: ChargingGuidanceEnv, policy_fn, rng: np.random.Generator) -> EpisodeResult:
    """`policy_fn(env, observation) -> action_index`. Works for the static/greedy baselines and
    the trained RL agent alike -- same episode mechanics, same real cost accounting, for a fair
    comparison."""
    obs = env.reset(rng)
    total_reward = 0.0
    path = [env.current_site_id]
    for _ in range(env.max_steps):
        action = policy_fn(env, obs)
        result = env.step(action)
        total_reward += result.reward
        path.append(env.current_site_id)
        obs = result.observation
        if not result.valid:
            return EpisodeResult(success=False, total_time_hours=-total_reward, steps=len(path) - 1, path=path)
        if result.done:
            return EpisodeResult(success=True, total_time_hours=-total_reward, steps=len(path) - 1, path=path)
    return EpisodeResult(success=False, total_time_hours=-total_reward, steps=len(path) - 1, path=path)


def _all_simple_paths(adjacency: dict[str, list[str]], from_id: str, to_id: str, max_hops: int = 5) -> list[list[str]]:
    results: list[list[str]] = []

    def dfs(current: str, path: list[str], visited: set[str]):
        if current == to_id:
            results.append(list(path))
            return
        if len(path) > max_hops:
            return
        for nbr in adjacency.get(current, []):
            if nbr in visited:
                continue
            path.append(nbr)
            visited.add(nbr)
            dfs(nbr, path, visited)
            path.pop()
            visited.discard(nbr)

    dfs(from_id, [from_id], {from_id})
    return results


def _path_soc_feasible_and_cost(path: list[str], site_by_id: dict[str, RealSite], vehicle: RealVehicle,
                                 models: dict, utilization_fn) -> float | None:
    """Real SOC feasibility check (never drop below MIN_SOC) plus total expected cost for `path`,
    charging to CHARGE_TARGET_SOC_MAX at every non-destination stop -- same mechanics as
    ChargingGuidanceEnv.step, just evaluated once per candidate path instead of interactively.
    Returns None if `path` isn't SOC-feasible for `vehicle`."""
    soc = min(soc_bucket(ORIGIN_SOC), soc_bucket(CHARGE_TARGET_SOC_MAX))
    soc = bucket_soc(soc)
    total_hours = 0.0
    for i in range(len(path) - 1):
        a, b = site_by_id[path[i]], site_by_id[path[i + 1]]
        d_mi = driving_distance_mi(a, b)
        soc_needed = (d_mi * vehicle.consumption_kwh_per_mile) / vehicle.battery_kwh
        soc_after = bucket_soc(soc_bucket(soc)) - soc_needed
        if soc_after < MIN_SOC:
            return None
        total_hours += d_mi / AVG_HIGHWAY_SPEED_MPH
        soc = soc_after
        is_destination = i == len(path) - 2
        if not is_destination:
            soc_arrive = bucket_soc(soc_bucket(soc))
            if soc_arrive < CHARGE_TARGET_SOC_MAX:
                wait_hours = real_wait_hours(models, b, utilization_fn(b.site_id))
                charge_hours = charge_duration_hours(soc_arrive, CHARGE_TARGET_SOC_MAX, vehicle.battery_kwh, b.max_power_kw)
                total_hours += wait_hours + charge_hours
                soc = CHARGE_TARGET_SOC_MAX
    return total_hours


def plan_static_path(sites: list[RealSite], adjacency: dict[str, list[str]], models: dict,
                      vehicle: RealVehicle, origin_id: str, destination_id: str) -> list[str]:
    """The route Phase 9's style of planner would pick: minimum EXPECTED cost using only
    `BASELINE_UTILIZATION` at every site (no knowledge of any specific day's realized congestion),
    chosen once via exhaustive real-path enumeration over this small real corridor."""
    site_by_id = {s.site_id: s for s in sites}
    candidates = _all_simple_paths(adjacency, origin_id, destination_id)
    if not candidates:
        raise RuntimeError(f"no real path from {origin_id} to {destination_id} in this graph")

    best_path, best_cost = None, float("inf")
    for path in candidates:
        cost = _path_soc_feasible_and_cost(path, site_by_id, vehicle, models, lambda _sid: BASELINE_UTILIZATION)
        if cost is not None and cost < best_cost:
            best_path, best_cost = path, cost
    if best_path is None:
        raise RuntimeError(f"no real SOC-feasible path from {origin_id} to {destination_id} for this vehicle")
    return best_path


def make_static_policy(planned_path: list[str]):
    """Returns a `policy_fn` that always follows `planned_path`, blind to the episode's actual
    realized congestion -- committed once, exactly like Phase 9's static plan."""
    def policy_fn(env: ChargingGuidanceEnv, _obs: np.ndarray) -> int:
        idx_in_path = planned_path.index(env.current_site_id)
        next_site_id = planned_path[idx_in_path + 1]
        return next(i for i, s in enumerate(env.sites) if s.site_id == next_site_id)
    return policy_fn


def greedy_policy(env: ChargingGuidanceEnv, _obs: np.ndarray) -> int:
    """Picks whichever currently-valid real next site has the lowest IMMEDIATE real cost (drive +
    real wait/charge at that site, using the episode's actually realized congestion there) -- no
    look-ahead past this one hop."""
    mask = env.action_mask()
    current = env.site_by_id[env.current_site_id]
    best_i, best_cost = None, float("inf")
    for i, site in enumerate(env.sites):
        if not mask[i]:
            continue
        d_mi = driving_distance_mi(current, site)
        drive_hours = d_mi / AVG_HIGHWAY_SPEED_MPH
        cost = drive_hours
        if site.site_id != env.destination_id:
            soc_needed = (d_mi * env.vehicle.consumption_kwh_per_mile) / env.vehicle.battery_kwh
            soc_arrive = bucket_soc(env.soc_bucket_value) - soc_needed
            if soc_arrive < CHARGE_TARGET_SOC_MAX:
                wait_hours = real_wait_hours(env.models, site, env.realized_utilization[site.site_id])
                charge_hours = charge_duration_hours(soc_arrive, CHARGE_TARGET_SOC_MAX, env.vehicle.battery_kwh, site.max_power_kw)
                cost += wait_hours + charge_hours
        if cost < best_cost:
            best_i, best_cost = i, cost
    assert best_i is not None, "greedy_policy called with no valid action -- caller should have ended the episode"
    return best_i
