"""
Phase 6 EPA integration: a real EV battery-size distribution feeding the queueing/charging-curve
models, in place of the arbitrary `Uniform(60.0, 100.0)` demo assumption every prior run in this
codebase used (see ../../simulation/examples/site_queueing_demo.jl's `BATTERY_KWH_DIST`).

Loads real `battery_kwh_estimate` values from `epa_vehicle` (../../ingestion/supplemental/epa.py
— derived from real EPA range/efficiency figures for 1,192 real 2023+ EV model-year variants
across 35 real makes) and bootstrap-samples from that real empirical set instead of drawing from
a parametric Uniform(60,100) shape. Reruns the same three real sites and target utilization
Phase 3's `site_queueing_demo.jl` used, holding temperature at the same 20°C baseline as that demo
(this script isolates the vehicle-mix effect specifically — see `temperature_scenarios.jl` for the
temperature effect) so the comparison isolates what the real vehicle mix changes.

Run from warehouse/verify/, against optimization/'s Project.toml (already declares DuckDB.jl and a
VolterraSimulation path dependency):
    julia --project=../../optimization vehicle_mix_scenarios.jl

Real: site_id, stall_count, max_power_kw (Tesla); battery_kwh_estimate (EPA, derived from real
range/efficiency — see epa.py's module docstring for why it's derived rather than a raw EPA
field). Modeled: arrival rate, initial/target SOC — same assumptions as site_queueing_demo.jl.
"""

using DuckDB
using DBInterface
using VolterraSimulation
using Distributions
using Random
using Statistics

const WAREHOUSE_PATH = joinpath(@__DIR__, "..", "volterra.duckdb")

const REAL_SITE_NAMES = [
    "Cranberry Township, PA Supercharger",
    "Charleston, WV Supercharger",
    "Santa Clarita, CA Supercharger",
]

const ARBITRARY_BATTERY_KWH_DIST = Uniform(60.0, 100.0)  # site_queueing_demo.jl's original demo choice
const INITIAL_SOC_DIST = Truncated(Normal(0.30, 0.12), 0.02, 0.55)
const TARGET_SOC = 0.80
const TARGET_UTILIZATION = 0.80
const TEMP_C = 20.0  # held fixed at Phase 3's baseline -- this script isolates the vehicle-mix effect

struct RealSite
    name::String
    city::Union{String,Missing}
    state::Union{String,Missing}
    stall_count::Int
    max_power_kw::Float64
end

function _query(sql::String, params=[])
    con = DBInterface.connect(DuckDB.DB, WAREHOUSE_PATH)
    try
        return DBInterface.execute(con, sql, params) |> collect
    finally
        # See optimization/src/warehouse_io.jl's module docstring: GC.gc() is required after
        # close! to actually release DuckDB's Windows file lock.
        DBInterface.close!(con)
        GC.gc()
    end
end

function load_real_sites()
    rows = _query("""
        SELECT name, city, state, stall_count, max_power_kw FROM charging_site
        WHERE name IN (?, ?, ?)
    """, REAL_SITE_NAMES)
    by_name = Dict(row.name => RealSite(row.name, row.city, row.state, row.stall_count, row.max_power_kw) for row in rows)
    return [by_name[n] for n in REAL_SITE_NAMES if haskey(by_name, n)]
end

function load_real_battery_kwh_values()
    rows = _query("SELECT battery_kwh_estimate FROM epa_vehicle WHERE battery_kwh_estimate IS NOT NULL")
    return Float64[row.battery_kwh_estimate for row in rows]
end

function service_time_moments(power_kw::Float64, draw_battery_kwh::Function; n::Int=2000, seed::Int=42)
    rng = MersenneTwister(seed)
    samples = [
        charge_duration_hours(
            clamp(rand(rng, INITIAL_SOC_DIST), 0.02, TARGET_SOC - 0.01), TARGET_SOC,
            draw_battery_kwh(rng), power_kw; temp_c=TEMP_C,
        ) for _ in 1:n
    ]
    mean_s = sum(samples) / n
    var_s = sum((s - mean_s)^2 for s in samples) / (n - 1)
    return mean_s, var_s
end

function compare_scenario(site::RealSite, real_battery_kwh_values::Vector{Float64})
    mean_arb, var_arb = service_time_moments(site.max_power_kw, rng -> rand(rng, ARBITRARY_BATTERY_KWH_DIST))
    mean_real, var_real = service_time_moments(site.max_power_kw, rng -> real_battery_kwh_values[rand(rng, 1:length(real_battery_kwh_values))])

    arrival_rate = TARGET_UTILIZATION * site.stall_count / mean_arb  # tuned once, held fixed across both

    println("\n", "-"^100)
    println("$(site.name)  ($(site.city), $(site.state))  stalls=$(site.stall_count) max_power_kw=$(site.max_power_kw)")
    println("  service time   arbitrary Uniform(60,100)=$(round(mean_arb*60, digits=1)) min   " *
            "real EV fleet mix=$(round(mean_real*60, digits=1)) min" *
            "   (Δ $(round((mean_real-mean_arb)*60, digits=1)) min, $(round(100*(mean_real/mean_arb - 1), digits=1))%)")

    for (label, mean_s, var_s) in (("arbitrary", mean_arb, var_arb), ("real fleet mix", mean_real, var_real))
        try
            q = mgc_queue(arrival_rate, site.stall_count, mean_s, var_s)
            println("  $label queue: utilization=$(round(q.utilization, digits=3))  mean wait=$(round(q.mean_wait_hours*60, digits=2)) min")
        catch e
            e isa AssertionError || rethrow()
            println("  $label queue: UNSTABLE at this arrival rate (utilization would exceed 1.0 — a " *
                     "real operational risk this comparison surfaces: sizing arrivals to the arbitrary " *
                     "60-100 kWh assumption understates load once the real, larger-battery EV fleet is " *
                     "substituted in, not a bug)")
        end
    end
end

function main()
    println("="^100)
    println("VOLTERRA Phase 6 — real EPA EV fleet-mix battery sizes vs. arbitrary Uniform(60,100) baseline")
    println("Real: site_id, stall_count, max_power_kw, EPA-derived battery_kwh. Modeled: arrival rate, SOC.")
    println("="^100)

    real_battery_kwh_values = load_real_battery_kwh_values()
    if isempty(real_battery_kwh_values)
        println("\nNo real EPA vehicle data in the warehouse yet. From repo root:")
        println("  cd ingestion && python -m supplemental.epa")
        println("  python warehouse/build_warehouse.py")
        return
    end

    println("\n$(length(real_battery_kwh_values)) real EPA-derived battery_kwh estimates on file " *
             "(2023+ model years). Real range: $(round(minimum(real_battery_kwh_values), digits=1)) - " *
             "$(round(maximum(real_battery_kwh_values), digits=1)) kWh, mean=" *
             "$(round(mean(real_battery_kwh_values), digits=1)) kWh — versus the arbitrary " *
             "Uniform(60,100) assumption's fixed 60-100 kWh range, mean=80 kWh.")

    sites = load_real_sites()
    for site in sites
        compare_scenario(site, real_battery_kwh_values)
    end

    println("\n", "="^100)
end

main()
