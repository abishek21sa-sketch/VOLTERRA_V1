## AIRLINES-1.5× DEPTH CANDIDATE

Current release `VOLTERRA_V1_FORTUNE50_AIRLINES15X_RC4` adds a live empirical/historical analysis layer, 26+ substantive workspaces, project-native domain diagnostics, external-source refresh/provenance, and AI decisions grounded in explicit evidence mode. See `docs/AIRLINES_15X_RELEASE.md`.

# Fortune-50 TENX analytical release

**Internal portfolio target:** Math 10/10 · UI 10/10 · AI 10/10, subject to the evidence boundaries below.

- Repository-authored algorithm: **GRIDWEAVE-v1**
- Unique predictive-learning family: **Graph-regularized spatiotemporal demand learning**
- Analytical AI role: **AI Network Planner**
- TENX workspaces: **20**
- Operational authority: **human-gated; autonomous execution blocked**

### Test the TENX layer on Windows

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_tenx_acceptance.ps1
.\scripts\start_tenx_workstation.ps1
```

The first command validates prediction → decision → counterfactual → OR escalation → user-aid behavior and a five-seed originality stress suite. The second opens the dedicated analytical workstation.

> **Evidence boundary:** TENX bundled metrics are synthetic/reference validation, not field deployment validation. Existing native Windows, Julia/Go/Rust/frontend, external-data, clinical, or production gates remain applicable where documented.

---


## Portfolio RC1 — Greenfield network expansion

VOLTERRA now includes a multi-period greenfield charging-network expansion MILP in `optimization/src/network_expansion.jl`. The formulation jointly decides site-opening period, stall additions, corridor assignment, annual capital budgets, grid-interconnection limits, service capacity, demand-weighted uncovered demand, and mandatory horizon coverage for critical corridors. A SciPy/HiGHS reference formulation in `validation/` independently checks key invariants when Julia is unavailable. Native Julia/JuMP execution remains a required release gate.

# VOLTERRA

**EV Charging Network & Grid Decision Intelligence Platform**

VOLTERRA is a charging-network control tower and infrastructure-planning laboratory built over
real, publicly-sourced EV charging infrastructure data. It is not a map and not an invented
composite-score dashboard — it is a set of real operations-research, queueing-theory, network-
science, and grid-optimization tools sitting on top of an empirical backbone reconstructed from
Tesla's public Supercharger network data, combined with authoritative public datasets (NREL,
FHWA, US Census, EIA, NOAA, EPA).

**VOLTERRA is OEM/network-neutral by design.** Tesla is the primary real-world case study because
it currently publishes the richest public station-level dataset, but no part of the schema,
optimizer, or simulator is Tesla-specific. Every field sourced from Tesla, versus modeled/
estimated/sourced elsewhere, is labeled as such — see [`data/README.md`](data/README.md). That
distinction is mandatory, not cosmetic: it's what keeps this a credible infrastructure-planning
tool instead of a "Tesla fan dashboard."

## The core question

Where should EV fast chargers be built, how large should each site be, how should charging
demand be routed and scheduled, and how resilient will the network remain as EV adoption grows?

## Status

**All 12 planned phases working** (Phase 7's battery storage *sizing* — dispatch scheduling is
out of scope; Phase 8's stochastic model covers real weather uncertainty only, not
demand-growth/adoption-rate uncertainty — see below for both). Real Tesla Supercharger data
(`ingestion/tesla/collector.py`, 17 US sites so far) is loaded into `volterra.duckdb`
(`warehouse/build_warehouse.py`) and served by a real Go API (`GET /api/sites`) to a real
Angular + MapLibre GL full-screen map with a click-through site dossier.

- **`simulation/`** implements and validates a real per-site M/G/c queueing model (Erlang-C +
  Allen-Cunneen) against a ConcurrentSim.jl discrete-event simulation. Phase 12 adds
  component-level reliability: per-stall failure/repair calibrated so per-stall availability
  reproduces Tesla's real published 99.95% aggregate uptime (2024 Impact Report) exactly. Real,
  honest finding: at that real target, over a real-scale 90-day window, queueing degradation at
  three real sites is genuinely negligible (0.0-3.2%) — expected failures per stall in that window
  are well under one. A modeled what-if sweep below the real target shows the same mechanism has
  real teeth once availability degrades (mean wait at an 8-stall real site rises 23.6% at 99%
  availability, more than doubles at 95%), while site-level total-outage probability stays
  vanishingly small throughout (down to `2.3e-42` at 32 stalls even at 95%) — redundancy
  eliminates catastrophic outage risk but not the separate, real risk of degraded queueing from
  partial failures, reported as two metrics, never blended.
- **`optimization/`** uses that queueing core as a real cost function inside a JuMP/HiGHS MILP
  that jointly selects stall count and charger power tier per site under a capital budget — run
  against the real network, it produces a genuine efficient frontier and independently reproduces
  the queueing pooling effect with no hand-tuning. Phase 7 added a fourth real cost term: annual
  grid demand charges (real per-site OpenEI/URDB $/kW rates × modeled peak coincident demand) —
  $2.53M/year at the real network's natural optimum, a material cost alongside the existing
  $36.0M electricity and $3.8M waiting-cost totals, reported separately per this project's
  never-blend discipline. A second, separate MILP (`battery_storage.jl`) sizes peak-shaving
  battery storage per site against real NREL/NLR ATB costs — a real, honest finding: only 1 of 6
  currently-priced real sites clears the real economics, and only at a $5M+ budget, not "batteries
  everywhere." Phase 8 adds a third, separate MILP (`stochastic_capacity_selection.jl`): a
  two-stage stochastic, chance-constrained variant sizing tiers against real per-site NOAA weather
  scenarios (Phase 6 data) instead of a single 20°C point estimate. Real finding: this network's
  real weather variability tops out at 4.96 minutes of mean wait at 1.3x demand — nowhere near this
  project's usual 15-min target — but at a target derived from the network's own real median wait,
  tightening the chance-constraint threshold doesn't just raise cost, it can make the $1M-budget
  portfolio provably infeasible (`MOI.INFEASIBLE`, not just costlier): real weather variance at
  some real sites (e.g. Brandon, FL — 100% of its real forecast periods miss the tighter target)
  can't be brought under a 50% violation rate by any affordable tier, a genuine result about risk
  tolerance and capital budget not being independent policy levers.
- **`graph/`** computes real degree/betweenness centrality and a Charging Criticality Index over
  real coordinates, surfacing a genuine bottleneck-vs-hub tradeoff between two real I-15 waypoints
  and Las Vegas depending on the modeled range assumption — in Julia/Graphs.jl rather than the
  originally-planned Memgraph, since Docker was confirmed hung when that phase was built (see
  `graph/README.md`). Docker later started working again, unblocking a real migration:
  `backend/internal/graphdb` now runs the same real analysis as a live Cypher query against a real
  Memgraph instance — server-side centrality via Memgraph's MAGE library, criticality computed
  client-side from a Memgraph-sourced edge list — cross-validated exactly against this Julia
  implementation's real output for the same real network before being trusted.
- **`ingestion/supplemental/`** — seven of eight planned real sources ingested: `nrel.py` (200 real
  DC-fast stations, 12 states — NREL renamed itself and moved its API host mid-development, see
  that module), `fhwa.py` (all 994 real EV-designated Alternative Fuel Corridor segments
  nationwide, cross-validated against Phase 5's resilience graph in
  `warehouse/verify/corridor_alignment.py`), `eia.py` (real per-state electricity prices, now a
  real third cost term in `optimization/`'s MILP — also surfaced a real, reproducible Windows
  DuckDB.jl file-lock bug, fixed in both `optimization/` and `graph/`), `noaa.py` (real current
  NWS forecast for 15/17 real sites, feeding real temperature into `simulation/`'s
  charging-curve model for the first time — real cold derating at the coldest forecast on file
  lengthens service time 4.9%; found and fixed two real bugs: an em-dash silently broke every
  request's ASCII header, and httpx doesn't follow redirects by default), and `epa.py` (1,189 real
  2023+ EV variants — this phase's most consequential finding: substituting a real
  bootstrap-sampled battery-size distribution for `simulation/`'s arbitrary demo assumption
  lengthens service time 39.4% and pushes utilization past 1.0 at all three of Phase 3's real demo
  sites, see `warehouse/verify/vehicle_mix_scenarios.jl`), and `openei.py` (Phase 7 — real
  per-site utility demand-charge tariffs, 6 of 17 real sites via a real lat/lon lookup against
  NREL/OpenEI's Utility Rate Database, real rates spanning $4.52-$17.13/kW across real utilities —
  now a real fourth cost term in `optimization/`'s MILP, $2.53M/year at the network's natural
  optimum), and `nrel_atb.py` (Phase 7 — real, government-published battery storage cost, all 5
  real durations, from NREL/NLR's real bulk technology-cost baseline — feeds the new battery
  sizing MILP above). Census is the one source left genuinely blocked (no `DEMO_KEY`-equivalent; a
  real key needs submitting the user's email to an external form, which this project won't do
  autonomously), not just unbuilt.
- **`ml/`** trains a queue-risk surrogate: four LightGBM regressors approximating
  `simulation/`'s own validated queueing model in microseconds (labels are that model's own
  output, not fabricated demand — Tesla publishes none). Held-out R² 0.95-0.9997 on the training
  sweep, and R² 0.987-0.9998 on a genuinely independent check against all 17 real sites' actual
  configurations (`ml/examples/verify_real_sites.py`). Phase 10 adds a DQN charging-guidance
  agent, trained on the real Las Vegas <-> Nephi, UT corridor and evaluated against two real
  baselines (never presented standalone, per this project's discipline): a static plan (Phase
  9's style, blind to realized congestion) and a greedy policy (adaptive but myopic). Real,
  paired-episode result: greedy actually does *worse* than the static plan (98.6% success, mean
  6.517h, vs. static's 100%/6.115h) since nothing stops it cycling without a look-ahead or
  visited-site memory — while the DQN agent, being both adaptive and farsighted, modestly beats
  the static plan on every measure (100% success, mean 6.111h) — a small, honestly-reported real
  margin, not a dramatized one.
- **`frontend/`** gained its first layer beyond LIVE NETWORK: DEMAND, a toggle + utilization
  slider that re-colors the real site markers by `ml/`'s queue-risk predictions (`backend/`'s new
  `GET /api/demand`, reading a precomputed JSON file — not the warehouse, and not a live
  cross-language model call, see `backend/internal/mlpredictions`). Verified end-to-end against
  the real running stack: real API calls, all 17 real sites joined correctly by `site_id` in the
  live page.
- **`routing/`** (Phase 9) adds two real-network-grounded routing models, layered on the existing
  Julia stack rather than reinventing its machinery: `soc_path.jl` solves the real Electric
  Vehicle Shortest Path Problem (label-setting Dijkstra over a real `(site, SOC bucket)` state
  space) — of 8 real EPA vehicles sampled for the real ~302-mile Las Vegas -> Nephi, UT corridor,
  every one needed at least one real intermediate charging stop, and removing the real
  intermediate waypoints makes even the longest-range real vehicle sampled (516mi) unable to
  finish the trip. `traffic_assignment.jl` computes system-optimal vs. user-equilibrium flow over
  4 real alternate routes (rediscovered programmatically, reusing Phase 5's known corridor
  structure) with real M/G/c congestion cost: at low demand UE and SO are identical, and only past
  4 vehicles/hour does a real, modest price of anarchy appear (1.0006-1.0013). A real bug —
  Dijkstra exploiting a numerical-quadrature artifact in the charging-time integration to "prefer"
  several small charging steps over one big one — was found and fixed during development; see
  `routing/README.md`.
- **`backend/`** (Phase 11) adds `POST /api/copilot`, the Network Operations Copilot: a Go
  tool-calling loop against Claude's Messages API, mirroring AirlinesApp's `api/copilot.py`
  pattern (tiered models, forced-final-answer discipline). Four real tools — two fast Go-native
  warehouse queries, a live Python subprocess call for queue-risk predictions (~10-20s), and a
  live Cypher query against a real, always-on Memgraph for network resilience (well under 3s —
  see `graph/`'s entry above; this originally used a precomputed artifact because the equivalent
  live Julia call measured ~53s, too slow for a synchronous tool call, a problem a warm Memgraph
  service sidesteps entirely). 15 tests pass (7 against a mocked API server, 4 against real data,
  4 more in `internal/graphdb`, 2 self-contained and 2 cross-validated against real Memgraph
  output). Honestly unverified: no `ANTHROPIC_API_KEY` is configured in this dev environment, so
  whether a real Claude model picks sensible tools for a real question hasn't been checked
  end-to-end.

132 tests passing in `optimization/` alone, 261 across the four Julia modules (`simulation/` grew
to 56 with Phase 12's reliability tests, `graph/` 26, `routing/` 47), 34 in `ml/` (11 queue-risk,
23 RL), 15 in `backend/`. That's every phase on the original 12-phase roadmap. Real gaps that
remain, by design rather than oversight, are noted inline above where they apply (Phase 7's
battery dispatch scheduling, Phase 8's demand-growth uncertainty, Phase 11's untested live-model
tool selection) — plus real demand forecasting and additional map layers, which were never
committed to a specific phase. See [`docs/roadmap.md`](docs/roadmap.md) for the full build order
and phase-by-phase detail.

## Repository layout

| Path | Language | Purpose |
|---|---|---|
| [`ingestion/`](ingestion/) | Python | Versioned collectors: Tesla Supercharger network (primary), NREL/FHWA/Census/EIA/NOAA/EPA/OpenEI/NREL-ATB (supplemental). Writes dated snapshots, never overwrites history. |
| [`warehouse/`](warehouse/) | SQL / DuckDB | DuckDB + `spatial` extension analytical warehouse. Canonical schema all other modules read from. |
| [`optimization/`](optimization/) | Julia (JuMP + HiGHS) | Charger-capacity/power selection (working) + battery storage sizing (working) + stochastic/chance-constrained capacity selection (working) — greenfield facility location, network-protection portfolio still ahead. |
| [`simulation/`](simulation/) | Julia (ConcurrentSim.jl) | Discrete-event charging simulator: EV → drive → SOC decline → charger selection → queue → charge → continue. Component-level failure/repair reliability modeling (working). |
| [`graph/`](graph/) | Julia (Graphs.jl) + Cypher/Memgraph | Route + corridor graph, centrality, criticality index, outage/resilience analysis — both the original Julia implementation and the real Memgraph path (`backend/internal/graphdb`) working, cross-validated against each other. |
| [`routing/`](routing/) | Julia (label-setting Dijkstra + MSA) | SOC-feasible resource-constrained shortest path (working) + system-optimal vs. user-equilibrium traffic assignment (working) over the real network. |
| [`ml/`](ml/) | Python (PyTorch, LightGBM, Polars) | Queue-risk surrogate model (working) + DQN charging-guidance agent (working) — real demand forecasting, anomaly detection still ahead. |
| [`backend/`](backend/) | Go (Fiber) | API gateway — `/api/sites` (warehouse), `/api/demand` (`ml/` predictions), `/api/copilot` (tool-calling agent, working). Async job orchestration over Redpanda still ahead. |
| [`frontend/`](frontend/) | Angular + MapLibre GL | Full-screen national network map — LIVE NETWORK + DEMAND layers working; more layers, planning mode still ahead. |
| [`docs/`](docs/) | Markdown | Architecture notes, data source inventory, math formulations, roadmap. |

## Why this stack

Deliberately different from this author's other portfolio projects (Python/FastAPI/Next.js
elsewhere): Julia for the OR/simulation core, Go for the API layer, Angular for the frontend,
Memgraph for the graph engine, Redpanda for the event bus, DuckDB + `spatial` for the analytical
store. Python is kept, scoped specifically to ingestion (Selenium-style collectors) and the ML
stack, where its ecosystem is the actual advantage.

## Data sourcing discipline

- **Primary / real, station-level**: Tesla's public Find Us network (site, address, coordinates
  after geocoding, stall count, rated power, access/compatibility) and Tesla's published
  aggregate benchmarks (uptime, network growth, energy delivered, production/delivery figures).
- **Supplemental / real, contextual**: NREL AFDC (competitor stations, connector standards),
  FHWA (interstate traffic/corridors), US Census (population/commuting/density), EIA (electricity
  prices/grid), NOAA (weather), EPA (vehicle efficiency/range).
- **Modeled / estimated**: anything Tesla does not publish at station granularity (e.g.
  station-level demand, queueing parameters not directly observed) is explicitly labeled as
  modeled or estimated in the schema and in the UI — never presented as an observed Tesla figure.

Full inventory with source URLs and refresh cadence: [`data/README.md`](data/README.md).

## Local development

Each module directory has its own README with its own toolchain instructions once real code
lands there. Cross-cutting local orchestration (Memgraph, Redpanda, the Go API, the warehouse
volume) is defined in [`docker-compose.yml`](docker-compose.yml).

## License / attribution

All third-party data (Tesla, NREL, FHWA, Census, EIA, NOAA, EPA) is used under its respective
public-data terms; see `data/README.md` for per-source attribution and terms links. This is an
independent analytics project and is not affiliated with, endorsed by, or sponsored by Tesla,
Inc.

## Enterprise operability gate

This source release includes a governed decision-assurance layer, negative-path operability tests, hash-verifiable evidence, and a Windows enterprise acceptance gate. See `docs/ENTERPRISE_OPERABILITY.md`.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\\validation\\windows_enterprise_acceptance.ps1
```


## Public Data Backbone
This release contains a structured public-data layer under `data/raw`, `data/processed`, `data/contracts`, `data/dictionaries`, `data/provenance`, and `data/snapshots`. Run `scripts\fetch_public_data_windows.ps1` when the primary public dataset is not bundled, then run `scripts\windows_real_data_acceptance.ps1`. `artifacts/data_backbone_status.json` records source state, row/feature counts, missingness, SHA-256, validation status, case-study state, claim boundary, model version, and the human decision authority.

The public-data case is `Illinois Charging Coverage and N-1 Expansion Study` and is wired into `GRIDWEAVE-v1` review. Missing external raw data never silently falls back to a real-data claim; the dossier explicitly enters `REFERENCE_MODE_HOLD_FOR_REAL_DATA_CLAIM`.
