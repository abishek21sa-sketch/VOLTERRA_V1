"""
Phase 3 demo: M/G/c analytical queueing vs. discrete-event simulation, cross-validated against
each other, for three real Tesla Supercharger sites spanning the observed capacity range.

Run from the simulation/ directory:
    julia --project=. examples/site_queueing_demo.jl

Site stall_count/max_power_kw below are real, pulled from warehouse/volterra.duckdb on
2026-08-26 (`SELECT name, stall_count, max_power_kw FROM charging_site ORDER BY stall_count`).
Not queried live here — this example intentionally has no DuckDB dependency; re-pull manually if
the warehouse changes. Arrival rate, battery size, initial SOC, and target SOC are all MODELED —
see ../../docs/data-sources.md. TARGET_UTILIZATION is a demo choice, not an observed figure.
"""

using VolterraSimulation
using Distributions

const REAL_SITES = [
    ("Cranberry Township, PA Supercharger", 6, 120.0),   # smallest, lowest power
    ("Charleston, WV Supercharger", 8, 150.0),           # typical mid-size site
    ("Santa Clarita, CA Supercharger", 32, 250.0),       # largest, highest power
]

const TARGET_UTILIZATION = 0.80  # chosen to demonstrate real queueing behavior, not a Tesla figure

# Modeled vehicle-population assumptions, held constant across sites for comparability.
const BATTERY_KWH_DIST = Uniform(60.0, 100.0)
const INITIAL_SOC_DIST = Truncated(Normal(0.30, 0.12), 0.02, 0.55)
const TARGET_SOC = 0.80

function service_time_moments(power_kw::Float64; n::Int=2000)
    samples = [
        charge_duration_hours(
            clamp(rand(INITIAL_SOC_DIST), 0.02, TARGET_SOC - 0.01), TARGET_SOC,
            rand(BATTERY_KWH_DIST), power_kw; temp_c=20.0,
        ) for _ in 1:n
    ]
    mean_s = sum(samples) / n
    var_s = sum((s - mean_s)^2 for s in samples) / (n - 1)
    return mean_s, var_s
end

function main()
    println("="^100)
    println("VOLTERRA Phase 3 — M/G/c analytical model vs. discrete-event simulation")
    println("Sites: real stall_count/max_power_kw. Arrivals/SOC/battery: MODELED — see docs/data-sources.md.")
    println("="^100)

    for (name, stalls, power_kw) in REAL_SITES
        println("\n", "-"^100)
        println("$name  (stalls=$stalls, max_power_kw=$power_kw)")

        mean_s, var_s = service_time_moments(power_kw)
        println("  charging-curve service time: mean=$(round(mean_s*60, digits=1)) min, sd=$(round(sqrt(var_s)*60, digits=1)) min  (n=2000 samples)")

        # rho = lambda*E[S]/c  =>  lambda = rho*c/E[S]
        arrival_rate = TARGET_UTILIZATION * stalls / mean_s
        println("  arrival rate (modeled, tuned to rho=$TARGET_UTILIZATION): $(round(arrival_rate, digits=2)) vehicles/hour")

        analytical = mgc_queue(arrival_rate, stalls, mean_s, var_s)
        println("  ANALYTICAL (Erlang-C + Allen-Cunneen):")
        println("    utilization (rho)      = $(round(analytical.utilization, digits=3))")
        println("    P(wait > 0)            = $(round(analytical.p_wait, digits=3))")
        println("    mean wait              = $(round(analytical.mean_wait_hours*60, digits=2)) min")
        println("    mean queue length (Lq) = $(round(analytical.mean_queue_length, digits=3))")
        println("    P(wait > 15 min)       = $(round(prob_wait_exceeds(analytical, 15/60), digits=3))")

        cfg = SiteSimConfig(stalls, power_kw, arrival_rate, BATTERY_KWH_DIST, INITIAL_SOC_DIST,
                             TARGET_SOC, 20.0, 24.0 * 30)  # 30 simulated days
        records = run_site_simulation(cfg; seed=42)
        sim = summarize(records)
        println("  SIMULATED (ConcurrentSim.jl, 30-day run, n=$(sim.n_completed) vehicles):")
        println("    P(wait > 0)            = $(round(sim.p_wait_gt_zero, digits=3))")
        println("    mean wait              = $(round(sim.mean_wait_hours*60, digits=2)) min")
        println("    mean service time      = $(round(sim.mean_service_hours*60, digits=1)) min")
    end

    println("\n", "="^100)
end

main()
