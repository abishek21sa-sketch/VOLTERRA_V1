"""
Read-only warehouse access for optimization models. DuckDB.jl (native Julia binding) is used
instead of shelling out to the `duckdb` CLI, unlike the Go backend — see backend/README.md for
why Go needed the CLI (no C compiler for CGO). Julia's DuckDB.jl ships a precompiled binary via
an artifact, no compiler needed.

**No `spatial` extension here.** Loading it via DuckDB.jl crashed with a hard access violation in
this environment — a binary-compatibility issue between the extension build and DuckDB.jl's
bundled `libduckdb.dll`, not something fixable at the application level. None of the models in
this directory need coordinates yet (capacity/tier selection is per-existing-site, not
location-choice), so `geom` is simply not selected. Revisit if/when a model here needs lat/lon —
either retry the spatial extension against a newer DuckDB.jl, or add plain `latitude`/`longitude`
columns to `charging_site` so spatial functions aren't required for a plain coordinate read.

**`DBInterface.close!` doesn't release the Windows file lock synchronously.** Confirmed by
reproduction: calling `load_sites()` immediately followed by `load_electricity_prices()` in the
same process threw "file already open" from DuckDB — even though each function closes its own
connection before returning. The lock is tied to a finalizer that Julia's GC hasn't necessarily
run yet, not to `close!` itself; inserting `GC.gc()` between the two calls fixed it every time.
Every function here calls `_close_and_release!` instead of bare `DBInterface.close!` for exactly
this reason — this is not a one-off workaround for a single call site.
"""

using DuckDB
using DBInterface

const DEFAULT_WAREHOUSE_PATH = joinpath(@__DIR__, "..", "..", "warehouse", "volterra.duckdb")

"""
    _close_and_release!(con)

`DBInterface.close!(con)` plus a forced GC pass — see this file's module docstring for why the
GC pass is necessary on Windows, not optional cleanup.
"""
function _close_and_release!(con)
    DBInterface.close!(con)
    GC.gc()
end

struct WarehouseSite
    site_id::String
    name::String
    city::Union{String,Missing}
    state::Union{String,Missing}
    stall_count::Int
    max_power_kw::Float64
end

"""
    load_sites(; warehouse_path=DEFAULT_WAREHOUSE_PATH) -> Vector{WarehouseSite}

Reads every row of `charging_site` — real Tesla Supercharger data, see ../../docs/data-sources.md.
"""
function load_sites(; warehouse_path::AbstractString=DEFAULT_WAREHOUSE_PATH)
    con = DBInterface.connect(DuckDB.DB, warehouse_path)
    try
        result = DBInterface.execute(con,
            "SELECT site_id, name, city, state, stall_count, max_power_kw FROM charging_site ORDER BY name")
        return [
            WarehouseSite(row.site_id, row.name, row.city, row.state, row.stall_count, row.max_power_kw)
            for row in result
        ]
    finally
        _close_and_release!(con)
    end
end

"""
    load_electricity_prices(; warehouse_path=DEFAULT_WAREHOUSE_PATH, sector="COM") -> Dict{String,Float64}

Real per-state commercial electricity price in \$/kWh (converted from EIA's published
cents/kWh), keyed by two-letter state code — the most recent year on file per state. See
../../docs/data-sources.md.
"""
function load_electricity_prices(; warehouse_path::AbstractString=DEFAULT_WAREHOUSE_PATH,
                                  sector::AbstractString="COM")
    con = DBInterface.connect(DuckDB.DB, warehouse_path)
    try
        result = DBInterface.execute(con, """
            SELECT state, price_cents_per_kwh
            FROM eia_electricity_price
            WHERE sector_id = ?
            QUALIFY row_number() OVER (PARTITION BY state ORDER BY period_year DESC) = 1
        """, [sector])
        return Dict(row.state => row.price_cents_per_kwh / 100.0 for row in result)
    finally
        _close_and_release!(con)
    end
end

