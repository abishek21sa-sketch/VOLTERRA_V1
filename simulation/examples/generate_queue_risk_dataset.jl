"""
Phase 6 ml/ prep: generates a training dataset for a fast ML "queue-risk" surrogate model that
approximates this module's own M/G/c analytical queueing model (`queueing.jl` +
`charging_curve.jl`) without needing a Julia process per query — see `../../ml/`.

**What the label actually is, stated plainly**: every target column here (`mean_wait_min`,
`p_wait`, `mean_queue_length`, `p_wait_over_15min`) is computed by this module's own
`mgc_queue`/`prob_wait_exceeds` — the same functions `site_queueing_demo.jl` uses and
`test/runtests.jl` validates against exact M/M/1 and from-scratch M/M/c references. This is NOT
fabricated demand data: it's a dense sweep of an already-validated deterministic model, generated
so `ml/` can train a fast approximation of it. The ML model this feeds is therefore approximating
known physics/queueing-theory outputs, not learning a hidden real-world demand pattern — no claim
is made anywhere that this represents observed Tesla session/demand data (which does not exist;
see ../../docs/data-sources.md).

**Inputs, and which are real vs. swept**:
- `stall_count`, `max_power_kw`: swept over a realistic range informed by (not limited to) the 17
  real Tesla sites' actual observed range (6-32 stalls, 120-250 kW) — swept wider (4-40, 100-300)
  so the surrogate can answer "what if" queries for candidate site designs, not just replay
  existing sites.
- `temp_c`: swept from -20°C to 45°C, covering `charging_curve.jl`'s full defined derate range
  (`COLD_DERATE_FLOOR_TEMP_C` to comfortably above the highest real NOAA reading on file, 42.8°C).
  This doesn't need real observed temperature data at every point — `temperature_derate` is a
  fully deterministic, already-implemented function (like sweeping domain values to fit sin(x)),
  not a hidden pattern needing real-world discovery.
- battery size: sampled from the REAL EPA-derived `battery_kwh_estimate` distribution
  (../../ingestion/supplemental/epa.py, 1,189 real 2023+ EV variants) — same real bootstrap
  `../../warehouse/verify/vehicle_mix_scenarios.jl` uses, not an arbitrary shape.
- `utilization`: the load level being queried, swept 0.50-0.95 (modeled, same role as every other
  arrival-rate assumption in this codebase — see docs/data-sources.md). Arrival rate is solved
  backward from this target so it isn't a separate free parameter.

Run against optimization/'s Project.toml (already declares DuckDB.jl + a VolterraSimulation path
dependency — no new Julia package added just for a CSV dump):
    julia --project=../../optimization examples/generate_queue_risk_dataset.jl [n_samples]

Writes `../../ml/data/queue_risk_training.csv` (n_samples rows, default 6000) — the random sweep
above — and `../../ml/data/real_sites_holdout.csv`: real (stall_count, max_power_kw) from all 17
real Tesla sites, crossed with 3 modeled utilization levels and the real coldest/warmest NOAA
forecast temperatures on file plus the 20°C baseline. This second file is a genuinely
held-out, real-site-grounded check (not drawn from the random sweep's distribution) for `ml/` to
validate the trained surrogate against actual site configurations, not just synthetic points.
Plain hand-written CSV (all columns numeric, no quoting/escaping needed) rather than pulling in
CSV.jl/DataFrames.jl for an eight-column dump.
"""

using VolterraSimulation
using DuckDB
using DBInterface
using Distributions
using Random

const WAREHOUSE_PATH = joinpath(@__DIR__, "..", "..", "warehouse", "volterra.duckdb")
const OUT_DIR = joinpath(@__DIR__, "..", "..", "ml", "data")
const OUT_PATH = joinpath(OUT_DIR, "queue_risk_training.csv")
const HOLDOUT_PATH = joinpath(OUT_DIR, "real_sites_holdout.csv")
const HOLDOUT_UTILIZATIONS = (0.60, 0.75, 0.90)

