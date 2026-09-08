# VOLTERRA build roadmap

Ordered so each phase produces something runnable/checkable before the next starts. Nothing
here is committed to a date — this is a dependency order, not a schedule.

## Phase 0 — Scaffold (this commit)
Directory structure, per-module starter toolchains, empty warehouse schema, docs. No data, no
features.

## Phase 1 — Empirical backbone — done for a 15-site sample, full run pending
- `ingestion/tesla`: Find Us collector implemented (`collector.py`, Selenium-driven — see its
  README for the network constraint that makes it local-only). First dated snapshot exists:
  `data/snapshots/tesla/2026-08-26.json`, 15 US sites.
- `warehouse/schema.sql`: `charging_site`, `charging_site_snapshot`, `network_operator` tables
  with a `source`/`*_source` provenance column on every field that isn't a raw Tesla observation.
  `build_warehouse.py` replays snapshots into `volterra.duckdb` — verified working.
- Success criterion (met, at 15-site scale): real site count, stall count, and spatial
  (`ST_X`/`ST_Y`) queries all return correct values from DuckDB.
- **Remaining**: run `python -m tesla.collector` for the full ~1000+-site US network from a
  residential network (~3 hours at the mandatory 10s crawl delay) — this session's sandbox can't
  reach Tesla's site at all (see `ingestion/tesla/README.md`), so this step needs the user's own
  machine.

## Phase 2 — Map interface (read-only) — done
- `backend`: `GET /api/sites` implemented, reads `warehouse/volterra.duckdb` via the `duckdb` CLI
  (no CGO driver — see `backend/README.md`), returns real Tesla site data. Verified with `curl`.
- `frontend`: Angular + MapLibre GL full-screen map (`network-map` component), fetches `/api/sites`
  on map load, plots a pin per site sized by stall count, click opens a site dossier panel. Build
  verified; end-to-end pipeline verified (Go API → real JSON → browser `fetch` → marker creation
  all confirmed working). Full visual paint not screenshotted during development — the dev
  session's browser pane runs backgrounded (`document.hidden`), which suspends MapLibre's
  `requestAnimationFrame`-driven render loop; open the pane directly to see it live. No layers yet
  beyond LIVE NETWORK.

## Phase 3 — Queueing + simulation core — done (single-site model)
- `simulation`: M/G/c analytical model (Erlang-C + Allen-Cunneen) and a ConcurrentSim.jl
  discrete-event engine implemented, tested (39 tests), and cross-validated against each other
  on three real sites (6/8/32 stalls) — see `simulation/README.md`. Vehicle charging-curve,
  arrival rate, battery size, and initial SOC are explicit `modeled` assumptions (see
  `docs/data-sources.md`); stall count and charger power are real.
- **Remaining**: this is a *single-site* model — no drive/SOC-decline-between-sites, no charger
  selection among multiple sites, no route-level EV journey yet. That's the Phase 9 routing work
  (resource-constrained shortest path) layered on top; Phase 3's job was the per-site queueing
  core those later phases consume, which now exists and is validated.

## Phase 4 — Capacity/power-tier optimization — done (existing network); greenfield siting deferred
- `optimization`: JuMP/HiGHS MILP implemented, tested (58 tests), and run against the real
  15-site network — see `optimization/README.md`. Jointly selects stall count + power tier per
  site (or "no change"), minimizing capex + annualized waiting cost (from Phase 3's M/G/c
  queueing core, not a proxy), subject to budget. A budget sweep demonstrates a real efficient
  frontier and correctly reproduces the queueing pooling effect with no hand-tuning.
- **Scope note**: this optimizes capacity/tiers at the *existing* real network, not brand-new
  candidate locations — true facility *location* choice (the brief's original framing) needs
  candidate sites placed against real highway/traffic/population data, which doesn't exist until
  Phase 6 (FHWA/Census). Fabricating arbitrary candidate coordinates now would break this
  project's real-data-first discipline; greenfield siting is deferred to build on Phase 6, not
  skipped.
- Frontend Planning Mode UI (budget/horizon/service-level inputs → OPTIMIZE NETWORK → proposed
  plan) is still ahead — the optimizer it would call now exists and returns real results.

## Phase 5 — Network resilience — done (Julia implementation; Memgraph deferred)
- `graph`: degree centrality, betweenness centrality, and a Charging Criticality Index
  (connectivity loss from removing each site) implemented, tested (26 tests), and run against
  the real network — see `graph/README.md`. **Not Memgraph/Cypher as originally planned** —
  Docker's daemon was confirmed hung when this was built (`docker ps` never returned even after
  killing and retrying); implemented in Julia/Graphs.jl instead, a dependency already declared
  elsewhere in this repo. `graph/load.cypher` remains the intended production target.
- Collected 2 more real sites (St. George UT, Beaver UT — real I-15 waypoints) specifically
  because the original 15-site sample was too geographically scattered to show any real
  corridor structure. With them, real coordinates alone produce a genuine finding: at a tight
  150mi range assumption those two sites are the critical bottleneck between Las Vegas and Utah;
  at 250mi the bottleneck dissolves and Las Vegas becomes the critical hub instead. Neither
  outcome was engineered — both fall out of real betweenness/criticality math once a real
  corridor sample exists, which is exactly why the extra 2 sites were worth collecting.
- Schema change: `charging_site` now carries plain `latitude`/`longitude` columns alongside
  `geom`, since DuckDB.jl (used by both `optimization/` and `graph/`) can't load the `spatial`
  extension in this environment (see `optimization/README.md`).
