# optimization

Julia/JuMP models for the OR core. Solver: HiGHS.

## Status: working, tested, real data + real cost functions

```
julia --project=. -e "using Pkg; Pkg.instantiate()"
julia --project=. test/runtests.jl                             # 132 tests
julia --project=. examples/capacity_portfolio_demo.jl           # real network, budget sweep
julia --project=. examples/battery_storage_demo.jl              # real network, battery sizing budget sweep
julia --project=. examples/stochastic_capacity_selection_demo.jl  # real network, chance-constrained sizing
```

- `src/warehouse_io.jl` — reads real site data, real per-state EIA electricity prices, and real
  per-site OpenEI/URDB demand charges (Phase 7) via **DuckDB.jl** (Julia's native binding, not the
  `duckdb` CLI subprocess the Go backend needs — Julia's FFI doesn't require a C compiler the way
  CGO does). Deliberately doesn't load the `spatial` extension: it segfaults
  (`EXCEPTION_ACCESS_VIOLATION`) inside DuckDB.jl in this environment — a binary-compatibility
  issue between the extension build and DuckDB.jl's bundled `libduckdb.dll`, not fixable at the
  application level. Not needed yet anyway: no model here does location selection, only
  capacity/tier selection at existing (real) coordinates.
  **Also documents a real, reproducible Windows-specific bug**: `DBInterface.close!(con)` does
  not synchronously release DuckDB's file lock — calling `load_sites()` immediately followed by
  `load_electricity_prices()` in the same process threw "file already open" even though each
  function closes its own connection. The lock is tied to a finalizer the GC hadn't run yet;
  every loader here calls a shared `_close_and_release!` (close + `GC.gc()`) instead of bare
  `close!` because of this — not a one-off workaround for a single call site.
- `src/capacity_selection.jl` — the capacity + power-tier selection MILP. For each real site,
  choose a stall count and charger power from a discrete menu (or keep it unchanged), minimizing
  capital cost, plus the annualized cost of customer waiting time (`../simulation`'s real M/G/c
  queueing model, not a proxy), plus the annualized cost of the electricity itself (real per-state
  EIA prices × modeled energy consumption per session), plus the annualized grid demand charge
  (real per-site OpenEI/URDB $/kW rate × modeled peak coincident demand, Phase 7) — subject to a
  capital budget.
