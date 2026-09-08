# routing

Julia congestion-aware EV routing for VOLTERRA (Phase 9): a resource-constrained shortest path
problem (SOC-feasible routing) and a system-optimal vs. user-equilibrium traffic assignment, both
over the real Tesla Supercharger network.

## Status: working, tested, real data + real congestion machinery reused

```
julia --project=. -e "using Pkg; Pkg.instantiate()"
julia --project=. test/runtests.jl                          # 47 tests
julia --project=. examples/soc_path_demo.jl                 # real network, real EPA vehicle sample
julia --project=. examples/traffic_assignment_demo.jl       # real network, SO vs UE demand sweep
```

- `src/warehouse_io.jl` — real EPA vehicle data (`load_epa_vehicles`, Phase 6 — 1,189 real 2023+ EV
  variants, real `range_miles` and real per-mile consumption). Site loading is reused directly from
  `VolterraGraph`, not duplicated a third time.
- `src/soc_path.jl` — the Electric Vehicle Shortest Path Problem: minimum-time route between two
  real sites that never lets a real vehicle's state of charge run out, charging at real
  intermediate sites (real `VolterraSimulation.charge_duration_hours` charging-curve model) as
  needed. Solved via label-setting Dijkstra over an augmented `(site, SOC bucket)` state space — a
  standard, correct technique for this class of resource-constrained shortest path problem, not a
  heuristic. Also `all_simple_paths`, a small DFS helper for discovering real alternate routes.
- `src/traffic_assignment.jl` — system-optimal vs. user-equilibrium traffic assignment: when
  multiple vehicles share the real network, does everyone independently picking their fastest
  route (user equilibrium) cost the network more in aggregate than a centrally coordinated
  assignment (system optimal)? Real congestion cost reuses `VolterraOptimization.service_time_moments`
  and `VolterraSimulation.mgc_queue` — the same validated Monte Carlo charging-time and M/G/c
  queueing machinery `optimization/` already uses, not a new invented congestion function. Solved
  via Method of Successive Averages (MSA), the standard textbook procedure for Wardrop equilibria.

## What's real, what's modeled

**Real**: every site's coordinates, stall count, and `max_power_kw` (from the warehouse, via
`VolterraGraph`); the real edge topology is Phase 5's resilience graph, already cross-validated
against real FHWA corridor geometry in Phase 6 — not a third, independently-invented graph; the
real 1,189-vehicle EPA distribution's `range_miles` and per-mile consumption (Phase 6); the
charging-curve and M/G/c queueing machinery (Phase 3/4), unchanged from how `optimization/` already
uses them.