const STALL_COUNT_RANGE = 4:40
const MAX_POWER_KW_RANGE = (100.0, 300.0)
const TEMP_C_RANGE = (-20.0, 45.0)
const UTILIZATION_RANGE = (0.50, 0.95)
const INITIAL_SOC_DIST = Truncated(Normal(0.30, 0.12), 0.02, 0.55)
const TARGET_SOC = 0.80

function load_real_battery_kwh_values()
    con = DBInterface.connect(DuckDB.DB, WAREHOUSE_PATH)
    try
        rows = DBInterface.execute(con, "SELECT battery_kwh_estimate FROM epa_vehicle WHERE battery_kwh_estimate IS NOT NULL")
        return Float64[row.battery_kwh_estimate for row in rows]
    finally
        DBInterface.close!(con)
        GC.gc()
    end
end

function load_real_sites()
    con = DBInterface.connect(DuckDB.DB, WAREHOUSE_PATH)
    try
        rows = DBInterface.execute(con, "SELECT name, stall_count, max_power_kw FROM charging_site ORDER BY name")
        # Int(...): DuckDB.jl returns INTEGER columns as Int32 on this platform, but
        # simulation/src/queueing.jl's mgc_queue requires c::Int (Int64) -- confirmed by
        # reproduction (MethodError without this conversion).
        return [(name=row.name, stall_count=Int(row.stall_count), max_power_kw=row.max_power_kw) for row in rows]
    finally
        DBInterface.close!(con)
        GC.gc()
    end
end

function load_real_temp_extremes_c()
    con = DBInterface.connect(DuckDB.DB, WAREHOUSE_PATH)
    try
        row = DBInterface.execute(con, "SELECT min(temperature_f) AS lo, max(temperature_f) AS hi FROM noaa_forecast") |> first
        to_c(f) = (f - 32.0) * 5.0 / 9.0
        return (coldest=to_c(row.lo), warmest=to_c(row.hi))
    finally
        DBInterface.close!(con)
        GC.gc()
    end
end

"""
    service_time_moments(power_kw, temp_c, real_battery_kwh_values, rng; n=500) -> (mean, var)

Monte Carlo moments of the charging-curve service-time distribution at a given power/temperature,
battery size drawn from the real EPA distribution each sample.
"""
function service_time_moments(power_kw::Float64, temp_c::Float64, real_battery_kwh_values::Vector{Float64},
                               rng::AbstractRNG; n::Int=500)
    samples = [
        charge_duration_hours(
            clamp(rand(rng, INITIAL_SOC_DIST), 0.02, TARGET_SOC - 0.01), TARGET_SOC,
            real_battery_kwh_values[rand(rng, 1:length(real_battery_kwh_values))], power_kw; temp_c=temp_c,
        ) for _ in 1:n
    ]
    mean_s = sum(samples) / n
    var_s = sum((s - mean_s)^2 for s in samples) / (n - 1)
    return mean_s, var_s
end

function generate_row(stall_count::Integer, max_power_kw::Float64, temp_c::Float64, utilization::Float64,
                       real_battery_kwh_values::Vector{Float64})
    rng = MersenneTwister(hash((stall_count, max_power_kw, temp_c, utilization)))
    mean_s, var_s = service_time_moments(max_power_kw, temp_c, real_battery_kwh_values, rng)

    arrival_rate = utilization * stall_count / mean_s
    q = mgc_queue(arrival_rate, stall_count, mean_s, var_s)
    p_over_15 = prob_wait_exceeds(q, 15.0 / 60.0)

    return (
        stall_count=stall_count, max_power_kw=max_power_kw, temp_c=round(temp_c, digits=2),
        utilization=round(utilization, digits=3),
        mean_wait_min=round(q.mean_wait_hours * 60, digits=3),
        p_wait=round(q.p_wait, digits=4),
        mean_queue_length=round(q.mean_queue_length, digits=4),
        p_wait_over_15min=round(p_over_15, digits=4),
    )
