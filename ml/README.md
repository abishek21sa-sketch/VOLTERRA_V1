# ml

Demand forecasting, queue-risk forecasting, anomaly detection, and the RL charging-guidance
agent (Phase 10). Python, chosen specifically for this module's ecosystem — see root README's
"Why this stack".

## Status: queue-risk surrogate model + RL charging-guidance agent working, tested

```
pip install -e .          # pulls lightgbm, polars, scikit-learn, duckdb, torch
pip install -e .[dev]     # + pytest
pytest tests/ -v          # 34 tests (11 queue-risk, 23 RL), self-contained synthetic data, no Julia/warehouse needed

# 1. Generate training data (from simulation/, needs the warehouse built with epa_vehicle/noaa_forecast — Phase 6)
cd ../simulation && julia --project=../optimization examples/generate_queue_risk_dataset.jl

# 2. Train + evaluate + save (from ml/)
python examples/train_queue_risk_model.py

# 3. Check against real site configurations, independent of the training split
python examples/verify_real_sites.py

# 4. Precompute predictions for backend/'s GET /api/demand (all 17 real sites x 10 utilization levels)
python examples/predict_real_sites_grid.py

# 5. Train + evaluate the Phase 10 DQN charging-guidance agent against real static/greedy baselines
python examples/train_rl_agent.py

# 6. One real prediction per invocation, for backend/'s Phase 11 copilot tool to shell out to
python examples/predict_one.py <stall_count> <max_power_kw> <temp_c> <utilization>
```

`volterra_ml/queue_risk.py` trains four separate LightGBM regressors (`mean_wait_min`, `p_wait`,
`mean_queue_length`, `p_wait_over_15min` — reported separately, never blended into one score) that
approximate `../simulation/`'s own validated M/G/c queueing model in microseconds, without a Julia
process per query. **Read `queue_risk.py`'s module docstring before touching this**: the training
labels are outputs of simulation/'s already-validated Erlang-C + Allen-Cunneen model, not
fabricated demand data — Tesla has never published session-level demand, and nothing here claims
otherwise (see "Non-negotiable" below). Held-out performance is strong (R² 0.95-0.9997 on a random
split of the synthetic parameter sweep) and, more importantly, holds up on a genuinely independent
check against all 17 real Tesla sites' actual (stall_count, max_power_kw) at real NOAA
coldest/warmest temperatures on file (R² 0.987-0.9998) — see `examples/verify_real_sites.py`.

Real vs. swept inputs to the sweep dataset (`../simulation/examples/generate_queue_risk_dataset.jl`
generates it, not this module): battery size is sampled from the REAL EPA-derived distribution
(`../ingestion/supplemental/epa.py`); `stall_count`/`max_power_kw`/`temp_c`/`utilization` are
swept over a realistic range (`temp_c` doesn't need real-only coverage — `temperature_derate` is a
known deterministic function, not a hidden pattern). Full reasoning in that script's docstring.

Uses `.to_numpy()`, not `.to_pandas()`, throughout — polars' pandas conversion needs `pyarrow`,
an otherwise-unneeded dependency here since LightGBM/scikit-learn both accept plain numpy arrays.

Raw model output isn't automatically physically sensible — LightGBM regressors have no built-in
output-range constraint, and a real trained model predicted a tiny negative `p_wait_over_15min`
(-5.6e-05) near utilization=0.50 before `predict_queue_risk` started clamping every target to its
real bounds (`TARGET_BOUNDS`): probabilities to `[0, 1]`, wait time and queue length to `>= 0`.
Evaluation (`train_all_targets`) deliberately does NOT clamp — it reports the model's honest raw
error, not an artificially flattering one.

**`examples/predict_real_sites_grid.py`** precomputes predictions for all 17 real sites across the
same 10-level utilization grid, one file `backend/`'s `GET /api/demand` reads directly (see
`backend/internal/mlpredictions`'s doc comment for why a JSON file, not a warehouse table or a
live model call). Each site's grid uses its real current NOAA forecast temperature where one
exists (15 of 17); the 2 without one fall back to an assumed 20°C, explicitly tagged
`temp_source: "assumed_20c_no_real_forecast"` in the output rather than silently blended in.
Verified end-to-end in the actual running frontend, not just compiled: fetched both
`/api/sites` and `/api/demand` live and confirmed all 17 real sites join correctly by `site_id`.

