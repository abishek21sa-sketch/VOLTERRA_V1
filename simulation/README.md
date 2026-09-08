# simulation

Discrete-event charging simulator built on ConcurrentSim.jl, plus the analytical M/G/c queueing
model each site's discrete-event results validate against. Each Supercharger site is modeled as
an M/G/c queueing system: heterogeneous, non-exponential service times driven by SOC, vehicle
battery size, charger power, and temperature — not a textbook M/M/c.

## Status: working, tested, validated against real sites

```
julia --project=. -e "using Pkg; Pkg.instantiate()"
julia --project=. test/runtests.jl               # 56 tests
julia --project=. examples/site_queueing_demo.jl # analytical vs. simulated, 3 real sites
julia --project=. examples/reliability_demo.jl   # Phase 12: failure/repair calibrated to Tesla's real uptime

# Queue-risk ML training data (Phase 6, needs DuckDB.jl + a VolterraSimulation path dep — uses
# optimization/'s Project.toml rather than adding those to this module's, see the script itself):
julia --project=../optimization examples/generate_queue_risk_dataset.jl  # -> ../ml/data/*.csv, see ../ml/README.md
```

- `src/charging_curve.jl` — vehicle charging-power taper (SOC-dependent) and temperature derate.
  **Modeled**, not observed — Tesla doesn't publish per-vehicle charging curves; see
  `../docs/data-sources.md`. The temperature *input* is real as of Phase 6, though: every prior
  run in this repo called `temp_c` at its hardcoded 20.0 default, but
  `../warehouse/verify/temperature_scenarios.jl` now feeds real NOAA forecast temperatures
  (`../ingestion/supplemental/noaa.py`) through it — real cold derating at the coldest forecast
  period on file lengthens service time 4.9% and mean queue wait from 1.59 to 2.6 minutes; the
  warmest period on file shows 0% effect, honestly surfacing that this model only derates for
  cold, never heat.
- `src/queueing.jl` — analytical M/G/c model: Erlang-C + Allen-Cunneen approximation, the same
  combination the sibling AirlinesApp project's `api/queue_pressure.py` uses for a parallel
  purpose. Reports utilization, `P(wait>0)`, mean wait, mean queue length, `P(wait>threshold)` as
  separate fields — never blended into one score.
- `src/des.jl` — the ConcurrentSim.jl discrete-event simulation: EVs arrive (Poisson, modeled λ),
  draw a random battery size and initial SOC (modeled distributions), compute service time from
  the real charging-curve model, and queue for a `Resource` sized to the site's **real**
  `stall_count`. The battery-size distribution is where Phase 6 landed its most consequential
  finding — see below.
- `src/reliability.jl` (Phase 12) — per-stall failure/repair, layered onto `des.jl`'s engine via
  one extra `Resource`-competing process per real stall. Calibrated against Tesla's **real**
  published aggregate Supercharger uptime (99.95%, 2024 Impact Report) — `MTTR_HOURS` is a
  modeled repair-time assumption, `MTBF_HOURS` is *derived* from it so per-stall availability
  reproduces that real target exactly. A genuinely different resilience question from `graph/`'s
  Phase 5 topology resilience: component-level failure/repair, not whole-site connectivity loss.

## Validated

`examples/site_queueing_demo.jl` runs three real sites spanning the observed capacity range
(6/8/32 stalls) at a fixed 80% target utilization. Analytical and simulated results agree closely
at all three (e.g. Charleston, WV: analytical mean wait 2.96 min vs. simulated 3.06 min over a
30-day run), and the results correctly reproduce the queueing "pooling effect" — the 32-stall
site has roughly a third the wait-probability of the 6-stall site at identical utilization, which
is real Erlang-C scaling behavior, not an artifact.

**Caveat on that 80% figure, found in Phase 6**: the demo's `BATTERY_KWH_DIST = Uniform(60,100)`
was always an arbitrary modeled choice, never grounded in a real vehicle-mix sample.
`../warehouse/verify/vehicle_mix_scenarios.jl` now substitutes a real empirical distribution —
bootstrap-sampled from 1,189 real 2023+ EV model-year variants across 35 makes
(`../ingestion/supplemental/epa.py`, EPA/DOE data) — at the same three real sites and the same
tuned arrival rate. Result: real mean battery size (111.3 kWh) is 39% larger than the arbitrary
assumption's mean (80 kWh), lengthening service time 39.4% and pushing utilization from 0.80 past
1.0 — an unstable queue — at **all three** sites. The demo's "80% utilization" figure above is
correctly computed under its stated assumption, but that assumption is now known to understate
real-world battery sizes; treat it as a controlled comparison point, not a claim about real-world
site loading.

`test/runtests.jl` additionally validates `erlang_c` against the exact M/M/1 identity
(`erlang_c(1, ρ) == ρ`), validates `mgc_queue`'s M/M/c special case (`SCV=1`) against a
from-scratch reference M/M/c simulation independent of `des.jl`, and checks `des.jl`'s own
internal consistency (record ordering, Little's-Law-implied non-negativity).

## Reliability (Phase 12): a real, honest finding at two different timescales

`examples/reliability_demo.jl` calibrates `MTBF_HOURS`/`MTTR_HOURS` so per-stall availability
reproduces Tesla's real 99.95% published uptime exactly (MTTR modeled at 24h -> derived MTBF
~5.5 years), then runs the same three real sites `site_queueing_demo.jl` uses with and without
failures. **At the real target, over a real-scale 90-day operating window, queueing degradation
is genuinely negligible** (0.0-3.2% across the three real sites) — expected failures per stall in
that window are well under one, so this isn't a modeling failure, it's an honest finding: Tesla's
real reported reliability doesn't meaningfully stress real queueing at realistic timescales.

To show the same mechanism has real teeth (not just "no effect because nothing happens at any
scale"), the demo also sweeps MODELED what-if availability levels below the real target, MTTR
held fixed: at 99% availability, mean wait at Charleston, WV rises 23.6%; at 95%, it more than
doubles (+116.7%). Site-level total-outage probability (`site_full_outage_probability`, a
closed-form calculation, not simulated) stays separately vanishingly small at every real stall
count even at these degraded availability levels — at the real target it's `1.56e-20` (6 stalls)
down to `2.33e-106` (32 stalls); computing the same formula at the 95% what-if level still gives
`1.6e-8` (6 stalls) and `2.3e-42` (32 stalls). Redundancy makes total site outage a non-concern
across this whole sweep, but does NOT eliminate the real, separate risk of degraded queueing from
partial failures. Two metrics, reported separately, never blended, per this project's
cross-cutting discipline.

## Scope boundary

This module simulates; `../optimization/` decides. It consumes optimizer outputs (proposed site
locations/capacities) to evaluate them under stochastic demand — it never duplicates
facility-location or capacity-selection logic itself.