- `src/battery_storage.jl` — the battery storage *sizing* MILP (Phase 7). For each real site with
  a real demand-charge rate on file, decide whether a peak-shaving battery is worth installing,
  and at which real NREL/NLR ATB duration (1/2/4/6/8hr), minimizing net annualized cost (real ATB
  capex/O&M minus real demand-charge savings) subject to a capital budget. Sizing only — real
  hour-by-hour dispatch scheduling isn't built, see that file's module docstring for why (same
  "not attempted rather than faked" reasoning `ml/`'s README gives for real demand forecasting).
- `src/stochastic_capacity_selection.jl` — a two-stage stochastic, chance-constrained variant of
  `capacity_selection.jl` (Phase 8). Same tier/capex/electricity/demand-charge logic, but waiting
  cost is now an EXPECTATION over real per-site NOAA weather scenarios (`load_temperature_scenarios`,
  Phase 6 data — every real forecast period on file treated as one equally-likely scenario, a
  sample-average approximation over real data, not an invented distribution) instead of a single
  20°C point estimate, and a per-site chance constraint caps the probability-weighted fraction of
  real scenarios allowed to miss the service target. A tier only qualifies as a candidate if
  queueing-stable at *every* real scenario, not just the baseline. Implemented as an exact big-M
  MILP reformulation (`M = gap` itself, not an arbitrary constant). Demand-growth/adoption-rate
  uncertainty is NOT modeled — VOLTERRA has no real historical growth-rate series to ground it
  the way real NOAA data grounds weather uncertainty; see that file's module docstring.

## What's real, what's modeled

**Real**: every site's `stall_count`, `max_power_kw`, and `state` (live from the warehouse); each
site's state's commercial electricity price (`load_electricity_prices`, real EIA data); each real
site's own commercial demand-charge rate where OpenEI/URDB has one on file
(`load_demand_charges`, Phase 7 — 6 of 17 real sites as of this writing, `DEMO_KEY` limited; see
`../ingestion/supplemental/openei.py`); real commercial battery storage capex/O&M per real
duration (`load_battery_storage_costs`, Phase 7 — NREL/NLR ATB, a government-published national
baseline, not a per-vendor quote; see `../ingestion/supplemental/nrel_atb.py`); each real site's
own set of real NOAA/NWS weather forecast periods (`load_temperature_scenarios`, Phase 8 — 15 of
17 real sites, 14 real periods each; see `../ingestion/supplemental/noaa.py`).

**Modeled** (see `../docs/data-sources.md` — none of this is Tesla-published): the discrete tier
menu and per-stall/power-upgrade capital costs (industry-order-of-magnitude, not Tesla capex
figures), the demand-growth scenario (today's utilization is assumed, not observed — Tesla
doesn't publish per-site utilization), the dollar value of customer waiting time, the energy
consumed per charging session (from the charging-curve model — the *quantity* of kWh is modeled,
the *price* per kWh is real), the peak coincident-demand fraction used to convert a tier's
nameplate capacity into a billable peak kW (`PEAK_DEMAND_COINCIDENCE_FACTOR = 0.7` — the real
demand-charge *rate* is real, but VOLTERRA has no real per-site measurement of actual simultaneous
peak draw), the fallback demand charge used for the 11 real sites OpenEI's `DEMO_KEY` ceiling
hasn't reached yet (mean of the known real rates, same fallback shape as the electricity-price one
— logged with a warning every time it fires, not silently substituted), and `battery_storage.jl`'s
`BATTERY_LIFETIME_YEARS = 15` (a standard grid-storage planning assumption used to annualize the
real ATB capex figure, not fit to a specific product). Sites with no real demand-charge rate are
excluded from battery sizing entirely rather than falling back — see that file's module docstring
for why a discrete build/no-build decision shouldn't be priced off a fabricated rate the way a
continuous cost *total* safely can be. `stochastic_capacity_selection.jl`'s
`EPSILON_DEFAULT = 0.20` (Phase 8) is likewise a modeled policy choice, not derived from data —
what fraction of real weather scenarios a site is allowed to miss the service target in, not
itself an uncertain quantity.

## A design choice worth explaining: the service-level cap is a report, not a filter

An earlier version of this model excluded any tier that failed a mean-wait service-level cap
from even being a candidate. That makes per-site compliance mandatory-or-infeasible, which
defeats the purpose of a budget constraint — the optimizer can never produce "the best network
we can afford," only "can we afford full compliance everywhere, yes or no." `candidate_options`
now only excludes genuinely unstable tiers (utilization ≥ 1); `optimize_capacity_portfolio`
reports which *selected* sites fall short of the cap in `below_service_target_sites`, separately
from cost — same "never blend metrics into one score" discipline as the rest of this project
(now four separate cost totals: capex, waiting cost, electricity cost, demand charge).

The demo's budget sweep shows the result: a real efficient frontier, from $0 (no upgrades, high
aggregate waiting cost) up through binding budget levels to a natural optimum where further
budget stops helping. It also reproduces real queueing-theory behavior with no hand-tuning: the
optimizer prioritizes upgrading the *smallest* sites first, because the Erlang-C pooling effect
(validated in Phase 3) makes small-`c` sites disproportionately worse at the same utilization.

## Why demand charge is a separate cost term, not folded into electricity cost

Energy cost (`annualized_electricity_cost_usd`) and demand-charge cost
(`annualized_demand_charge_usd`) both come from a utility bill, but they respond to completely
different things: energy cost scales with *throughput* (kWh delivered, which scales with arrival
rate), demand charge scales with *nameplate capacity* (kW available, independent of how busy the
site actually is). A nearly-idle 40-stall site and a fully-booked 40-stall site pay the same
demand charge under this model's peak-coincidence assumption but very different energy costs —
collapsing them into one "electricity cost" number would hide that a bigger tier can be a real
demand-charge liability even when it barely gets used, exactly the kind of real-world grid-cost
surprise this phase exists to surface.

