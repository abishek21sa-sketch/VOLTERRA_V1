# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## What this is

VOLTERRA is an EV charging-network and grid decision-intelligence platform: real Tesla
Supercharger network data (primary source) plus supplemental public datasets (NREL, FHWA,
Census, EIA, NOAA, EPA) feeding a set of real operations-research tools — facility location,
charger-capacity/power optimization, queueing theory (M/G/c), a discrete-event charging
simulator, network resilience/criticality analysis, grid-aware and battery-storage optimization,
demand/queue forecasting, component-level reliability engineering, and an RL-based
charging-guidance agent — surfaced through a full-screen network-map interface with a "Network
Operations Copilot" agent on top. See [README.md](README.md) for the full concept and
[docs/roadmap.md](docs/roadmap.md) for build order. **All 12 planned phases are working, real,
and tested** — this is no longer scaffold-stage; see the root README's Status section and
docs/roadmap.md for exactly what's real vs. modeled per phase, and for the real gaps that remain
by design (Phase 7's battery dispatch scheduling, Phase 8's demand-growth uncertainty, Phase 11's
untested live-model tool selection, real demand forecasting). Update this file's "Commands" and
"Architecture" sections as real code changes in each module.

## Non-negotiable project discipline

**OEM-neutral, always.** Tesla is the empirical case study, not a hardcoded assumption. Nothing
in the schema, optimizer, or UI should require "Tesla" as a value rather than treating it as one
row in a `network_operator` field.

**Real vs. modeled data must stay labeled at the field level.** Every value that isn't observed
Tesla/NREL/FHWA/Census/EIA/NOAA/EPA data — station-level demand, queueing parameters not directly
measurable, forecasts — carries an explicit `source: modeled|estimated|<agency>` tag through the
warehouse and into anything the frontend or copilot renders. Never let a modeled number look like
an observed one. This mirrors the sibling AirlinesApp project's rule that score components are
always reported separately, never folded into one invented number — same instinct, applied to
provenance instead of aggregation.

**Ingestion is versioned, never destructive.** Tesla's network changes over time (sites added,
stall counts change, power upgrades). `ingestion/` writes dated, immutable snapshots to
`data/snapshots/` — it never overwrites a prior snapshot. The warehouse is built by replaying
snapshots forward, so "network expansion over time" is always reconstructable.

**Respect the source, don't hammer it.** Tesla's Find Us pages are not a public API. Any
collector must be rate-limited, cache aggressively, identify itself, and be safe to stop and
resume. Treat NREL/FHWA/Census/EIA/NOAA/EPA the same way even where they do offer real APIs —
respect published rate limits and cache raw responses under `data/raw/` before any processing.

## Commands

Toolchains available in this environment as of Phase 2: Julia 1.12, Node 24 / npm 11 / Angular
CLI 22, Python 3.14, Go 1.26 (installed but not on PATH by default in a fresh shell — see below),
DuckDB CLI 1.5.5 (winget, same). As of Phase 11, both Go and the DuckDB CLI were confirmed already
installed on this machine, just not on this session's shell `PATH` — see `backend/README.md`'s
"Toolchain note" for the exact paths found (a `winget list`/`WinGet\Packages` directory search),
which differ from an earlier session's note there about a portable-zip Go install; both approaches
work identically once the binary is found, so this isn't a contradiction, just two different
sessions solving the same PATH problem differently.

