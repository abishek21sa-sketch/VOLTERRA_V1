"""
Read-only warehouse access for the routing module, matching the `_close_and_release!` pattern
`optimization/` and `graph/` already established (`DBInterface.close!` alone doesn't
synchronously release DuckDB's Windows file lock — see either of those modules' `warehouse_io.jl`
for the reproduction). Site loading itself is NOT duplicated here — `routing/` depends on
`VolterraGraph` and reuses its `load_sites`/`GeoSite`/`haversine_miles` directly, since there's no
routing-specific reason for a site's real coordinates to be read differently. Only the real EPA
vehicle table (not needed by any existing module) gets its own loader.
"""

using DuckDB
using DBInterface

const DEFAULT_WAREHOUSE_PATH = joinpath(@__DIR__, "..", "..", "warehouse", "volterra.duckdb")

function _close_and_release!(con)
    DBInterface.close!(con)
    GC.gc()
end

struct RealEVSpec
    make::String
    model::String
    year::Int
    range_miles::Float64              # real, EPA-published combined range
    battery_kwh::Float64              # DERIVED by ingestion/supplemental/epa.py (range x consumption), not a raw EPA field
    consumption_kwh_per_mile::Float64 # real, EPA-published combined consumption / 100
end

"""
    load_epa_vehicles(; warehouse_path=DEFAULT_WAREHOUSE_PATH) -> Vector{RealEVSpec}

Reads every real `epa_vehicle` row with a usable real range and consumption figure (Phase 6 —
see `../../ingestion/supplemental/epa.py`, 1,189 real 2023+ EV variants across 35 makes). This is
the real vehicle-population distribution `soc_path.jl` bootstrap-samples from, the same technique
`warehouse/verify/vehicle_mix_scenarios.jl` already validated for `simulation/`'s battery-size
assumption in Phase 6.
"""
function load_epa_vehicles(; warehouse_path::AbstractString=DEFAULT_WAREHOUSE_PATH)
    con = DBInterface.connect(DuckDB.DB, warehouse_path)
    try
        result = DBInterface.execute(con, """
            SELECT make, model, year, range_miles, battery_kwh_estimate, comb_e_kwh_per_100mi
            FROM epa_vehicle
            WHERE range_miles IS NOT NULL AND battery_kwh_estimate IS NOT NULL
              AND comb_e_kwh_per_100mi IS NOT NULL AND comb_e_kwh_per_100mi > 0
        """)
        return [
            RealEVSpec(row.make, row.model, row.year, row.range_miles, row.battery_kwh_estimate,
                       row.comb_e_kwh_per_100mi / 100.0)
            for row in result
        ]
    finally
        _close_and_release!(con)
    end
end
