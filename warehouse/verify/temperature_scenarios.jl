"""
Phase 6 NOAA integration: real forecast temperatures feeding the queueing/charging-curve models,
which previously only ever ran with a hardcoded `temp_c=20.0` everywhere in this codebase (see
../../simulation/src/charging_curve.jl's `temperature_derate`/`charge_duration_hours` — no real
temperature input existed anywhere until this script).

Loads every real NOAA National Weather Service forecast period on file
(../../ingestion/supplemental/noaa.py) — every real site's every real day/night reading, not just
"today" — and picks the two climatically-different extremes (coldest and warmest real period
across the whole set), then reruns Phase 3's `site_queueing_demo.jl` comparison at the REAL
forecast temperature instead of the flat 20°C assumption. Deliberately not restricted to each
site's current reading: run in August, every site's *today* temperature is well above the
charging-curve model's 10°C cold-derate threshold, which would make the comparison show zero
effect as a seasonal accident rather than a real absence of derating. Scanning the full multi-day
forecast finds real overnight lows (e.g. Cle Elum, WA: 45°F / 7.2°C) that actually cross the
threshold — still 100% real NOAA data, just a more informative real slice of it.

Run from warehouse/verify/, against optimization/'s Project.toml (already declares both DuckDB.jl
and a VolterraSimulation path dependency — no new Project.toml needed for one verification
script):
    julia --project=../../optimization temperature_scenarios.jl

Real: site_id, stall_count, max_power_kw (Tesla), NOAA forecast temperature. Modeled: arrival
rate, battery size, initial/target SOC — same assumptions as site_queueing_demo.jl, held constant
across the two temperature scenarios so the comparison isolates the temperature effect rather
than re-tuning utilization away for each one.
"""

using DuckDB
using DBInterface
using VolterraSimulation
using Distributions
using Random

const WAREHOUSE_PATH = joinpath(@__DIR__, "..", "volterra.duckdb")

const BATTERY_KWH_DIST = Uniform(60.0, 100.0)
const INITIAL_SOC_DIST = Truncated(Normal(0.30, 0.12), 0.02, 0.55)
const TARGET_SOC = 0.80
const TARGET_UTILIZATION = 0.80  # same demo choice as site_queueing_demo.jl, not a Tesla figure
const BASELINE_TEMP_C = 20.0     # the hardcoded default every prior run in this repo used

struct SiteForecast
    site_id::String
    name::String
    city::Union{String,Missing}
    state::Union{String,Missing}
    stall_count::Int
    max_power_kw::Float64
    temp_f::Float64
    period_name::String
end

"""
    load_all_forecast_periods() -> Vector{SiteForecast}

Every real (site, forecast period) row on file — every day/night period NWS returned for every
site, not just "today"'s reading. Deliberately not deduplicated to one row per site: `main()`
picks the single coldest and single warmest real period across the whole set, and in August that
turns out to matter — every site's *current* daytime reading is well above the charging-curve
model's 10°C cold-derate threshold, but several sites' real overnight lows (e.g. Cle Elum, WA:
45°F / 7.2°C) are genuinely below it. Restricting to "today" per site would show zero derating
effect purely as a seasonal accident, not because the model or the data is wrong.
"""
function load_all_forecast_periods()
    con = DBInterface.connect(DuckDB.DB, WAREHOUSE_PATH)
    try
        result = DBInterface.execute(con, """
            SELECT s.site_id, s.name, s.city, s.state, s.stall_count, s.max_power_kw,
                   f.temperature_f, f.period_name
            FROM charging_site s
            JOIN noaa_forecast f ON f.site_id = s.site_id
            ORDER BY f.temperature_f
        """)
        return [
            SiteForecast(row.site_id, row.name, row.city, row.state, row.stall_count,
                         row.max_power_kw, row.temperature_f, row.period_name)
            for row in result
        ]
    finally
        # See optimization/src/warehouse_io.jl's module docstring: DBInterface.close! doesn't
        # synchronously release DuckDB's Windows file lock, GC.gc() is required, not optional.
        DBInterface.close!(con)
        GC.gc()
    end