```
# Julia modules (optimization/, simulation/) — run from the module directory
julia --project=. -e "using Pkg; Pkg.instantiate()"

# simulation/ tests and demos (real, passing — see simulation/README.md)
julia --project=. test/runtests.jl
julia --project=. examples/site_queueing_demo.jl
julia --project=. examples/reliability_demo.jl

# optimization/ tests and demos (real, passing — see optimization/README.md)
julia --project=. test/runtests.jl
julia --project=. examples/capacity_portfolio_demo.jl
julia --project=. examples/battery_storage_demo.jl
julia --project=. examples/stochastic_capacity_selection_demo.jl

# graph/ tests and demo (real, passing — Julia/Graphs.jl; the real Memgraph path now also exists,
# in backend/internal/graphdb, cross-validated against this Julia output — see graph/README.md)
julia --project=. test/runtests.jl
julia --project=. examples/resilience_demo.jl

# routing/ tests and demos (real, passing — see routing/README.md)
julia --project=. test/runtests.jl
julia --project=. examples/soc_path_demo.jl
julia --project=. examples/traffic_assignment_demo.jl

# Tesla collector — from a residential network, not a cloud sandbox (see ingestion/tesla/README.md)
pip install -e ingestion
python -m tesla.collector --limit 15   # or no --limit for the full ~1000+-site network (~3h)

# NREL AFDC collector — real API, no browser needed; DEMO_KEY is capped at 10 req/hour on this
# endpoint (see ingestion/supplemental/README.md) — set NREL_API_KEY for a real pull
python -m supplemental.nrel --states NV,UT,CA --max-records 200

# FHWA corridor collector — real API, no key, no rate limit hit; pulls the full national
# EV-designated network in one request
python -m supplemental.fhwa

# EIA electricity price collector — real API, DEMO_KEY capped at 10 req/hour on this endpoint
# (same api-umbrella gateway as NREL); one request covers every state passed
python -m supplemental.eia --year 2024

# NOAA NWS forecast collector — real API, genuinely keyless; pulls the current forecast for every
# site in the warehouse (or specific sites via --site id,lat,lon)
python -m supplemental.noaa --from-warehouse

# EPA/DOE vehicle efficiency collector — real bulk CSV, no key; filters to battery-electric,
# model year 2023+ by default
python -m supplemental.epa

# OpenEI/NREL Utility Rate Database demand-charge collector — real API, DEMO_KEY capped at
# 10 req/hour; queries each real site's own lat/lon, not a state average
python -m supplemental.openei --from-warehouse

# NREL/NLR ATB battery storage cost collector — real bulk CSV, no key; current-year projection by default
python -m supplemental.nrel_atb

# Census: BLOCKED, not a command to run — the ACS API requires a real key with no DEMO_KEY
# equivalent, and getting one means submitting an email to an external form (see CLAUDE.md
# Architecture section and docs/data-sources.md).

# Warehouse build/rebuild — loads all eight sources' data/snapshots/*/*.json
pip install duckdb
python warehouse/build_warehouse.py
python warehouse/verify/corridor_alignment.py  # real Tesla coords vs. real FHWA corridor geometry
julia --project=optimization warehouse/verify/temperature_scenarios.jl  # real NOAA temps -> queueing model
julia --project=optimization warehouse/verify/vehicle_mix_scenarios.jl  # real EPA battery mix -> queueing model

# ml/ — venv recommended, same pattern as ingestion. Queue-risk surrogate (Phase 6) + RL agent (Phase 10), working:
pip install -e ml[dev]
cd simulation && julia --project=../optimization examples/generate_queue_risk_dataset.jl  # generates ml/data/*.csv
cd ml && python examples/train_queue_risk_model.py   # trains + saves 4 LightGBM models
python examples/verify_real_sites.py                 # checks against all 17 real sites
python examples/predict_real_sites_grid.py            # -> ml/data/queue_risk_predictions.json, for backend/'s GET /api/demand
python examples/train_rl_agent.py                     # trains + evaluates the DQN charging-guidance agent vs. static/greedy baselines
pytest ml/tests/ -v                                   # 34 tests (11 queue-risk, 23 RL), no Julia/warehouse needed

# Frontend (frontend/)
npm install
npm start            # ng serve, default port 4200 (verified this session on 4300 ad hoc — no
                      # actual conflict found on 4200, just picked defensively; either is fine)

# Backend (backend/) — needs `go` and `duckdb` on PATH
docker compose up -d memgraph   # real graph database backend/internal/graphdb queries live
go mod tidy
go run ./cmd/loadgraph           # loads the real warehouse network into Memgraph (re-run after warehouse changes)
go test -p 1 ./...   # 15 tests: 7 mocked-server copilot-loop + 4 guarded real-data tool (copilot) + 4 graphdb tests
                      # -p 1 required: internal/copilot and internal/graphdb both LoadGraph
                      # (reload) the same live Memgraph -- run in parallel by plain `go test ./...`,
                      # this produces a real transaction conflict, confirmed by reproduction; see
                      # backend/README.md
go run ./cmd/api     # PORT env var if 8080 is taken (it is, on this dev machine — see its README)
curl localhost:8090/api/sites    # real warehouse data
curl localhost:8090/api/demand   # ml/'s precomputed queue-risk predictions (needs predict_real_sites_grid.py run first)
curl -X POST localhost:8090/api/copilot -H "Content-Type: application/json" -d '{"message":"..."}'  # needs ANTHROPIC_API_KEY set

# Copilot support script (Phase 11) — the ml/-backed tool's live model; the resilience tool now
# queries Memgraph live (above) rather than reading a precomputed artifact
cd ml && python examples/predict_one.py <stall_count> <max_power_kw> <temp_c> <utilization>  # live, ~10-20s
```

