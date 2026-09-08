"""
Read-only warehouse access, matching optimization/src/warehouse_io.jl's approach: DuckDB.jl
(native Julia binding), no `spatial` extension (crashes here — see
../../optimization/README.md). `latitude`/`longitude` are plain columns on `charging_site`
specifically so this module and optimization/ never need that extension.

Also matches that module's `_close_and_release!` fix: `DBInterface.close!` alone doesn't
synchronously release DuckDB's Windows file lock (it's tied to a finalizer, not the close call) —
confirmed by reproduction in optimization/'s equivalent file when two load functions were called
back-to-back in the same process. Same fix here even though this module doesn't have a second
loader yet, so it doesn't quietly reappear the day one gets added.
"""

using DuckDB
using DBInterface

const DEFAULT_WAREHOUSE_PATH = joinpath(@__DIR__, "..", "..", "warehouse", "volterra.duckdb")

function _close_and_release!(con)
    DBInterface.close!(con)
    GC.gc()
end

struct GeoSite
    site_id::String
    name::String
    city::Union{String,Missing}
    state::Union{String,Missing}
    stall_count::Int
    max_power_kw::Float64
    latitude::Float64
    longitude::Float64
end

"""
    load_sites(; warehouse_path=DEFAULT_WAREHOUSE_PATH) -> Vector{GeoSite}

Reads every row of `charging_site` with real coordinates — real Tesla Supercharger data, see
../../docs/data-sources.md.
"""
function load_sites(; warehouse_path::AbstractString=DEFAULT_WAREHOUSE_PATH)
    con = DBInterface.connect(DuckDB.DB, warehouse_path)
    try
        result = DBInterface.execute(con,
            "SELECT site_id, name, city, state, stall_count, max_power_kw, latitude, longitude FROM charging_site ORDER BY name")
        return [
            GeoSite(row.site_id, row.name, row.city, row.state, row.stall_count, row.max_power_kw,
                    row.latitude, row.longitude)
            for row in result
        ]
    finally
        _close_and_release!(con)
    end
end
