# warehouse

DuckDB + the `spatial` extension is the canonical analytical store. Every other module (Julia
optimization/simulation, Memgraph graph loads, the ML stack, the Go API) reads from the warehouse
file produced here — nothing else is a source of truth.

## Status: working

```
pip install duckdb
python warehouse/build_warehouse.py
```

Applies `schema.sql`, then loads eight real sources: `data/snapshots/tesla/*.json` (each
snapshot's sites go into the immutable `charging_site_snapshot` history, `charging_site` upserted
to latest known state), `data/snapshots/nrel/*.json` (upserted into `nrel_station`),
`data/snapshots/fhwa/*.json` (upserted into `fhwa_corridor_segment`, real corridor geometry),
`data/snapshots/eia/*.json` (upserted into `eia_electricity_price`, real per-state price),
`data/snapshots/noaa/*.json` (inserted into `noaa_forecast` — genuinely insert-only, not upserted,
since each snapshot is a point-in-time forecast observation, not a "latest state"; see
schema.sql's comment), `data/snapshots/epa/*.json` (upserted into `epa_vehicle`, real EV
efficiency/range with a derived battery-kWh estimate), `data/snapshots/openei/*.json`
(upserted into `grid_demand_charge`, real per-site utility demand-charge tariffs, Phase 7), and
`data/snapshots/nrel_atb/*.json` (upserted into `battery_storage_cost`, real government-published
battery storage technology cost per duration, Phase 7).
Idempotent — safe to re-run after a new snapshot lands. Verified end-to-end against the real
network (17 Tesla sites + 200 NREL AFDC stations + 994 FHWA corridor segments + 12 EIA state
prices + 210 NOAA forecast periods across 15 sites + 1,189 EPA EV variants + 6 OpenEI demand
charges + 5 NREL ATB battery-storage duration costs as of 2026-08-27): warehouse builds,
`ST_X(geom)`/`ST_Y(geom)` spatial queries and the
plain `latitude`/`longitude` columns (added for Phase 5) return correct coordinates,
`verify/corridor_alignment.py` confirms real Tesla site coordinates sit near real FHWA corridor
geometry, `verify/temperature_scenarios.jl` confirms real NOAA forecast temperatures produce a
real, measurable effect (4.9% longer service time) when fed into `simulation/`'s queueing model,
`verify/vehicle_mix_scenarios.jl` confirms the real EPA-derived battery-size distribution produces
an even larger effect (39.4% longer service time, unstable queues at all three sites tested), and
`optimization/`'s two MILPs confirm the real OpenEI demand-charge rates produce a real, material
fourth cost term ($2.53M/year at the network's natural optimum) and that real NREL ATB battery
storage costs only pencil out at 1 of 6 currently-priced real sites — see `simulation/README.md`
and `optimization/README.md`.

**`charging_site` carries both `geom` and plain `latitude`/`longitude` columns**, same values,
redundant on purpose: `DuckDB.jl` (the native Julia binding `optimization/` and `graph/` use)
crashes loading the `spatial` extension in this dev environment — a binary-compatibility issue
between the extension build and DuckDB.jl's bundled `libduckdb.dll`, not something fixable at the
application level (see `optimization/README.md`). Anything that only needs coordinates, not real
spatial operations (distance, containment, etc.), reads the plain columns instead.

`volterra.duckdb` itself is gitignored (regenerate it locally with the command above) — only the
schema and build script are checked in.

## Access discipline

Only `build_warehouse.py` ever opens the warehouse file for writing. Every other module opens it
read-only. This mirrors AirlinesApp's `api.db.open_readonly_connection()` convention — see the
root `CLAUDE.md`.

## Cross-source validation

`verify/` holds scripts that check independently-collected real sources agree with each other —
not unit tests, spot-checks of the data itself. `verify/corridor_alignment.py` checks real Tesla
site coordinates against real FHWA corridor geometry. `verify/temperature_scenarios.jl` and
`verify/vehicle_mix_scenarios.jl` check that real NOAA forecast temperatures and real EPA
vehicle-mix data, respectively, actually move `simulation/`'s queueing model's output, not just
that the data loaded — Julia, not Python, since both need `simulation/`'s charging-curve/queueing
code directly rather than reimplementing it. Worth extending as more sources land (e.g. NREL
station density vs. FHWA corridor designation) rather than trusting each source in isolation.

## Provenance is schema, not documentation

Tables that mix real and modeled fields carry a `*_source` column per such field (or a sibling
`field_provenance` JSON column) so a query can filter to "Tesla-observed only" without joining out
to a separate doc. See `schema.sql` and `../docs/data-sources.md`.