**Modeled** (see `../docs/data-sources.md`): `CIRCUITY_FACTOR` (a standard real-world
road-distance-to-great-circle-distance ratio — no real routing-engine distance exists for this
network); `AVG_HIGHWAY_SPEED_MPH`; `ORIGIN_SOC`, `MIN_SOC`, and `CHARGE_TARGET_SOC_MAX` (driver
behavior conventions, the last matching `capacity_selection.jl`'s existing `TARGET_SOC`);
`BASELINE_UTILIZATION` in `traffic_assignment.jl` (mirrors `capacity_selection.jl`'s convention,
duplicated rather than imported since it isn't part of that module's public API); and the EV trip
demand volume itself in the traffic-assignment demo — Tesla publishes no real per-site trip data,
so this is a labeled modeled scenario, the same "not attempted rather than faked" reasoning
`ml/`'s README gives for real demand forecasting.

**Inherited, not new, modeling gap**: `service_time_moments` (reused from `VolterraOptimization`
for real congestion cost) still uses its original arbitrary `Uniform(60,100)` battery-size
assumption, not the real EPA-bootstrap distribution Phase 6's `warehouse/verify/vehicle_mix_scenarios.jl`
showed understates real battery sizes by ~39%. That gap was surfaced, not fixed, in Phase 6 and
is unchanged here — reusing the same function inherits the same known caveat, not a new one.

## Real network structure drove the demo design, not the other way around

Phase 5's resilience analysis (`graph/examples/resilience_demo.jl`) already found that the real
Las Vegas <-> Nephi, UT corridor gains genuine alternate routes once the range threshold widens
from 150mi to 250mi: real direct hops Las Vegas<->Beaver and St.George<->Nephi become viable
alongside the original St.George+Beaver chain. This routing module's demos reuse exactly that real
corridor rather than inventing a separate example — `all_simple_paths` rediscovers those same three
real routes programmatically, and their real, *partially overlapping* intermediate stops (Beaver
appears on two of the three real routes) are what create a genuine congestion externality for the
SO vs. UE comparison to reveal, not a scripted setup.

## SOC-feasible routing: what "resource-constrained" means here

A real vehicle's state of charge is a resource that only decreases while driving and only
increases while charging — the shortest path problem has to respect that constraint, not just
minimize distance or time. `soc_feasible_shortest_path` solves this exactly (for a discretized SOC
resource) via label-setting Dijkstra: every state is `(site, SOC bucket)`, every transition (drive
to a real neighbor, or charge in place) has a non-negative real time cost, so ordinary Dijkstra
correctness guarantees the true minimum-time SOC-feasible path is found, not an approximation.

## System-optimal vs. user-equilibrium: SO = UE under marginal-cost pricing

This is a standard, real result from transportation science (Beckmann et al.; Wardrop's second
principle), not an invented shortcut: the flow pattern that minimizes total system cost is exactly
the flow pattern that would emerge from a Wardrop equilibrium if every driver additionally paid
for the real congestion externality they impose on others, not just their own travel+wait time.
`assign_traffic`'s `marginal_cost` flag switches between the two by swapping which cost function
Method of Successive Averages equilibrates against — same algorithm, same real per-site queueing
cost function, different accounting of who bears the real congestion cost.

## A real bug found and fixed: numerical quadrature, not physics, was picking the path

An early version of `soc_feasible_shortest_path` let a single charging action jump directly from
any SOC bucket to any higher one, computing that jump's time with one `charge_duration_hours`
call. Run against the real network, several real EPA vehicles' optimal paths showed the search
charging at the *same real site* in three or four separate consecutive steps (e.g. `5%->15%;
15%->25%; 25%->35%; 35%->45%`) instead of one `5%->45%` session — because
`charge_duration_hours` numerically integrates with a fixed step count regardless of interval
width, a chain of several narrower integrations over a real interval isn't bit-identical to one
wider integration over the same interval, and Dijkstra was correctly (if uselessly) exploiting
that microsecond-scale quadrature difference as if it were a real timing advantage. Fixed by
restricting the search to single-SOC-bucket charge steps only — a multi-bucket charging session's
total time is now additive *by construction* (there's only one way to compute it: chain the
steps), so the artifact can't recur — and merging consecutive same-site steps back into one
reported `ChargeStop` for output. Confirmed fixed against the real network: every real vehicle
tested now reports at most one `ChargeStop` per real site visited.

## Real findings from both demos

**SOC-feasible routing** (`soc_path_demo.jl`, Las Vegas -> Nephi, UT, the real ~302-mile corridor
Phase 5 already found the real alternate-route structure for): of 8 real EPA vehicles bootstrap-
sampled from the real fleet, every single one needed at least one real intermediate charging stop
— none could make the real ~362-mile modeled driving distance (great-circle x `CIRCUITY_FACTOR`)
on a single real charge. Removing the real intermediate waypoints entirely makes even the
longest-range real vehicle sampled (a Lucid Air, 516mi real EPA range) unable to complete the
trip — confirming the real stops are load-bearing, not decorative, the same "validate by removal"
pattern Phase 5 used for its criticality index.

**System-optimal vs. user-equilibrium** (`traffic_assignment_demo.jl`, same real corridor, 4 real
alternate routes discovered programmatically — one more than Phase 5's original 3-route
description, since `all_simple_paths` also finds a real but geographically inefficient
Las Vegas->Beaver->St.George->Nephi route the assignment correctly leaves at ~zero flow
throughout): at low real demand (0.5-2.0 vehicles/hour), UE and SO are identical — everyone using
the fastest real route creates no real congestion pressure to diverge from it. Only once demand
reaches 4+ vehicles/hour does a real, if modest, price of anarchy appear (1.0006-1.0013): the
system-optimal assignment starts routing a small share of flow onto a real alternate path earlier
than user-equilibrium drivers would choose to on their own, because SO accounts for the real
congestion externality a driver's own choice imposes on others already at a shared real site,
which a self-interested UE driver has no incentive to consider. A small, real, honest number —
not manufactured to look dramatic.