end

fahrenheit_to_celsius(temp_f::Float64) = (temp_f - 32.0) * 5.0 / 9.0

function service_time_moments(power_kw::Float64, temp_c::Float64; n::Int=2000, seed::Int=42)
    rng = MersenneTwister(seed)  # fixed seed: isolates the temperature effect, not sampling noise
    samples = [
        charge_duration_hours(
            clamp(rand(rng, INITIAL_SOC_DIST), 0.02, TARGET_SOC - 0.01), TARGET_SOC,
            rand(rng, BATTERY_KWH_DIST), power_kw; temp_c=temp_c,
        ) for _ in 1:n
    ]
    mean_s = sum(samples) / n
    var_s = sum((s - mean_s)^2 for s in samples) / (n - 1)
    return mean_s, var_s
end

function compare_scenario(site::SiteForecast)
    real_temp_c = fahrenheit_to_celsius(site.temp_f)

    mean_base, var_base = service_time_moments(site.max_power_kw, BASELINE_TEMP_C)
    mean_real, var_real = service_time_moments(site.max_power_kw, real_temp_c)

    # Arrival rate tuned once, at the baseline scenario, then held fixed across both scenarios.
    arrival_rate = TARGET_UTILIZATION * site.stall_count / mean_base

    println("\n", "-"^100)
    println("$(site.name)  ($(site.city), $(site.state))  stalls=$(site.stall_count) max_power_kw=$(site.max_power_kw)")
    println("  REAL NOAA forecast: $(site.temp_f)°F ($(round(real_temp_c, digits=1))°C) — \"$(site.period_name)\"")
    println("  service time   baseline(20°C)=$(round(mean_base*60, digits=1)) min   real-temp=$(round(mean_real*60, digits=1)) min" *
            "   (Δ $(round((mean_real-mean_base)*60, digits=1)) min, $(round(100*(mean_real/mean_base - 1), digits=1))%)")

    for (label, mean_s, var_s) in (("baseline(20°C)", mean_base, var_base), ("real-temp", mean_real, var_real))
        try
            q = mgc_queue(arrival_rate, site.stall_count, mean_s, var_s)
            println("  $label queue: utilization=$(round(q.utilization, digits=3))  mean wait=$(round(q.mean_wait_hours*60, digits=2)) min")
        catch e
            e isa AssertionError || rethrow()
            println("  $label queue: UNSTABLE at this arrival rate (real temperature derating pushed " *
                    "utilization to/above 1.0 — a real operational risk this comparison surfaces, not a bug)")
        end
    end
end

function main()
    println("="^100)
    println("VOLTERRA Phase 6 — real NOAA forecast temperature vs. hardcoded 20°C baseline")
    println("Real: site_id, stall_count, max_power_kw, NOAA forecast temp. Modeled: arrivals/SOC/battery (see docs/data-sources.md).")
    println("="^100)

    forecasts = load_all_forecast_periods()
    if isempty(forecasts)
        println("\nNo real NOAA forecast data in the warehouse yet. From repo root:")
        println("  cd ingestion && python -m supplemental.noaa --from-warehouse")
        println("  python warehouse/build_warehouse.py")
        return
    end

    println("\n$(length(forecasts)) real (site, forecast period) rows on file across " *
             "$(length(unique(f.site_id for f in forecasts))) sites.")
    coldest = first(forecasts)
    warmest = last(forecasts)

    println("\nCOLDEST real forecast period on file (any site, any day/night in the current NWS forecast):")
    compare_scenario(coldest)

    if warmest.site_id != coldest.site_id || warmest.period_name != coldest.period_name
        println("\nWARMEST real forecast period on file:")
        compare_scenario(warmest)
    end

    println("\n", "="^100)
end

main()
