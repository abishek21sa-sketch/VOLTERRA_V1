"""The charging-guidance environment and its three compared policies (static, greedy, RL) — see
rl_routing.py's module docstring for the real data grounding and the reasoning behind this
three-way comparison.

**Per-episode realized congestion**: each real intermediate site's utilization for a given trip is
drawn i.i.d. `Uniform(0.50, 0.95)` — matching `queue_risk.py`'s own real validated training domain
exactly (`simulation/examples/generate_queue_risk_dataset.jl`'s `UTILIZATION_RANGE`), so every
congestion query stays inside the surrogate's confirmed-accurate range rather than extrapolating.
This is a MODELED day-to-day variability assumption — Tesla publishes no real per-site occupancy
time series to fit a real distribution to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .queue_risk import load_models, predict_queue_risk
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

REALIZED_UTILIZATION_RANGE = (0.50, 0.95)  # matches queue_risk.py's own validated training domain


def sample_realized_utilization(rng: np.random.Generator, n_sites: int) -> np.ndarray:
    lo, hi = REALIZED_UTILIZATION_RANGE
    return rng.uniform(lo, hi, size=n_sites)


def real_wait_hours(models: dict, site: RealSite, utilization: float, *, temp_c: float = 20.0) -> float:
    """Real M/G/c mean wait (via the validated LightGBM surrogate) at `site`, at `utilization` —
    NOT this project's `BASELINE_UTILIZATION`, the caller's realized (or planning-assumption)
    value."""
    pred = predict_queue_risk(models, stall_count=site.stall_count, max_power_kw=site.max_power_kw,
                               temp_c=temp_c, utilization=utilization)
    return pred["mean_wait_min"] / 60.0


@dataclass
class StepResult:
    observation: np.ndarray
    reward: float
    done: bool
    truncated: bool
    valid: bool
    info: dict = field(default_factory=dict)


class ChargingGuidanceEnv:
    """One real corridor trip, origin -> destination, real driving + real congestion-dependent
    queueing + real charging-curve time, one action per decision: which real site to drive to
    next. Charging amount is NOT a separate decision -- on arrival at any non-destination real
    site, the vehicle always charges up to `CHARGE_TARGET_SOC_MAX` (this project's standard
    `TARGET_SOC` convention), keeping the learned decision squarely about ROUTE choice, matching
    how real EV trip-planning apps (Tesla's own, ABRP, PlugShare) primarily let a driver choose
    *which* stations to route through, not how many kWh to add at each.
    """

    def __init__(self, sites: list[RealSite], adjacency: dict[str, list[str]], models: dict,
                 vehicle: RealVehicle, origin_id: str, destination_id: str, *, max_steps: int = 10):
        self.sites = sites
        self.site_by_id = {s.site_id: s for s in sites}
        self.adjacency = adjacency
        self.models = models
        self.vehicle = vehicle
        self.origin_id = origin_id
        self.destination_id = destination_id
        self.max_steps = max_steps
        self.n_sites = len(sites)

        self.current_site_id: str = origin_id
        self.soc_bucket_value: int = 0
        self.realized_utilization: dict[str, float] = {}
        self.steps = 0

    def action_mask(self) -> np.ndarray:
        """True where action i (drive to sites[i]) is a real, currently-feasible move: a real edge
        exists from the current site, it isn't a self-loop, and the vehicle's real SOC covers the
        real energy needed without dropping below `MIN_SOC`."""
        mask = np.zeros(self.n_sites, dtype=bool)
        current = self.site_by_id[self.current_site_id]
        neighbors = self.adjacency[self.current_site_id]
        soc_now = bucket_soc(self.soc_bucket_value)
        for i, site in enumerate(self.sites):
            if site.site_id == self.current_site_id or site.site_id not in neighbors:
                continue
            d_mi = driving_distance_mi(current, site)
            soc_needed = (d_mi * self.vehicle.consumption_kwh_per_mile) / self.vehicle.battery_kwh
            if soc_now - soc_needed >= MIN_SOC:
                mask[i] = True
        return mask

    def _observation(self) -> np.ndarray:
        onehot = np.zeros(self.n_sites, dtype=np.float32)
        onehot[[i for i, s in enumerate(self.sites) if s.site_id == self.current_site_id][0]] = 1.0
        soc_norm = np.array([bucket_soc(self.soc_bucket_value)], dtype=np.float32)
        utils = np.array([self.realized_utilization[s.site_id] for s in self.sites], dtype=np.float32)
        return np.concatenate([onehot, soc_norm, utils])

    @property
    def observation_dim(self) -> int:
        return self.n_sites + 1 + self.n_sites

    def reset(self, rng: np.random.Generator) -> np.ndarray:
        self.current_site_id = self.origin_id
        self.soc_bucket_value = min(soc_bucket(ORIGIN_SOC), soc_bucket(CHARGE_TARGET_SOC_MAX))
        draws = sample_realized_utilization(rng, self.n_sites)
        self.realized_utilization = {s.site_id: float(u) for s, u in zip(self.sites, draws)}
        self.steps = 0
        return self._observation()

    def step(self, action_index: int) -> StepResult:
        mask = self.action_mask()
        if not mask[action_index]:
            # An invalid action ends the episode with a real, large penalty -- callers that use
            # action_mask() to restrict choices (the RL agent during training/eval, the baselines)
            # should never actually trigger this path; it exists so an untrained/exploring policy
            # gets an honest, strongly negative signal rather than undefined behavior.
            return StepResult(self._observation(), reward=-100.0, done=False, truncated=True,
                               valid=False, info={"reason": "invalid_action"})

        target = self.sites[action_index]
        current = self.site_by_id[self.current_site_id]
        d_mi = driving_distance_mi(current, target)
        soc_needed = (d_mi * self.vehicle.consumption_kwh_per_mile) / self.vehicle.battery_kwh
        soc_after_drive = bucket_soc(self.soc_bucket_value) - soc_needed
        drive_hours = d_mi / AVG_HIGHWAY_SPEED_MPH

        self.current_site_id = target.site_id
        self.soc_bucket_value = soc_bucket(soc_after_drive)
        reward = -drive_hours

        done = target.site_id == self.destination_id
        if not done:
            soc_arrive = bucket_soc(self.soc_bucket_value)
            if soc_arrive < CHARGE_TARGET_SOC_MAX:
                wait_hours = real_wait_hours(self.models, target, self.realized_utilization[target.site_id])
                charge_hours = charge_duration_hours(soc_arrive, CHARGE_TARGET_SOC_MAX,
                                                       self.vehicle.battery_kwh, target.max_power_kw)
                reward -= (wait_hours + charge_hours)
                self.soc_bucket_value = soc_bucket(CHARGE_TARGET_SOC_MAX)

        self.steps += 1
        truncated = (not done) and self.steps >= self.max_steps
        return StepResult(self._observation(), reward=reward, done=done, truncated=truncated, valid=True)
