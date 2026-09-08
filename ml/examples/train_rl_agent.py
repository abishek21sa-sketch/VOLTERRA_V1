"""Phase 10 demo: train the DQN charging-guidance agent on the real Las Vegas <-> Nephi, UT
corridor and evaluate it against the two real baselines this project's discipline requires --
Phase 9's style of static plan and a greedy/shortest-time policy -- on held-out episodes (a
different random seed than training, so this isn't grading the agent on data it trained on).

Run from ml/ (after `pip install -e .` and training the queue-risk models -- see ../README.md):
    python examples/train_rl_agent.py

Real: site coordinates/stall counts/max_power_kw, the queue-risk surrogate (itself validated
against simulation/'s real M/G/c model). Modeled: per-episode realized utilization
(Uniform(0.50, 0.95) per real site, matching the surrogate's own validated training domain -- see
rl_env.py), the representative vehicle spec, and everything already modeled in routing/'s
SOC-feasible path (circuity factor, highway speed, SOC conventions) -- see rl_routing.py.
"""

from __future__ import annotations

import numpy as np

from volterra_ml.queue_risk import load_models
from volterra_ml.rl_agent import DQNAgent, train_dqn
from volterra_ml.rl_baselines import greedy_policy, make_static_policy, plan_static_path, run_episode
from volterra_ml.rl_env import ChargingGuidanceEnv
from volterra_ml.rl_routing import RealVehicle, build_corridor_graph, load_corridor_sites

N_EVAL_EPISODES = 500
TRAIN_SEED = 0
EVAL_SEED = 12345  # different from TRAIN_SEED -- held-out episodes, not training data

# A representative real EV: comparable to several real vehicles Phase 9's demo sampled
# successfully for this same corridor (e.g. a real ~100kWh, ~0.30kWh/mi midsize EV).
VEHICLE = RealVehicle(battery_kwh=100.0, consumption_kwh_per_mile=0.30)


def summarize(name: str, results: list) -> None:
    successes = [r for r in results if r.success]
    success_rate = len(successes) / len(results)
    if successes:
        mean_hours = float(np.mean([r.total_time_hours for r in successes]))
        p90_hours = float(np.percentile([r.total_time_hours for r in successes], 90))
    else:
        mean_hours, p90_hours = float("nan"), float("nan")
    print(f"{name:10s}  success={success_rate:5.1%}  mean={mean_hours:6.3f}h  p90={p90_hours:6.3f}h  "
          f"(n={len(results)}, {len(successes)} succeeded)")


def main() -> None:
    sites = load_corridor_sites()
    adjacency = build_corridor_graph(sites, threshold_mi=250.0)
    models = load_models()

    site_names = {s.site_id: s.name for s in sites}
    origin = next(s.site_id for s in sites if "Las Vegas" in s.name)
    destination = next(s.site_id for s in sites if "Nephi" in s.name)

    print("=" * 100)
    print("VOLTERRA Phase 10 -- DQN charging-guidance agent vs. static plan vs. greedy")
    print(f"Real corridor: {site_names[origin]} -> {site_names[destination]}, "
          f"{len(sites)} real sites, vehicle: {VEHICLE.battery_kwh}kWh / {VEHICLE.consumption_kwh_per_mile}kWh-mi")
    print("=" * 100)

    train_env = ChargingGuidanceEnv(sites, adjacency, models, VEHICLE, origin, destination)
    print("\nTraining DQN agent...")
    agent: DQNAgent = train_dqn(train_env, n_episodes=2000, seed=TRAIN_SEED)
    print("Training complete.")

    static_path = plan_static_path(sites, adjacency, models, VEHICLE, origin, destination)
    print(f"\nStatic plan (Phase-9-style, chosen once using BASELINE_UTILIZATION only): "
          f"{' -> '.join(site_names[s] for s in static_path)}")

    eval_env = ChargingGuidanceEnv(sites, adjacency, models, VEHICLE, origin, destination)
    static_fn = make_static_policy(static_path)

    print(f"\nHeld-out evaluation ({N_EVAL_EPISODES} fresh episodes, seed={EVAL_SEED} -- not seen during training):")
    for name, policy_fn in [("Static", static_fn), ("Greedy", greedy_policy),
                             ("DQN", lambda env, obs: agent.act(env, obs))]:
        rng = np.random.default_rng(EVAL_SEED)  # same episode sequence for every policy -- fair comparison
        results = [run_episode(eval_env, policy_fn, rng) for _ in range(N_EVAL_EPISODES)]
        summarize(name, results)

    print("\n", "=" * 100)


if __name__ == "__main__":
    main()