- **Remaining**: Frontend RESILIENCE layer (not built).
- **Update (during Phase 12/later-work session)**: the real Memgraph/Cypher path is done.
  Docker started working again in this dev environment (confirmed via `docker ps` while building
  the Phase 11 copilot) — `backend/internal/graphdb` is now a real Cypher-over-Bolt client against
  a real running Memgraph, exactly matching `graph/load.cypher`'s original target shape: real
  degree/betweenness centrality computed server-side via Memgraph's bundled MAGE library, and the
  Charging Criticality Index computed client-side in Go from a Memgraph-sourced edge list (MAGE
  has no single built-in procedure for that metric, and this Julia implementation's own version is
  itself a custom algorithm on a connected-components primitive, not a single library call
  either). **Cross-validated, not just run and trusted**: `go test ./internal/graphdb` loads the
  real warehouse network into a real Memgraph and checks every sampled value against this Julia
  implementation's real output for the exact same real network at the same 250mi threshold — Las
  Vegas degree=4/betweenness=0.05/criticality=6, St. George and Beaver UT degree=3/betweenness=
  0.0125, Cle Elum WA degree=0, Charleston WV degree=1 — all matched exactly. A real Cypher-dialect
  bug was found and fixed: Memgraph doesn't implement Neo4j's inline `size((s)--())` pattern-
  comprehension syntax (`Not yet implemented: atom expression '(s)--()'`), fixed with a standard
  `OPTIONAL MATCH (s)--(neighbor) ... count(neighbor)` aggregation instead. `backend/cmd/loadgraph`
  is the real loader (warehouse → Memgraph); the copilot's `network_resilience` tool (Phase 11) now
  queries this live instead of a precomputed JSON artifact — see that phase's entry below for why
  the swap matters (a live Julia call for the same data measured ~53s, too slow for a synchronous
  tool call; a warm Memgraph service sidesteps that, answering in well under 3s). This Julia
  implementation stays in place as the tested reference the Memgraph path was validated against,
  not deleted now that a second real implementation exists. A benign infra issue was hit and fixed
  along the way: `docker compose up -d memgraph` initially failed with a port-3000 conflict against
  a sibling project's container on this dev machine (confirmed via `docker ps`, not a Docker
  reliability problem) — fixed by remapping Memgraph Lab's host port to 3001 in `docker-compose.yml`.