end

function main(n_samples::Int)
    println("Loading real EPA battery-kWh distribution from the warehouse...")
    real_battery_kwh_values = load_real_battery_kwh_values()
    if isempty(real_battery_kwh_values)
        error("No epa_vehicle data in the warehouse — run the epa.py collector and rebuild first.")
    end
    println("  $(length(real_battery_kwh_values)) real EPA-derived battery_kwh values loaded.")

    sweep_rng = MersenneTwister(2026)
    rows = Vector{NamedTuple}(undef, n_samples)
    println("Generating $n_samples training rows by sweeping simulation/'s own validated M/G/c model...")
    for i in 1:n_samples
        stall_count = rand(sweep_rng, STALL_COUNT_RANGE)
        max_power_kw = round(rand(sweep_rng, Uniform(MAX_POWER_KW_RANGE...)), digits=1)
        temp_c = rand(sweep_rng, Uniform(TEMP_C_RANGE...))
        utilization = rand(sweep_rng, Uniform(UTILIZATION_RANGE...))
        rows[i] = generate_row(stall_count, max_power_kw, temp_c, utilization, real_battery_kwh_values)
        if i % 1000 == 0
            println("  $i / $n_samples")
        end
    end

    mkpath(OUT_DIR)
    header = "stall_count,max_power_kw,temp_c,utilization,mean_wait_min,p_wait,mean_queue_length,p_wait_over_15min"
    write_csv(path, rows) = open(path, "w") do io
        println(io, header)
        for row in rows
            println(io, "$(row.stall_count),$(row.max_power_kw),$(row.temp_c),$(row.utilization)," *
                         "$(row.mean_wait_min),$(row.p_wait),$(row.mean_queue_length),$(row.p_wait_over_15min)")
        end
    end

    write_csv(OUT_PATH, rows)
    println("Wrote $(length(rows)) rows to $OUT_PATH")

    println("\nGenerating real-site holdout set...")
    real_sites = load_real_sites()
    temp_extremes = load_real_temp_extremes_c()
    println("  $(length(real_sites)) real sites; real temp extremes on file: " *
             "$(round(temp_extremes.coldest, digits=1))C - $(round(temp_extremes.warmest, digits=1))C")
    holdout_temps = (20.0, temp_extremes.coldest, temp_extremes.warmest)

    # Includes the real site `name`, unlike the sweep CSV above -- several real sites share an
    # identical (stall_count, max_power_kw), so without a name a spot-check can't tell them apart
    # (confirmed: an earlier version of ml/examples/verify_real_sites.py's spot-check silently
    # printed the same underlying row 3 times because its filter had no way to distinguish sites).
    open(HOLDOUT_PATH, "w") do io
        println(io, "name," * header)
        for site in real_sites, utilization in HOLDOUT_UTILIZATIONS, temp_c in holdout_temps
            row = generate_row(site.stall_count, site.max_power_kw, temp_c, utilization, real_battery_kwh_values)
            println(io, "\"$(site.name)\",$(row.stall_count),$(row.max_power_kw),$(row.temp_c),$(row.utilization)," *
                         "$(row.mean_wait_min),$(row.p_wait),$(row.mean_queue_length),$(row.p_wait_over_15min)")
        end
    end
    n_holdout = length(real_sites) * length(HOLDOUT_UTILIZATIONS) * length(holdout_temps)
    println("Wrote $n_holdout real-site holdout rows to $HOLDOUT_PATH " *
             "($(length(real_sites)) real sites x $(length(HOLDOUT_UTILIZATIONS)) utilizations x 3 real temps)")
end

n_samples = length(ARGS) >= 1 ? parse(Int, ARGS[1]) : 6000
main(n_samples)