"""
    load_demand_charges(; warehouse_path=DEFAULT_WAREHOUSE_PATH) -> Dict{String,Float64}

Real per-site max commercial demand charge in \$/kW/month (OpenEI/NREL Utility Rate Database,
nearest real tariff to each site's own coordinates — see
../../ingestion/supplemental/openei.py). Keyed by `site_id`, not state — unlike electricity
*price*, demand *charges* are genuinely utility- and tariff-specific, not well approximated by a
state average. Only a subset of real sites have one on file: OpenEI's `DEMO_KEY` is capped at 10
requests/hour (confirmed via `X-Ratelimit-Remaining`, same as NREL/EIA), so a single collector run
against 17 real sites gets real data for as many as the hourly quota allows and gracefully skips
the rest — see that module's docstring. Sites with no real rate are simply absent from this dict;
callers decide how to handle that (see capacity_selection.jl's `candidate_options` for the
fallback this project uses, mirroring `load_electricity_prices`'s same-shaped fallback).
"""
function load_demand_charges(; warehouse_path::AbstractString=DEFAULT_WAREHOUSE_PATH)
    con = DBInterface.connect(DuckDB.DB, warehouse_path)
    try
        result = DBInterface.execute(con,
            "SELECT site_id, max_demand_charge_usd_per_kw FROM grid_demand_charge")
        return Dict(row.site_id => row.max_demand_charge_usd_per_kw for row in result)
    finally
        _close_and_release!(con)
    end
end

struct BatteryStorageCost
    duration_hours::Int
    capex_usd_per_kw::Float64
    fixed_om_usd_per_kw_year::Float64
end

"""
    load_battery_storage_costs(; warehouse_path=DEFAULT_WAREHOUSE_PATH, projection_year=nothing) -> Vector{BatteryStorageCost}

Real, government-published (NREL/NLR Annual Technology Baseline) commercial battery storage cost
per real duration (1/2/4/6/8 hour) — see ../../ingestion/supplemental/nrel_atb.py. Not site- or
Tesla-specific, a national technology-class baseline, same role `load_electricity_prices` plays
for energy cost. `projection_year` defaults to the most recent year on file (there should only
ever be one, from the most recent collector run, but this is robust to future re-runs at a
different year without requiring a warehouse rebuild to drop the old one).
"""
function load_battery_storage_costs(; warehouse_path::AbstractString=DEFAULT_WAREHOUSE_PATH,
                                     projection_year::Union{Int,Nothing}=nothing)
    con = DBInterface.connect(DuckDB.DB, warehouse_path)
    try
        year = projection_year
        if year === nothing
            year = first(DBInterface.execute(con, "SELECT max(projection_year) AS y FROM battery_storage_cost")).y
        end
        result = DBInterface.execute(con, """
            SELECT duration_hours, capex_usd_per_kw, fixed_om_usd_per_kw_year
            FROM battery_storage_cost WHERE projection_year = ?
            ORDER BY duration_hours
        """, [year])
        return [BatteryStorageCost(row.duration_hours, row.capex_usd_per_kw, row.fixed_om_usd_per_kw_year)
                for row in result]
    finally
        _close_and_release!(con)
    end
end

struct TemperatureScenario
    temp_c::Float64
    probability::Float64
end

"""
    load_temperature_scenarios(; warehouse_path=DEFAULT_WAREHOUSE_PATH) -> Dict{String,Vector{TemperatureScenario}}

Real per-site weather scenario sets for Phase 8's stochastic model, built from every real NOAA
forecast period on file per site (`noaa_forecast`, Phase 6 — 14 real periods per site as of this
writing, each site's own real week-ahead forecast) — see
../../ingestion/supplemental/noaa.py. Each real period is treated as one equally-likely scenario
(`probability = 1/n`), a standard sample-average-approximation technique in stochastic
programming applied to real forecast data rather than an invented distribution. Only sites with
real NOAA data are included (currently 15 of 17) — the 2 without one are absent from the returned
dict entirely, not given a fabricated scenario set; callers exclude them the same way
`battery_storage.jl` excludes sites with no real demand-charge rate.
"""
function load_temperature_scenarios(; warehouse_path::AbstractString=DEFAULT_WAREHOUSE_PATH)
    con = DBInterface.connect(DuckDB.DB, warehouse_path)
    try
        result = DBInterface.execute(con, """
            SELECT site_id, (temperature_f - 32.0) * 5.0 / 9.0 AS temp_c
            FROM noaa_forecast ORDER BY site_id, start_time
        """)
        by_site = Dict{String,Vector{Float64}}()
        for row in result
            push!(get!(by_site, row.site_id, Float64[]), row.temp_c)
        end
        return Dict(
            site_id => [TemperatureScenario(t, 1.0 / length(temps)) for t in temps]
            for (site_id, temps) in by_site
        )
    finally
        _close_and_release!(con)
    end
end