Not yet built: demand forecasting proper (would need a real observed-demand signal that doesn't
exist yet) and anomaly detection.

## Phase 10: RL charging-guidance agent, evaluated against real baselines

`volterra_ml/rl_routing.py`, `rl_env.py`, `rl_baselines.py`, `rl_agent.py` implement a DQN
(`torch`) that learns which real site to route to next along the real Las Vegas <-> Nephi, UT
corridor Phase 9 already validated, evaluated against two real baselines per the root CLAUDE.md
discipline that RL must be compared, never presented standalone:

- **Static** — a route chosen once (Phase 9's style of planning), using only this project's
  `BASELINE_UTILIZATION` convention, then walked through each episode's actual realized
  congestion, blind to it.
- **Greedy** — adaptive and observant (it does see realized congestion at candidate next stops)
  but myopic, with no look-ahead and no memory of visited sites.

Realized congestion is a per-episode, per-real-site draw from `Uniform(0.50, 0.95)` — matching
`queue_risk.py`'s own validated training domain exactly, so every congestion query stays inside
the surrogate's confirmed-accurate range. This is a MODELED day-to-day variability assumption —
Tesla publishes no real per-site occupancy series to fit a real distribution to. Site topology,
real distances, and the real charging-curve model are ported directly from `graph/`, `routing/`,
and `simulation/`'s Julia code (no Python<->Julia bridge exists in this project, and this
environment needs microsecond-scale queries for RL training) — same formulas, same constants,
documented in `rl_routing.py`'s module docstring, not re-derived from scratch.

**Real result** (`examples/train_rl_agent.py`, 500 held-out episodes, same congestion draws across
all three policies for a fair paired comparison): Static succeeds 100% of the time (mean 6.115h,
p90 6.277h). Greedy actually does WORSE than Static despite being adaptive — 98.6% success (7 of
500 episodes truncate before reaching the destination, since nothing stops greedy from cycling
between real sites without a look-ahead or a visited-site memory) and a higher mean (6.517h) and
p90 (6.759h). DQN modestly beats Static on both counts (mean 6.111h, p90 6.249h) while matching
its 100% success rate. The honest finding: adaptivity alone (greedy) isn't enough and can actively
hurt — a policy needs to be BOTH adaptive to real-time conditions AND farsighted to reliably beat
a well-chosen static plan, and DQN (which is both) is the only one of the three that does, if only
by a modest real margin. 23 new tests (11 -> 34 total in `ml/`).

## Phase 11: a script for the Network Operations Copilot to call

`examples/predict_one.py` is a small one-shot script — one real `queue_risk.py` prediction per
invocation, printed as JSON to stdout — that `backend/internal/copilot` shells out to for its
`queue_risk_prediction` tool (see `backend/README.md`'s "Copilot" section). Not a new model, just
a thin CLI wrapper: `predict_real_sites_grid.py`'s fixed 17-site grid is precomputable in advance,
but the copilot needs arbitrary on-demand queries a user might ask about, so this stays a live
subprocess call — confirmed by direct measurement to take roughly 10-20 real seconds end to end
(mostly Python/LightGBM import and model-loading overhead), real but tolerable latency for an
interactive tool call.

## Non-negotiable

Every model output (`ml/` produces nothing else) is `modeled`, full stop — never rendered by the
frontend or copilot as if it were an observed Tesla/NREL/FHWA/Census/EIA/NOAA/EPA figure. See
[`../docs/data-sources.md`](../docs/data-sources.md). The queue-risk surrogate above is a second
remove from that: its *labels* are themselves a validated model's output, not even a direct
modeled assumption — see `volterra_ml/queue_risk.py`'s docstring for why that distinction matters
and is stated explicitly rather than left implicit.