## Phase 6 — Supplemental data + demand/queue forecasting — nearly done (Census blocked, anomaly detection remains)
- `ingestion/supplemental/nrel.py`: **done.** Real public REST API (no browser workaround needed,
  unlike Tesla) — see its README for two non-obvious findings: NREL was renamed the National
  Laboratory of the Rockies (NLR), so the documented `developer.nrel.gov` hostname is retired in
  favor of `developer.nlr.gov` (found by reading the still-AFDC-branded site's own JS bundle);
  and `DEMO_KEY`'s rate limit is tighter on this endpoint (10/hour) than NLR's documented 30/hour
  site default. First real snapshot: 200 DC-fast stations across VOLTERRA's 12 Tesla-network
  states (partial — 6,831 matched the query; `DEMO_KEY` didn't allow pulling the rest). Loaded
  into a new `nrel_station` warehouse table, verified with a real cross-table query. Confirms
  Tesla is one of many networks AFDC tracks (31 of the 200 sampled stations) — exactly the
  OEM-neutral role this source is meant to play.
- `ingestion/supplemental/fhwa.py`: **done.** Real public ArcGIS Feature Service (official
  USDOT/BTS Alternative Fuel Corridors, part of the National Transportation Atlas Database) — no
  API key, no rate limit hit, easier than both Tesla and NREL. Found the same way those two were:
  by reading what the live FHWA/ArcGIS pages actually call, not trusting the docs — see its
  README. Pulled **all 994** EV-designated corridor segments nationwide, with real
  `LineString`/`MultiLineString` geometry, in a single request. This is real corridor *topology*
  (which highways are officially designated for EV travel) — a different, not-yet-ingested FHWA
  dataset covers traffic *volume* (AADT/HPMS).
  - **Cross-validated against Phase 5**: `warehouse/verify/corridor_alignment.py` checks real
    Tesla site coordinates against the real I-15 corridor geometry — 3 of 4 sites land within ~3
    miles of the designated centerline (Beaver, UT: 0.14 mi), confirming Phase 5's
    geographic-proximity proxy graph was a reasonable stand-in in that specific case. One site
    (Nephi) comes out ~21 mi off — a real, undismissed gap in this dataset's segment coverage
    near Nephi despite I-15 running through the actual town, documented rather than hidden.
- `ingestion/supplemental/eia.py`: **done.** Real public REST API (api.eia.gov, "Electricity
  Retail Sales"), same `api-umbrella` gateway signature and same `DEMO_KEY` (10/hour on this
  endpoint) as NREL, despite a different domain — see its README. Pulled real commercial
  electricity retail price for all 12 states in **one request** (multi-value facets, no
  per-state loop needed). Loaded into a new `eia_electricity_price` warehouse table.
  - **Used immediately, not just stored**: `optimization/src/capacity_selection.jl`'s MILP
    previously had no electricity/operating cost term at all (capex + waiting cost only). Now
    adds a real annualized electricity cost (real EIA price × modeled per-session energy
    consumption) as a third objective component, reported separately per this project's
    never-blend-metrics discipline. See `optimization/README.md`.
  - **Found a real, reproducible bug along the way**: calling `load_sites()` immediately followed
    by `load_electricity_prices()` in the same Julia process threw a DuckDB "file already open"
    error on Windows — `DBInterface.close!` doesn't synchronously release the file lock, which is
    tied to a GC finalizer. Fixed with a shared `_close_and_release!` helper (close + `GC.gc()`)
    in both `optimization/` and `graph/`'s warehouse-access code, not a one-off workaround.
- `ingestion/supplemental/noaa.py`: **done.** Real public NWS API (`api.weather.gov`), genuinely
  keyless — no account/token needed at all, unlike NOAA's other API (Climate Data Online, which
  does require one; tried that first). Live current forecast, not a historical climate archive.
  Two real, non-obvious bugs found and fixed during development — see its README: an em-dash in
  the `User-Agent` string broke every request (httpx encodes headers as ASCII by default), and
  httpx doesn't follow redirects by default (`requests` does) so the mandatory
  `/points/{lat},{lon}` → canonicalized-coordinate 301 broke every request until
  `follow_redirects=True` was added. First real snapshot: current forecast for 15 of 17 real Tesla
  sites (2 hit persistent upstream `503`s that exhausted the retry budget — confirmed real NWS
  flakiness via the warehouse, not a collector bug), 210 forecast periods, loaded into a new
  `noaa_forecast` warehouse table (insert-only, not upsert — see `schema.sql`'s comment on why).
  - **Used immediately, not just stored**: `simulation/src/charging_curve.jl`'s
    `temperature_derate`/`charge_duration_hours` existed since Phase 3 but had never once been
    called with a real temperature anywhere in this codebase — every prior run used the hardcoded
    `temp_c=20.0` default. `warehouse/verify/temperature_scenarios.jl` scans every real forecast
    period on file (not just each site's "today" reading — run in August, that would show zero
    effect as a seasonal accident, since every site's current daytime temp is above the model's
    10°C cold-derate floor) and compares the coldest and warmest real periods against the 20°C
    baseline. At the real coldest period on file (Cle Elum, WA, "Saturday Night", 45°F/7.2°C),
    real cold derating lengthens service time 4.9% and mean queue wait from 1.59 to 2.6 minutes
    (utilization 0.800 → 0.839). The real warmest period (Las Vegas, 109°F/42.8°C) shows exactly
    0% effect — an honest finding, not a bug: the charging-curve model only derates for cold
    battery temperatures, it has no heat-derating term, so this is a real, surfaced gap in the
    model rather than a hidden one.
- `ingestion/supplemental/epa.py`: **done.** Real public bulk data — fueleconomy.gov's full
  vehicle CSV (EPA/DOE joint), ~22MB, no key, single request. Also explored and confirmed real
  fueleconomy.gov's nested per-model JSON API (`/ws/rest/vehicle/{id}`), then chose the CSV
  instead since it's the same underlying database (cross-checked one vehicle id against both) in
  one request rather than one per model variant. Filtered to battery-electric, model year 2023+:
  1,192 real EV variants across 35 makes (1,189 distinct after 3 benign duplicate-key collapses —
  see `schema.sql`'s comment). EPA doesn't publish battery-kWh directly, so `battery_kwh_estimate`
  is DERIVED (range × combined consumption) and tagged with a distinct source string, not
  conflated with directly-published fields.
  - **Used immediately, not just stored — and the most consequential Phase 6 finding**:
    `warehouse/verify/vehicle_mix_scenarios.jl` replaces `simulation/`'s arbitrary
    `Uniform(60,100)` battery-size assumption (never grounded in real data) with bootstrap
    sampling from the real 1,189-vehicle distribution, at the same three real sites Phase 3's demo
    used. Real mean battery size (111.3 kWh) is 39% larger than the arbitrary assumption's mean
    (80 kWh); substituting it lengthens service time 39.4% and pushes utilization from the "safe"
    0.80 the demo was tuned to, past 1.0 — an unstable queue — at **all three** sites. This means
    every previously reported "0.80 utilization" figure in this project's Phase 3/4 work was
    correctly computed under its stated assumption, but that assumption is now known to
    understate real battery sizes in today's EV market. See `simulation/README.md`.
- **Census blocked, not just unbuilt**: the ACS API has no `DEMO_KEY`-equivalent — every keyless
  request 302s to `missing_key.html` (confirmed by testing a real reverse-geocoded tract query;
  the reverse-geocoding half, `geocoding.geo.census.gov`, is itself keyless and does work). A real
  key requires submitting the user's email to an external signup form, which this project won't do
  autonomously — revisit if the user provides a key.
- `ml/` **queue-risk surrogate: done.** `simulation/examples/generate_queue_risk_dataset.jl`
  sweeps `simulation/`'s own already-validated M/G/c model (6,000 rows: realistic
  stall_count/max_power_kw/temp_c/utilization sweep, real EPA-derived battery sizes) to produce a
  training set — the labels are the queueing model's own output, not fabricated demand (Tesla
  publishes none; see that script's docstring for why this is a legitimate, honestly-labeled
  approach, not a workaround). `ml/volterra_ml/queue_risk.py` trains 4 separate LightGBM
  regressors (`mean_wait_min`, `p_wait`, `mean_queue_length`, `p_wait_over_15min` — reported
  separately, per this project's never-blend discipline) that answer the same query in
  microseconds instead of a Julia process. Held-out R² 0.95-0.9997 on the random sweep split, and
  — the real check — R² 0.987-0.9998 against a genuinely independent holdout of all 17 real Tesla
  sites' actual (stall_count, max_power_kw) at the real coldest/warmest NOAA temperatures on file
  (`ml/examples/verify_real_sites.py`), confirming the surrogate generalizes to the real network,
  not just the synthetic sweep it trained on. 11 tests pass (self-contained synthetic data, no
  Julia/warehouse dependency). Found and fixed two real bugs along the way: `polars`'
  `.to_pandas()` requires `pyarrow` (switched to `.to_numpy()`, sidestepping an unneeded
  dependency entirely); DuckDB.jl's `Int32` INTEGER columns didn't match `mgc_queue`'s `Int64`
  signature (`MethodError`, fixed with an explicit `Int(...)` conversion at the load site).
  Real, actual demand forecasting (not this queue-risk proxy) remains unbuilt — it would need a
  real observed-demand signal Tesla has never published; not attempted rather than faked.
- **Frontend DEMAND layer: done.** `ml/examples/predict_real_sites_grid.py` precomputes
  `queue_risk.py`'s predictions for all 17 real sites × the same 10-level utilization grid — each
  site's grid uses its real current NOAA forecast temperature where one exists (15 of 17; the
  other 2 fall back to an assumed 20°C, explicitly tagged, never blurred with a real reading).
  `backend/internal/mlpredictions` reads that as a plain JSON file (`GET /api/demand`) — not a
  warehouse query (ml/ output isn't ingestion data) and not a live Python/LightGBM call (no
  cross-language runtime bridge exists yet) — see that package's doc comment for both constraints.
  `frontend/`'s `network-map` gained a DEMAND toggle + utilization slider that re-colors the same
  LIVE NETWORK markers (not a separate marker set) by predicted `P(wait > 15min)`. Verified
  end-to-end against the real running stack, not just compiled: `go build`, `ng build`, a live
  `GET /api/demand` (200 OK) from the actual toggled UI, and all 17 real sites confirmed joining
  correctly by `site_id` via a direct fetch in the running page — the literal per-pixel pin-color
  paint hit the same backgrounded-tab MapLibre rendering gotcha `docs/frontend-notes.md` already
  documented from Phase 2, so that specific check relied on the DOM/network verification instead
  of a screenshot, same as Phase 2's original map paint. Also found and fixed a real bug along the
  way: LightGBM regressors have no output-range constraint, so a real trained model predicted a
  tiny negative `p_wait_over_15min` near utilization=0.50 — fixed by clamping every target to its
  physical bounds in `predict_queue_risk`, with evaluation left unclamped so it still reports
  honest raw error.
- **Remaining**: `ml/` anomaly detection. FHWA landing unlocks real greenfield facility-location
  siting (Phase 4's scope note) and a real corridor-topology graph for `graph/` to use instead of
  Phase 5's geographic-proximity proxy — neither built yet, this phase only ingested and
  spot-validated the data those need.

## Phase 7 — Grid-aware optimization + battery storage — done (sizing; dispatch out of scope)
- `ingestion/supplemental/openei.py`: **done.** Real public REST API — NREL/OpenEI's U.S. Utility
  Rate Database (URDB), the canonical real archive of actual published utility tariffs. Same
  `api-umbrella`/`DEMO_KEY` (10 req/hour) story as NREL/EIA. Unlike EIA's state-level average,
  this queries each real site's own lat/lon directly (`sector=Commercial`, 20mi radius) since
  demand charges are genuinely utility/tariff-specific, not well approximated by a state average —
  confirmed via a real example: the real Chino Hills, CA site resolves to a real Southern
  California Edison "TOU-GS-3" tariff with real on/off-peak demand rates ($11.24-$17.13/kW/month).
  First real snapshot: 6 of 17 real sites (`DEMO_KEY`'s hourly ceiling — the collector correctly
  logged, retried, and gracefully skipped the remaining 11 once it hit real `429`s mid-run). Real
  rates found span a real ~4x spread across real utilities ($4.52-$17.13/kW): PacifiCorp,
  Progress Energy Florida, Appalachian Power, Southern California Edison, Duquesne Light.
  - **Used immediately, not just stored**: a new real cost term,
    `optimization/src/capacity_selection.jl`'s `annualized_demand_charge_usd` — real $/kW rate ×
    modeled peak coincident demand (`PEAK_DEMAND_COINCIDENCE_FACTOR = 0.7`, since the
    charging-curve taper means not every occupied stall draws rated power simultaneously).
    Reported as a **fourth separate cost total** (capex, waiting cost, electricity cost, demand
    charge — never blended), same discipline as every other Phase 4/6 cost term. At the real
    network's natural optimum ($1M budget, 1.3x demand growth), the real budget sweep shows
    $2.53M/year total demand-charge cost — a real, material cost component (versus $36.0M
    electricity, $3.8M waiting cost at that same point), confirming demand charges are a genuine
    grid-cost concern for this kind of facility, not a rounding error. 75 tests pass (was 66),
    including new tests confirming demand charge scales with the real rate and is independent of
    arrival rate (unlike energy/waiting cost, which scale with throughput) — a real, meaningful
    behavioral distinction the separate-reporting discipline exists to surface.
  - **A real data-quality caveat found and documented, not hidden**: the 20-mile radius search
    occasionally returns a real but not obviously EV-relevant Commercial rate (e.g. a real
    "School Service" tariff near the Charleston, WV site) — URDB has no EV-charging-specific
    sector tag, so this is the nearest real approved Commercial rate, not a perfect match.
- `ingestion/supplemental/nrel_atb.py`: **done.** Real public bulk data — NREL's Annual
  Technology Baseline (ATB), the real, government-published, annually-updated U.S. baseline for
  electricity generation/storage technology costs. Same NREL-to-NLR rename story as `nrel.py`,
  confirmed at the DNS level (`atb.nrel.gov` no longer resolves, `atb.nlr.gov` does). Real ~98.5MB
  bulk CSV on the public OEDI S3 data lake, keyless, single request. Filtered to "Commercial
  Battery Storage" across all 5 real published durations (1/2/4/6/8hr), current-year (2026)
  projection: $1,207-$2,754/kW real CAPEX depending on duration, confirmed `CAPEX = OCC + CFC`
  exactly across every row.
  - **Used immediately, not just stored — and the phase's real, non-obvious finding**: a new
    `optimization/src/battery_storage.jl` MILP sizes a peak-shaving battery per real site (real
    ATB capex/O&M vs. real OpenEI demand-charge savings), reusing `capacity_selection.jl`'s
    `nameplate_peak_kw` for the peak-shave sizing assumption rather than re-deriving it (both
    models share one definition, not two independently-invented ones). Real result: of the 6 real
    sites with a real demand-charge rate, only **one** (Chino Hills, CA, the highest real rate at
    $17.13/kW) has positive net annual benefit, and only at a $5M+ budget — a 2,800kW/1hr battery,
    $3.38M capex, $575,568/year in real savings against $81,407/year O&M and $225,225/year
    amortized capex (15-year modeled lifetime), netting $268,936/year (~13-year payback). The
    other 5 real sites' lower real rates ($4.52-$7.25/kW) never clear the real cost of a battery
    at any duration — an honest "batteries aren't a slam dunk" finding, not a demo tuned to show
    them winning everywhere. Sites without a real demand-charge rate are excluded from
    consideration entirely (not fallback-priced, unlike `capacity_selection.jl`'s cost-total
    fallback) since a discrete build/no-build decision shouldn't rest on a fabricated rate. 18 new
    tests (75 → 93 total in `optimization/`).
  - **Sizing only, not dispatch**: real hour-by-hour battery operation scheduling would need
    either real interval load data (Tesla publishes none) or a new modeled synthetic hourly
    load-shape assumption significant enough to deserve its own scope — not attempted here rather
    than bolted on as an afterthought, same "not attempted rather than faked" reasoning as `ml/`'s
    real demand-forecasting gap.
- **Remaining**: frontend GRID layer (not started, would follow the same real-JSON-artifact
  pattern Phase 6's DEMAND layer established).

## Phase 8 — Stochastic optimization — done (weather uncertainty; demand/adoption-rate deferred)
- `optimization/src/stochastic_capacity_selection.jl`: **done.** A two-stage stochastic,
  chance-constrained variant of Phase 4's capacity/tier MILP — same tier menu, capex,
  electricity- and demand-charge logic, but waiting cost is now an EXPECTATION over real per-site
  NOAA weather scenarios (`load_temperature_scenarios`, reusing Phase 6's real forecast data —
  every real period on file treated as one equally-likely scenario, a standard sample-average
  approximation applied to real data, not an invented distribution) instead of a single 20°C point
  estimate. A per-site chance constraint caps the probability-weighted fraction of real scenarios
  allowed to miss the service target, implemented via an exact big-M reformulation (`M = gap`
  itself, not an arbitrary constant — a binary `z[i,s]` forced to 1 only when a selected candidate's
  real scenario wait exceeds target). A tier only qualifies as a candidate if queueing-stable at
  *every* real scenario, not just the baseline. 39 new tests (93 → 132 in `optimization/`, 158 →
  197 across the three Julia modules).
  - **Demand-growth and adoption-rate uncertainty — also named in this phase's original scope —
    are NOT modeled.** VOLTERRA has no real historical growth-rate time series to ground a demand-
    uncertainty distribution the way real NOAA data grounds weather uncertainty (Tesla publishes
    point-in-time delivery figures, not the multi-year series a real growth-rate *distribution*
    would need); not attempted rather than faked. Weather is the uncertain dimension this project
    can back with real data today.
  - **Real finding #1 — the usual 15-min service target never binds on real weather.** Run against
    the real network at 1.3x demand growth (the same scenario Phase 4's demo uses), the highest
    real per-scenario mean wait observed anywhere across all 15 real NOAA-covered sites at their
    budget-optimum tiers is **4.96 minutes** — nowhere close to this project's usual 15-min target.
    Sweeping the chance-constraint threshold (epsilon) against that target is flat: every real
    weather scenario always complies regardless of risk tolerance. An honest result (this network's
    real recorded weather swings genuinely aren't severe enough to threaten a generous target at
    this demand), not a bug — but not demonstrative of the chance constraint's mechanics either, so
    the demo's epsilon sweep instead targets the real median (site, scenario) wait the model itself
    produced at the budget optimum (4.223 min) — a target derived from real model output, not a
    constant picked to force an interesting result.
  - **Real finding #2 — tightening epsilon can make the whole network provably infeasible, not
    just costlier.** At that real, tighter target: epsilon=1.0 (no real constraint) solves optimal,
    with at least one real site (Brandon, FL — literally all 14 of its real forecast periods
    produce a wait above target) showing a 100% real violation rate. But every epsilon from 0.5
    down to 0.01 makes the $1M-budget portfolio **`MOI.INFEASIBLE`** — no combination of affordable
    tier upgrades across the real 15-site network can bring every real site's violation rate at or
    under 50% simultaneously. This falls directly out of the model requiring every site to select
    exactly one tier (a chance-constraint failure is a real planning signal, not silently dropped
    the way a missing-scenario-data site is) and is a genuinely useful real-world result: past some
    point, risk tolerance and capital budget aren't independent policy levers an operator can set
    separately — tightening one can force loosening the other, or the plan is provably unbuildable,
    and this MILP reports that honestly instead of silently returning something.
  - Backward-compatibility note: extending `capacity_selection.jl`'s `service_time_moments` to
    accept a `temp_c` parameter (needed to price each real scenario's derated service time) had to
    preserve the exact Monte Carlo draws — and therefore the exact dollar figures already published
    from Phase 4/6/7 work — for every existing call site using the default temperature; only
    genuinely new (power_kw, temp_c) combinations get a new deterministic seed derived from the
    pair. Confirmed via `grep` that no other call site was affected.

## Phase 9 — Congestion-aware routing — done
- `routing/`: **done.** A new Julia module (`VolterraRouting`), depending on `VolterraGraph`
  (real site/topology reuse), `VolterraSimulation` (real charging-curve model), and
  `VolterraOptimization` (real M/G/c congestion cost via `service_time_moments`) — the project's
  established layered-reuse pattern, not a fourth independently-invented set of assumptions.
  - `soc_path.jl`: the Electric Vehicle Shortest Path Problem — minimum-time route between two
    real sites that never lets a real vehicle's state of charge run out, charging at real
    intermediate sites as needed, solved via label-setting Dijkstra over an augmented
    `(site, SOC bucket)` state space. Real inputs: site coordinates/power (warehouse), the real
    1,189-vehicle EPA range/consumption distribution (Phase 6, bootstrap-sampled), the real
    charging-curve model (Phase 3). Modeled: a real-world circuity factor converting great-circle
    to driving distance (no real routing-engine distance exists for this network), average highway
    speed, and SOC safety-margin/target-charge conventions.
  - `traffic_assignment.jl`: system-optimal vs. user-equilibrium traffic assignment via Method of
    Successive Averages, over real alternate routes (`all_simple_paths`) with real, flow-dependent
    congestion cost (reusing `VolterraSimulation.mgc_queue` and `VolterraOptimization.service_time_moments`
    exactly as `optimization/` already does — not a new invented congestion function). Demand
    volume itself is modeled — Tesla publishes no real EV-trip data.
  - **Real network structure drove the demo design**: Phase 5's resilience analysis already found
    that the real Las Vegas <-> Nephi, UT corridor gains alternate routes once the range threshold
    widens from 150mi to 250mi. This phase's demos reuse exactly that real corridor —
    `all_simple_paths` rediscovers 4 real routes programmatically (one more than Phase 5's
    original 3-route description: a real but geographically inefficient
    Las Vegas->Beaver->St.George->Nephi path that the traffic assignment correctly leaves at
    ~zero flow) — rather than inventing a separate example.
  - **Real finding #1 (SOC routing)**: of 8 real EPA vehicles bootstrap-sampled for the real
    ~302-mile Las Vegas -> Nephi trip, every one needed at least one real intermediate charging
    stop — none could complete the real ~362-mile modeled driving distance on a single charge.
    Removing the real intermediate waypoints makes even the longest-range real vehicle sampled
    (a Lucid Air, 516mi real EPA range) unable to make the trip, confirming the real stops are
    load-bearing, the same validate-by-removal pattern Phase 5 used for its criticality index.
  - **Real finding #2 (SO vs UE)**: at low real demand (0.5-2.0 vehicles/hour) on this corridor,
    user-equilibrium and system-optimal assignment are identical — no congestion pressure to
    diverge from the fastest real route. Only above 4 vehicles/hour does a real, modest price of
    anarchy appear (1.0006-1.0013): system-optimal starts shifting a small share of flow onto a
    real alternate route earlier than self-interested drivers would, because it accounts for the
    real congestion externality imposed on others at a shared real site. A small, honest, real
    number — this project's real network and modeled demand scenario don't manufacture a dramatic
    price of anarchy, and the finding is reported as computed, not tuned to look bigger.
  - **A real bug found and fixed during development**: an early version of the SOC search let a
    single charging action jump directly to any target SOC in one step; because
    `charge_duration_hours` numerically integrates with a fixed step count regardless of interval
    width, a chain of several narrower real integrations wasn't bit-identical to one wider one
    over the same real interval, and Dijkstra exploited that microsecond-scale quadrature
    difference — visible in real output as several separate charging stops at the same real site
    instead of one real session. Fixed by restricting the search to single-SOC-bucket charge
    steps (making a session's total time additive by construction, not by luck) and merging
    consecutive same-site steps for reporting. Confirmed fixed against the real network.
  - 47 tests pass (self-contained synthetic topology fixtures — a simple chain and a real,
    haversine-verified diamond — plus a guarded real-warehouse integration testset).

## Phase 10 — RL charging-guidance agent — done
- `ml/volterra_ml/rl_routing.py`, `rl_env.py`, `rl_baselines.py`, `rl_agent.py`: **done.** A DQN
  (`torch`, a dependency declared in `ml/pyproject.toml` specifically for this phase since Phase
  6) trained on the real Las Vegas <-> Nephi, UT corridor Phase 9's demos already validated,
  evaluated against two real baselines the roadmap requires (not presented standalone): a
  **static** plan (Phase 9's style of planning, chosen once using only this project's
  `BASELINE_UTILIZATION`, then walked through each episode's actual realized congestion blind to
  it) and a **greedy** policy (adaptive/observant of realized congestion at candidate next stops,
  but myopic with no look-ahead and no visited-site memory).
  - **Real data grounding, no invented congestion model**: realized congestion is a per-episode,
    per-real-site draw from `Uniform(0.50, 0.95)`, matching `queue_risk.py`'s own real, already-
    validated LightGBM surrogate training domain exactly (`generate_queue_risk_dataset.jl`'s
    `UTILIZATION_RANGE`) — every congestion query the RL environment makes stays inside the
    surrogate's confirmed-accurate range, reusing real, tested infrastructure rather than a third
    independent congestion model (after the Julia M/G/c model and its LightGBM surrogate). Real
    site topology, real distances, and the real charging-curve model are ported directly from
    `graph/`, `routing/`, and `simulation/`'s Julia code (same formulas, same constants) since no
    Python<->Julia bridge exists in this project and RL training needs microsecond-scale queries a
    live Julia process per step wouldn't give.
  - **Why RL isn't trying to "beat" a provably optimal algorithm**: Phase 9's SOC-feasible shortest
    path is exact for a static, fully-known network, but it commits to a route once, blind to
    today's realized conditions at any specific real site. This phase's real research question is
    whether reacting to real-time information (something Phase 9's static planner structurally
    can't use) beats committing blind to it, and whether a farsighted learned policy beats a
    myopic reactive one — not whether RL can out-optimize an exact algorithm at its own game.
  - **Real result** (`examples/train_rl_agent.py`, 500 held-out episodes, identical congestion
    draws across all three policies for a fair paired comparison): Static succeeds 100% of the
    time (mean 6.115h, p90 6.277h). Greedy actually does WORSE than Static despite being adaptive
    — 98.6% success (7 of 500 episodes truncate before reaching the destination, since nothing
    stops greedy from cycling between real sites without a look-ahead or a visited-site memory)
    and a higher mean (6.517h) and p90 (6.759h). DQN modestly beats Static on both counts (mean
    6.111h, p90 6.249h) while matching its 100% success rate. Honest finding: adaptivity alone
    (greedy) isn't enough and can actively hurt — a policy needs to be both adaptive AND
    farsighted to reliably beat a well-chosen static plan, and DQN (the only one of the three that
    is both) is the only one that does, by a modest, honestly-reported real margin, not a
    dramatized one.
  - 23 new tests (11 → 34 total in `ml/`): self-contained synthetic topology fixtures (mirroring
    `routing/test/runtests.jl`'s chain/diamond pattern in Python) plus a guarded real-warehouse
    integration test. One test bug caught and fixed along the way: an early version of the greedy-
    policy regression test used two synthetic waypoints with different real stall counts, so a
    real queueing pooling effect (more stalls -> shorter real queue) could outweigh the deliberate
    congestion gap being tested regardless of which site was actually "busier" — fixed by using
    equal-capacity waypoints so realized congestion is the only thing that differs, and separately,
    by using exactly-symmetric (same-longitude) coordinates so a ~2-real-mile drive-distance
    asymmetry from imprecise "symmetric" coordinates couldn't swamp the intentionally small
    congestion signal being tested.

## Phase 11 — Network Operations Copilot — done (loop verified, live model calls untested)
- `backend/internal/copilot`: **done.** A Go/Fiber-hosted tool-calling loop against Claude's
  Messages API, mirroring AirlinesApp's `api/copilot.py` pattern (this project's own sibling
  portfolio project) — tiered models (`public`/`researcher`, same tools and system prompt, only
  the model differs) and the forced-final-answer discipline (force a final answer with
  `tool_choice={"type":"none"}` **and** `thinking={"type":"disabled"}` together when `MaxHops` is
  reached but the model still wants tools — see `anthropic.go`'s `Thinking` doc comment for the
  specific silent-empty-response failure mode AirlinesApp's own production copilot found that
  combination prevents). `POST /api/copilot` wired up in `main.go`.
  - **Four real tools, deliberately not more** (`tools.go`'s `NewTools`): `list_sites` and
    `site_details` are fast, Go-native real warehouse queries (no subprocess). `queue_risk_prediction`
    is a live Python subprocess call to a new `ml/examples/predict_one.py` script (roughly 10-20
    real measured seconds, variable — tolerable for an interactive tool call).
    `network_resilience` reads a new precomputed JSON artifact
    (`graph/data/resilience.json`, from a new `graph/examples/export_resilience_json.jl` script)
    rather than a live Julia call.
  - **A real architectural finding drove that split, not a guess**: direct measurement during this
    phase found a warm one-shot `julia` subprocess (packages already precompiled from this
    session's earlier phases) still takes **~53 real seconds** end to end — Julia's own
    startup/JIT warmup, not the computation, which is fast — far too slow for a synchronous HTTP
    tool call. A live, on-demand capacity-optimization tool was considered and dropped for the
    same reason. Both remain real future upgrades via job orchestration over Redpanda (scaffolded
    in `docker-compose.yml` since Phase 0, never wired to any Go code before this phase, and still
    not wired now) — deferred with a documented reason, not silently abandoned, the same
    discipline Phase 5's Memgraph deviation used.
  - **Docker note**: `docker ps` now returns cleanly in this dev environment, unlike when Phase 5
    was built ("confirmed hung" at the time). This phase didn't act on that (migrating to
    Memgraph or standing up a real Redpanda worker pipeline is real, separate work, out of this
    phase's scope), but it's worth knowing the constraint that shaped Phase 5's Julia/Graphs.jl
    substitution may no longer hold.
  - **What has and hasn't been verified**: 7 tests verify the tool-calling loop logic against an
    `httptest` server playing back canned Anthropic API response shapes (including that the
    forced-final-answer request really does set `tool_choice` and `thinking` together — confirmed
    by inspecting the captured request body, not assumed), plus 4 guarded tests exercising the
    real tools against the real warehouse, the real precomputed resilience artifact, and the real
    trained queue-risk model — all passed on first run against real data. A real running server
    correctly returns a clear `ANTHROPIC_API_KEY not configured` error rather than crashing when
    the key is absent (verified via `curl` against a real `go run ./cmd/api` instance, alongside
    `/api/sites` and `/api/demand` still working). What's genuinely NOT verified: whether a real
    Claude model picks sensible tools for a real natural-language question about this network —
    this dev environment has no `ANTHROPIC_API_KEY` configured, so no live call to the real API
    was made. Documented honestly rather than claimed, the same as Census's blocked API key or
    Tesla's full-network collector needing the user's own residential network.
  - **Toolchain note**: both the Go and DuckDB CLI toolchains turned out to already be installed
    in this dev environment, just not on this session's shell `PATH` (found via `winget list` and
    a `WinGet\Packages` directory search) — a different discovery path than `backend/README.md`'s
    existing note about a prior session's portable-zip Go install, updated to reflect both.
  - **Update (during Phase 12/later-work session)**: the Docker opportunity this phase flagged but
    didn't act on has since been taken. `network_resilience` now queries a real, live Memgraph
    instance (`backend/internal/graphdb`) instead of reading the precomputed
    `graph/data/resilience.json` artifact described above — that artifact and the script that
    generated it (`graph/examples/export_resilience_json.jl`) have been retired. See Phase 5's
    entry above for the real migration, its cross-validation against `graph/`'s Julia
    implementation, and the real Cypher-dialect bug found and fixed along the way. The real
    architectural finding that drove this phase's original design (Julia subprocess startup is too
    slow for a synchronous tool call) still holds and still explains why a live capacity-
    optimization tool remains a real future upgrade via Redpanda rather than something a warm
    service alone can fix — Memgraph's fix works specifically because a graph query is cheap once
    warm, not because the general Julia-latency problem went away.

## Phase 12 — Reliability engineering — done
- `simulation/src/reliability.jl`: **done.** Per-stall failure/repair modeled as a continuous-time
  up/down process, layered directly onto `des.jl`'s existing ConcurrentSim.jl engine (one extra
  `Resource`-competing process per real stall, representing downtime the same way a very long
  charging session would occupy a slot — a standard, tractable DES technique, not a new
  capacity-tracking abstraction) — reused, not duplicated. Calibrated against Tesla's REAL
  published aggregate Supercharger uptime (99.95%, 2024 Impact Report — already flagged in
  `docs/data-sources.md` as a network-wide benchmark only, never disaggregated to a fake per-site
  figure, a constraint this module respects: `MTBF_HOURS`/`MTTR_HOURS` are ONE uniform pair
  applied identically to every real site, not derived per-site from data that doesn't exist).
  `MTTR_HOURS` (24h) is a modeled real-world-plausible repair-dispatch assumption; `MTBF_HOURS` is
  *derived* from it (`mtbf_for_target_availability`, inverting the standard two-state reliability
  formula `A = MTBF/(MTBF+MTTR)`) so per-stall availability reproduces the real 99.95% target
  exactly, rather than guessing a failure rate directly.
  - **A genuinely different resilience question from Phase 5**: `graph/`'s network-topology
    resilience asks what happens if an entire SITE goes offline and disconnects the network.
    This asks what happens to QUEUEING PERFORMANCE at a site whose individual stalls fail and get
    repaired — `queueing.jl` and `des.jl` both otherwise assume all `c` stalls are always
    available. Redundancy is the connecting idea across both: more stalls means more resilience
    to both mechanisms, for different reasons.
  - **Two real, separately-reported metrics, never blended**: `site_full_outage_probability`
    (closed-form, `(1-A)^stall_count` under a stated independent-failure simplification — real
    correlated failures, e.g. a shared grid outage, would make this an underestimate, not an
    overestimate) and empirical DES queueing degradation (`run_site_simulation_with_failures` vs.
    the no-failure baseline).
  - **Real, honest finding at the real calibration**: run against the same three real sites
    `site_queueing_demo.jl` (Phase 3) uses, over a real-scale 90-day simulated window, queueing
    degradation at Tesla's real 99.95% target is genuinely negligible (0.0-3.2% across the three
    real sites) — expected failures per real stall in that window are well under one
    (MTBF ~5.5 years, derived from the real target), so this is an honest result, not a modeling
    failure: Tesla's real reported reliability doesn't meaningfully stress real queueing at
    realistic timescales.
  - **A second, clearly-labeled sensitivity finding, not presented as real**: to show the same
    mechanism has real teeth (rather than leaving ambiguous whether "no effect" means "nothing
    happens at any scale" or "the mechanism doesn't work"), the demo also sweeps MODELED what-if
    availability levels below the real target (MTTR held fixed at the same 24h assumption, MTBF
    re-derived at each level): at 99% availability, mean wait at Charleston, WV (8 real stalls)
    rises 23.6%; at 95%, it more than doubles (+116.7%). Site-level total-outage probability stays
    separately vanishingly small throughout this whole sweep (down to `1.6e-8` at 6 stalls and
    `2.3e-42` at 32 stalls even at the 95% what-if level) — redundancy makes total site outage a
    non-concern regardless of availability level in this sweep, but does not eliminate the real,
    separate risk of degraded queueing from partial failures.
  - A real formatting bug was caught and fixed before this was documented: Julia's default
    float-to-string conversion doesn't respect a `sigdigits` rounding request for very small
    numbers in scientific notation (prints the full round-trip-exact decimal instead, e.g.
    `2.3299999999999997e-106` instead of a clean `2.33e-106`), which also silently ran two printed
    table columns together with no visible separator. Fixed with `Printf.@sprintf("%.3g", ...)`
    for explicit, correctly-truncated scientific-notation formatting.
  - 17 new tests (56 total in `simulation/`, up from 39): the availability/MTBF round-trip
    calibration, `site_full_outage_probability` sanity and monotonicity checks, and two
    `run_site_simulation_with_failures` tests — one confirming an aggressive failure regime
    measurably degrades service versus the no-failure baseline, one confirming a near-infinite
    MTBF reproduces the no-failure baseline closely — both passed on first run.