There is no root-level build/test command yet — each module is independent until the event bus
and API gateway actually wire them together.

## Architecture

Remaining placeholders below (everything past ingestion/warehouse) describe intent from the
design, not implemented behavior — update each as it gains real code:

- `ingestion/tesla/` — **implemented.** `collector.py` drives a real Chrome session (Selenium)
  against Tesla's Find Us pages (no documented public API; see the file's docstring for the
  exact source and parsing method) → dated JSON snapshot in `data/snapshots/tesla/`. Only runs
  from an ordinary residential/desktop network — Tesla's edge blocks cloud/datacenter IPs
  outright, confirmed against this dev sandbox. First real snapshot: 15 US sites, 2026-08-26.
- `ingestion/supplemental/` — **`nrel.py`, `fhwa.py`, `eia.py`, `noaa.py`, `epa.py` (Phase 6), and
  `openei.py`/`nrel_atb.py` (Phase 7) implemented**, `census.py` blocked (see below). All seven
  real public data sources, no browser workaround needed (`fhwa.py`/`epa.py`/`nrel_atb.py` easiest
  — no key at all). Two NREL findings worth
  knowing before touching it: NREL renamed itself the National Laboratory of the Rockies (NLR)
  and retired `developer.nrel.gov` (2026-05-29) for `developer.nlr.gov` — the collector's
  docstring has the full story; and `DEMO_KEY`'s rate limit is 10/hour on this endpoint
  specifically, tighter than NLR's documented 30/hour site default. NREL snapshot: 200 DC-fast
  stations, 12 states, 2026-08-27 (partial — 6,831 matched; get a real key for more). FHWA
  snapshot: **all 994** EV-designated Alternative Fuel Corridor segments nationwide, with real
  geometry, in one request — found via ArcGIS Online's public item search, same "read what the
  live site calls" method as Tesla/NREL, see `fhwa.py`'s docstring. EIA snapshot: real 2024
  commercial electricity price, all 12 states, in one request (`api.eia.gov`, same `api-umbrella`
  gateway + `DEMO_KEY` limits as NLR despite a different domain) — immediately consumed by
  `optimization/` as a real cost term, see below. NOAA snapshot: real current NWS forecast
  (`api.weather.gov`, genuinely keyless), 15 of 17 real Tesla sites (2 hit persistent upstream
  `503`s, real flakiness not a bug), 210 forecast periods — immediately consumed by `simulation/`,
  see below. Two real bugs found and fixed in `noaa.py`: an em-dash in the `User-Agent` broke
  every request (httpx encodes headers ASCII-only by default), and httpx doesn't follow redirects
  by default (unlike `requests`), which NWS's coordinate-canonicalization 301 depends on. EPA
  snapshot: 1,192 real 2023+ EV model-year variants across 35 makes (fueleconomy.gov's bulk CSV,
  same underlying database as its per-vehicle JSON API, confirmed by cross-checking a real vehicle
  id — chose the CSV since it's one request instead of one per model variant) — also immediately
  consumed by `simulation/`, see below. OpenEI snapshot (Phase 7): real per-site commercial
  demand-charge tariffs, 6 of 17 real sites (same `api-umbrella`/`DEMO_KEY` ceiling as NREL/EIA —
  the collector correctly logged, retried, and gracefully skipped the rest once it hit real
  `429`s), found via a real lat/lon lookup against NREL/OpenEI's Utility Rate Database rather than
  a state average (demand charges are genuinely utility-specific) — real rates span
  $4.52-$17.13/kW across real utilities (SCE, PacifiCorp, Progress Energy FL, Appalachian Power,
  Duquesne Light) — immediately consumed by `optimization/` as a real fourth cost term, see below.
  NREL ATB snapshot (Phase 7): real, government-published commercial battery storage cost, all 5
  real durations (1/2/4/6/8hr), from a real ~98.5MB bulk CSV on the public OEDI S3 data lake
  (same NREL→NLR domain rename as `nrel.py`, confirmed at the DNS level: `atb.nrel.gov` no longer
  resolves, `atb.nlr.gov` does) — immediately consumed by `optimization/`'s new battery-sizing
  MILP, see below.
  **`census.py` is blocked, not just unbuilt**: unlike NREL/EIA, the Census ACS API has no
  `DEMO_KEY`-equivalent — it hard-requires a real key (confirmed: every keyless request 302s to
  `missing_key.html`), and getting one means submitting the user's email to an external form,
  which this project won't do without the user doing it themselves. Revisit if the user provides a
  key.
- `warehouse/` — **implemented.** DuckDB file + `spatial` extension; schema in
  `warehouse/schema.sql` is the contract every other module reads against.
  `warehouse/build_warehouse.py` is the only writer — replays `data/snapshots/tesla/*.json` into
  `charging_site`/`charging_site_snapshot`, `data/snapshots/nrel/*.json` into `nrel_station`,
  `data/snapshots/fhwa/*.json` into `fhwa_corridor_segment`, `data/snapshots/eia/*.json` into
  `eia_electricity_price`, `data/snapshots/epa/*.json` into `epa_vehicle`,
  `data/snapshots/openei/*.json` into `grid_demand_charge`, and `data/snapshots/nrel_atb/*.json`
  into `battery_storage_cost` (all five latest-state only, no history table yet), and
  `data/snapshots/noaa/*.json` into `noaa_forecast`
  (insert-only — each snapshot is a point-in-time forecast observation, not a state to overwrite,
  see schema.sql's comment), idempotent, verified against real data for all eight.
  `warehouse/verify/corridor_alignment.py` is a real cross-source check (real Tesla coordinates
  against real FHWA corridor geometry). `warehouse/verify/temperature_scenarios.jl` and
  `warehouse/verify/vehicle_mix_scenarios.jl` extend the same pattern into Julia (real NOAA
  temperatures and real EPA-derived battery sizes, respectively, against `simulation/`'s queueing
  model) — worth reusing as more sources land and can cross-validate each other.
- `optimization/` — **capacity/power-tier selection implemented (Phase 4, extended Phase 6/7).**
  `capacity_selection.jl`: JuMP/HiGHS MILP over the real network, minimizing capex + waiting cost
  (`../simulation`'s M/G/c core, added as a local `Pkg.develop` path dependency) + electricity
  cost (real per-state EIA prices × modeled energy per session — Phase 6) + grid demand-charge
  cost (real per-site OpenEI/URDB $/kW rates × modeled peak coincident demand — Phase 7, reported
  as a fourth separate cost total: $2.53M/year at the real network's natural optimum). Reads the
  warehouse via **DuckDB.jl** (native Julia binding), not the `duckdb` CLI the Go backend uses —
  but does NOT load the `spatial` extension: it segfaults in this environment (binary-compat issue
  between the extension build and DuckDB.jl's bundled `libduckdb.dll`); not needed since this
  model doesn't do location selection. Also fixed a real Windows-specific bug here:
  `DBInterface.close!` doesn't synchronously release DuckDB's file lock (tied to a GC finalizer) —
  `warehouse_io.jl`'s loaders
  now call a shared `close + GC.gc()` helper instead of bare `close!`, confirmed necessary by
  reproduction (chaining two loaders back-to-back in one process threw "file already open"
  without it). `battery_storage.jl` (Phase 7): a second, separate MILP — per-site battery storage
  *sizing* (which real NREL/NLR ATB duration, if any), minimizing real ATB capex/O&M against real
  OpenEI demand-charge savings, reusing `capacity_selection.jl`'s `nameplate_peak_kw` for the
  peak-shave sizing assumption rather than re-deriving it. Real, honest finding: only 1 of 6
  currently-priced real sites (Chino Hills, CA, the highest real demand-charge rate) has positive
  net benefit, and only at a $5M+ budget — battery storage is not a slam dunk under real 2026
  costs, a genuine result, not a demo tuned to show batteries winning everywhere. Sizing only, not
  dispatch — see that file's module docstring for why hour-by-hour scheduling isn't attempted.
  `stochastic_capacity_selection.jl` (Phase 8): a third, separate MILP — two-stage stochastic,
  chance-constrained tier selection over real per-site NOAA weather scenarios (Phase 6 data — every
  real forecast period on file treated as an equally-likely scenario, not an invented distribution)
  instead of a single 20°C point estimate, with a per-site chance constraint on the fraction of
  real scenarios allowed to miss the service target, via an exact big-M MILP reformulation
  (`M = gap` itself). Demand-growth/adoption-rate uncertainty is deliberately NOT modeled — no real
  historical growth-rate series exists to ground it the way real NOAA data grounds weather
  uncertainty. Real finding: this network's real weather variability at 1.3x demand tops out at
  4.96 min of mean wait — nowhere near this project's usual 15-min target, so the demo's chance-
  constraint sweep instead targets the real median (site, scenario) wait it actually produced
  (4.223 min) rather than a guessed constant. At that real target, tightening epsilon below 1.0
  doesn't just raise cost, it makes the $1M-budget portfolio provably `MOI.INFEASIBLE`: some real
  sites' weather variance (e.g. Brandon, FL, 100% of real periods over target) can't be brought
  under a 50% violation rate by any affordable tier — a genuine result that risk tolerance and
  capital budget aren't independent policy levers, not a bug (every site must select exactly one
  tier; a chance-constraint failure is a real planning signal, not silently dropped the way a
  missing-scenario-data site is). 132 tests pass (was 93). Not yet built: greenfield facility
  location (brand-new candidate sites — real FHWA corridor data exists now, Phase 6, but nothing
  here consumes it yet) and network-protection portfolio — one file per problem in this directory
  as those land, same pattern as `capacity_selection.jl`.
- `simulation/` — **implemented (Phase 3), single-site model, extended Phase 12.** Analytical
  M/G/c queueing (`queueing.jl`) plus a ConcurrentSim.jl discrete-event engine (`des.jl`), sharing
  a modeled vehicle charging-curve (`charging_curve.jl`). Validated: the two models agree on three
  real sites (`examples/site_queueing_demo.jl`), 56 tests pass (`test/runtests.jl`). Two Phase 6
  findings sit on top of that demo, both real: `charging_curve.jl` temperature derating ran at a
  hardcoded `temp_c=20.0` everywhere in this codebase until `warehouse/verify/temperature_scenarios.jl`
  fed real NOAA forecast temperatures through it (real coldest period on file: 4.9% longer service
  time; real warmest period: exactly 0% effect, honestly showing the model has no heat-derating
  term). More consequentially, `des.jl`'s `BATTERY_KWH_DIST` was always an arbitrary
  `Uniform(60,100)` demo choice — `warehouse/verify/vehicle_mix_scenarios.jl` substituted a real
  bootstrap-sampled distribution from 1,189 real EPA-derived EV battery sizes (real mean 111.3 kWh
  vs. the arbitrary assumption's 80 kWh), lengthening service time 39.4% and pushing utilization
  from the demo's "safe" 0.80 past 1.0 (unstable) at **all three** real sites tested — meaning the
  demo's headline utilization figure, while correctly computed, rests on an assumption now known
  to understate real battery sizes. Multi-site journeys (drive → SOC decline → charger selection)
  are `routing/`'s job (Phase 9, below), consuming this module's charging-curve and M/G/c core
  rather than duplicating it.
  **`reliability.jl` (Phase 12): component-level failure/repair, layered onto `des.jl`'s engine**
  (one extra `Resource`-competing process per real stall, modeling downtime the same way a very
  long charging session occupies a slot). Calibrated against Tesla's REAL published aggregate
  Supercharger uptime (99.95%, 2024 Impact Report — already flagged in `docs/data-sources.md` as
  a network-wide benchmark only, never disaggregated to a fake per-site figure, a constraint this
  module respects: one uniform `MTBF_HOURS`/`MTTR_HOURS` pair applied identically to every real
  site). `MTTR_HOURS=24h` is a modeled repair-dispatch assumption; `MTBF_HOURS` (~5.5 years) is
  *derived* from it so per-stall availability reproduces the real target exactly. A genuinely
  different resilience question from `graph/`'s Phase 5 topology resilience — component-level
  failure/repair, not whole-site connectivity loss. Real, honest finding: at the real 99.95%
  target, over a real-scale 90-day window, queueing degradation at three real sites is negligible
  (0.0-3.2%) — expected failures per stall in that window are well under one. A clearly-labeled
  modeled what-if sweep below the real target shows the mechanism has real teeth once availability
  degrades (mean wait at an 8-stall real site: +23.6% at 99% availability, +116.7% at 95%), while
  site-level total-outage probability (a separate, closed-form metric, never blended with the
  queueing one) stays vanishingly small throughout (`2.3e-42` at 32 stalls even at 95%) —
  redundancy eliminates catastrophic-outage risk but not the real, separate risk of degraded
  queueing from partial failures. A real formatting bug (Julia's default float-to-string
  conversion ignoring a `sigdigits` rounding request for very small scientific-notation numbers)
  was caught and fixed with explicit `Printf.@sprintf` formatting before this was documented.
- `graph/` — **implemented (Phase 5), Julia AND — as of later work in this session — the real
  Memgraph path too.** Degree/betweenness centrality and a Charging Criticality Index
  (`resilience_graph.jl`), over a geographic-proximity graph built from real coordinates (`geo.jl`
  haversine distance; no real corridor topology exists yet — see its module docstring). **This
  originally deviated from the design** (Cypher against Memgraph, `load.cypher`,
  `../docker-compose.yml`) because Docker's daemon was confirmed hung (`docker ps` never returned)
  when Phase 5 was built — implemented in Graphs.jl instead. Docker started working again in this
  dev environment during later work (confirmed while building the Phase 11 copilot), which
  unblocked the real migration: `backend/internal/graphdb` is a real Cypher-over-Bolt client
  against a real running Memgraph — degree/betweenness computed server-side via Memgraph's MAGE
  library, criticality computed client-side in Go from a Memgraph-sourced edge list — cross-
  validated against THIS Julia implementation's real output for the same real network at the same
  250mi threshold (every sampled value matched exactly: Las Vegas degree=4/betweenness=0.05/
  criticality=6; St. George and Beaver, UT degree=3/betweenness=0.0125; Cle Elum, WA degree=0;
  Charleston, WV degree=1). A real Cypher-dialect bug was found and fixed along the way: Memgraph
  doesn't implement Neo4j's inline `size((s)--())` pattern-comprehension syntax, fixed with a
  standard `OPTIONAL MATCH ... count(...)` aggregation. This Julia implementation stays in place
  (still tested, still the ground truth the Memgraph path was validated against) — 26 tests pass.
  Collected 2 more real sites (St. George UT, Beaver UT) for this phase specifically — the
  original 15-site sample was too scattered to show any real network structure.
- `routing/` — **implemented (Phase 9), a new Julia module (`VolterraRouting`) depending on
  `VolterraGraph`, `VolterraSimulation`, and `VolterraOptimization`** — reusing real site/topology
  loading, the real charging-curve model, and real M/G/c congestion cost rather than a fourth
  independently-invented set of assumptions. `soc_path.jl`: the Electric Vehicle Shortest Path
  Problem (SOC-feasible resource-constrained shortest path), solved via label-setting Dijkstra over
  an augmented `(site, SOC bucket)` state space — real inputs are site coordinates/power, the real
  1,189-vehicle EPA range/consumption distribution (bootstrap-sampled), and the real charging-curve
  model; modeled inputs are a real-world circuity factor (no routing-engine distance exists for
  this network), average highway speed, and SOC safety-margin/target-charge conventions.
  `traffic_assignment.jl`: system-optimal vs. user-equilibrium traffic assignment via Method of
  Successive Averages over real alternate routes (`all_simple_paths`), with real flow-dependent
  congestion cost reusing `mgc_queue`/`service_time_moments` exactly as `optimization/` already
  does — demand volume itself is modeled, Tesla publishes no real trip data. Both demos reuse
  Phase 5's real Las Vegas <-> Nephi, UT corridor rather than inventing an example — 4 real
  alternate routes exist at a 250mi threshold (one more than Phase 5's original description; the
  extra one is a real but inefficient route the traffic assignment correctly leaves at ~zero flow).
  Real findings: every one of 8 real EPA vehicles sampled needed at least one real charging stop
  for the real ~302mi trip, and removing the real intermediate waypoints makes even a 516mi-range
  real vehicle unable to finish it; at low real demand UE and SO are identical, and only past 4
  vehicles/hour does a real, modest price of anarchy appear (1.0006-1.0013). A real bug — Dijkstra
  exploiting a numerical-quadrature difference between one wide charging-time integration and a
  chain of narrower ones to "prefer" several small charging steps at one site over one big one —
  was found and fixed by restricting the search to single-SOC-bucket charge steps (additive by
  construction) and merging consecutive same-site steps for reporting; confirmed fixed against the
  real network. 47 tests pass.
- `ml/` — **queue-risk surrogate implemented (Phase 6).** `volterra_ml/queue_risk.py` trains 4
  separate LightGBM regressors (`mean_wait_min`, `p_wait`, `mean_queue_length`,
  `p_wait_over_15min` — never blended) on `simulation/examples/generate_queue_risk_dataset.jl`'s
  output: a sweep of `simulation/`'s own already-validated M/G/c model (real EPA-derived battery
  sizes, a realistic stall_count/max_power_kw/temp_c/utilization range), NOT fabricated demand —
  read that script's docstring before treating the training labels as anything other than a known
  model's own output, since Tesla publishes no real session-level demand to train against.
  Answers in microseconds what previously needed a Julia process. Held-out R² 0.95-0.9997 on the
  training sweep; R² 0.987-0.9998 on an independent check against all 17 real sites' actual
  configurations (`ml/examples/verify_real_sites.py`) — the surrogate genuinely generalizes to the
  real network, not just its own training distribution. 11 tests pass, self-contained (no Julia/
  warehouse dependency at test time). Two real bugs found and fixed: `polars.to_pandas()` needs
  `pyarrow` (switched to `.to_numpy()` instead of adding that dependency); DuckDB.jl's `Int32`
  INTEGER columns didn't satisfy `mgc_queue`'s `Int64` signature (`Int(...)` conversion at the
  load site). Real demand forecasting and anomaly detection remain unbuilt — real demand
  forecasting specifically needs an observed-demand signal that doesn't exist yet, so it's not
  attempted rather than faked; outputs here are always tagged `modeled`, never presented as
  observed Tesla data.
  **RL charging-guidance agent implemented (Phase 10).** `rl_routing.py`/`rl_env.py`/
  `rl_baselines.py`/`rl_agent.py`: a DQN (`torch`, declared as a dependency since Phase 6
  specifically for this phase) trained on the real Las Vegas <-> Nephi, UT corridor `routing/`'s
  Phase 9 demos already validated, evaluated against two real baselines per this project's
  discipline that RL must be compared, not presented standalone — a **static** plan (Phase 9's
  style: chosen once using only `BASELINE_UTILIZATION`, then walked through each episode's actual
  realized congestion blind to it) and a **greedy** policy (adaptive/observant of realized
  congestion but myopic, no look-ahead, no visited-site memory). Realized congestion is a
  per-episode, per-real-site draw from `Uniform(0.50, 0.95)`, matching `queue_risk.py`'s own real
  validated training domain exactly, reusing that already-validated surrogate for congestion cost
  rather than inventing a third independent congestion model. Real topology/distances/charging-
  curve are ported directly from `graph/`/`routing/`/`simulation/`'s Julia code (same formulas,
  same constants — no Python<->Julia bridge exists in this project and RL training needs
  microsecond-scale queries a live Julia process per step wouldn't give). Real result (500
  held-out episodes, identical congestion draws across all three policies): Static succeeds 100%
  (mean 6.115h). Greedy actually does WORSE than Static despite being adaptive — 98.6% success
  (7/500 episodes truncate, nothing stops it cycling without a look-ahead or visited-site memory)
  and a higher mean (6.517h). DQN modestly beats Static on every measure (100% success, mean
  6.111h) — the only one of the three that's both adaptive AND farsighted, by a small, honestly-
  reported real margin. 23 new tests (11 → 34 total in `ml/`). One test bug caught along the way:
  an early greedy-policy regression test used unequal-stall-count synthetic waypoints, letting a
  real pooling effect outweigh the deliberate congestion gap being tested — fixed with
  equal-capacity, exactly-symmetric-coordinate waypoints so congestion is the only variable.
- `backend/` — **implemented (Phase 2, extended Phase 6, Phase 11).** Go/Fiber API gateway.
  `GET /api/sites` reads the warehouse read-only via the `duckdb` CLI (no CGO driver — see
  `backend/internal/warehouse`) and is verified against real data. `GET /api/demand` (Phase 6)
  reads `ml/`'s precomputed queue-risk predictions from a plain JSON file
  (`backend/internal/mlpredictions`) — deliberately NOT a warehouse query (ml/ model output isn't
  ingestion data, doesn't belong in `volterra.duckdb`) and NOT a live call into Python/LightGBM (no
  cross-language runtime bridge exists yet) — see that package's doc comment.
  `POST /api/copilot` (Phase 11): `backend/internal/copilot` is a raw `net/http` tool-calling loop
  against Claude's Messages API (no third-party SDK — the surface this copilot needs is small and
  stable enough to keep auditable in one file), mirroring AirlinesApp's `api/copilot.py` pattern —
  tiered models (`public`/`researcher`) and the forced-final-answer discipline (`tool_choice`
  **and** `thinking` both set together when `MaxHops` is reached and the model still wants tools —
  see `anthropic.go`'s `Thinking` doc comment for the specific silent-empty-response bug that
  combination prevents, confirmed in AirlinesApp's own production copilot). Four real tools:
  `list_sites`/`site_details` (fast, Go-native warehouse queries), `queue_risk_prediction` (a live
  Python subprocess to `ml/examples/predict_one.py`, ~10-20 real measured seconds),
  `network_resilience` (a live Cypher query against a real, always-on Memgraph —
  `backend/internal/graphdb`, well under 3 real seconds end to end; this originally read a
  precomputed JSON artifact because the equivalent live JULIA subprocess call measured ~53 real
  seconds, but a warm Memgraph service sidesteps that entirely — see `graph/`'s entry below for
  the real migration and its cross-validation). A live capacity-optimization tool was considered
  and dropped for the Julia-subprocess-latency reason and remains a real future upgrade via job
  orchestration over Redpanda (scaffolded since Phase 0, not wired to any Go code yet — a slower
  computation than a graph query, so a warm service alone isn't enough there). 7 tests verify the
  tool-calling loop against a mocked Anthropic API server; 4 guarded tests exercise the real tools
  against real data (warehouse, Memgraph, the trained queue-risk model) — all passed first run.
  **Not verified**: no `ANTHROPIC_API_KEY` is configured in this dev environment, so whether a
  real Claude model picks sensible tools for a real question has not been checked end-to-end —
  documented honestly, not claimed.
- `frontend/` — **LIVE NETWORK + DEMAND layers implemented (Phase 2 + Phase 6).** Angular +
  MapLibre GL; the map is the interface, not a card dashboard. `network-map/` is still the one map
  component — DEMAND is a toggle + utilization slider that re-colors LIVE NETWORK's existing
  markers (not a second marker set) by `GET /api/demand`'s predicted `P(wait > 15min)`, confirming
  the "layers toggle presentation of one shared marker set" design `docs/frontend-notes.md`
  anticipated. CONGESTION / RESILIENCE / GRID / EXPANSION / SIMULATION layers and Planning Mode
  are still ahead — see README's "Signature Interface" section. Verified end-to-end against the
  real running stack (real `GET /api/demand` call, all 17 real sites confirmed joining correctly
  by `site_id` via direct `fetch()` in the live page); the actual per-pixel pin-color paint hit the
  same MapLibre `requestAnimationFrame`/backgrounded-tab gotcha `docs/frontend-notes.md` already
  documented from Phase 2 — not a code bug, don't re-debug it if the map seems inert in an
  unfocused tab.

## Conventions carried over from the sibling AirlinesApp project

- Read-only analytical access to the warehouse is a hard rule: nothing outside `ingestion/`'s
  replay job ever opens DuckDB for writing.
- Bounded queries by default: any query against a table that grows over time (flight-equivalent:
  charging sessions, telemetry) must default to a bounded recent window, not an unscoped full
  history scan — AirlinesApp hit a production memory crash from exactly this mistake.
  Node/query cost matters more here once real session-level data exists.