## Battery storage: a real, honest finding, not a foregone conclusion

Running `battery_storage_demo.jl` against real 2026 NREL/NLR ATB battery costs and real
OpenEI/URDB demand-charge rates does **not** produce "batteries make sense everywhere" — the
economics are genuinely marginal. Of the 6 real sites with a real demand-charge rate on file,
only **one** (Chino Hills, CA — the highest real rate found, $17.13/kW from Southern California
Edison) has a positive net annual benefit, and only once the swept budget reaches $5M: a 1-hour,
2,800kW battery costing $3.38M upfront, saving $575,568/year in demand charges against $81,407/year
in O&M and $225,225/year in amortized capex (15-year modeled lifetime) — a net benefit of
$268,936/year, roughly a 13-year simple payback on the real capex. The other 5 real sites' lower
real demand-charge rates ($4.52-$7.25/kW) never clear the real cost of a battery at any duration
in this sweep. This is exactly the kind of honest result this project's real-data-first discipline
is supposed to produce — a plausible, defensible techno-economic finding, not a demo tuned to show
batteries winning everywhere.

## Stochastic optimization: two real findings, one expected and one sharper than expected

Running `stochastic_capacity_selection_demo.jl` against the real network (1.3x demand growth, the
same scenario the Phase 4 demo uses) produces two real, verified findings:

**This project's usual 15-minute service target never binds on real weather data.** Across all 15
real NOAA-covered sites at their real budget-optimum tiers, the highest real per-scenario mean
wait observed anywhere is **4.96 minutes** — nowhere close to 15. Sweeping epsilon against the
usual 15-min target is therefore flat: every real weather scenario always complies, regardless of
risk tolerance. This is itself an honest finding (this network's real recorded weather swings
aren't severe enough to threaten a generous target at this demand), not a bug — but it isn't
demonstrative of the chance constraint's actual mechanics, so the demo's epsilon sweep instead
targets the **real median (site, scenario) wait** observed at the budget optimum (4.223 min, as of
this writing) — a target derived from what the model produced on real data, not a constant chosen
to force an interesting-looking result.

**At that real, tighter target, tightening epsilon doesn't just raise cost — it can make the whole
network infeasible.** At epsilon=1.0 (no real constraint), the model is optimal, and at least one
real site (e.g. Brandon, FL — every one of its 14 real forecast periods produces a wait above the
4.223-min target) shows a 100% real violation rate. But at every epsilon from 0.5 down to 0.01, the
$1M-budget portfolio is **provably infeasible**, not merely costlier: no combination of affordable
tier upgrades across the real network can bring every real site's violation rate at or under 50%
simultaneously. This falls directly out of the model's structure — every site must select exactly
one tier (unlike a missing-scenario-data site, a site with no tier that clears its chance
constraint isn't silently dropped, since a discrete risk-policy failure is a real planning signal,
not something to hide) — and is a genuinely useful real-world result: past some point, risk
tolerance and capital budget aren't independent policy levers a network operator can set on their
own. Tightening one can require loosening the other, or the plan is provably unbuildable, and this
MILP reports that honestly (`MOI.INFEASIBLE`) instead of silently returning something.

## Greenfield facility location is deferred, not skipped

The project brief's original facility-location formulation (choosing brand-new geographic sites,
not just resizing existing ones) needs candidate locations placed somewhere defensible — highway
corridors, traffic volumes, population density. Real FHWA corridor data now exists (Phase 6,
`fhwa_corridor_segment` — see `../graph/README.md` for how it's already been used to validate
Phase 5's resilience graph), but nothing here consumes it yet for candidate-site placement. This
phase scoped down to capacity/tier selection over the *existing* real network instead. True
greenfield siting is `docs/roadmap.md`'s later work — the blocker now is building it, not missing
data.
