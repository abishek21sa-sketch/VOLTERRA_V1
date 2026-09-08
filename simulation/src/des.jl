"""
Discrete-event simulation of a single charging site: EV -> arrive -> queue for a stall ->
charge (duration from charging_curve.jl) -> release the stall -> depart. Built on ConcurrentSim.jl.

This exists to validate the analytical M/G/c approximation (queueing.jl) empirically, and as the
foundation Phase 4+ optimization outputs get evaluated against under stochastic demand — the
simulator never re-derives location/capacity decisions itself (see ../README.md).
"""

using ConcurrentSim
using ResumableFunctions
using Distributions
using Random

"""
    SiteSimConfig

`stall_count` and `rated_power_kw` should come from the warehouse (REAL). Everything else is
MODELED — see docs/data-sources.md — since Tesla publishes no session-level arrival, SOC, or
battery-size data.
"""
struct SiteSimConfig
    stall_count::Int
    rated_power_kw::Float64
    arrival_rate_per_hour::Float64
    battery_kwh_dist::Distribution
    initial_soc_dist::Distribution
    target_soc::Float64
    temp_c::Float64
    sim_duration_hours::Float64
end

struct VehicleRecord
    arrival_time::Float64
    service_start_time::Float64
    service_end_time::Float64
end

wait_hours(r::VehicleRecord) = r.service_start_time - r.arrival_time
service_hours(r::VehicleRecord) = r.service_end_time - r.service_start_time

@resumable function ev_service_process(sim::Simulation, charger::Resource, arrival_time::Float64,
                                        duration_hours::Float64, records::Vector{VehicleRecord})
    @yield lock(charger)
    service_start = now(sim)
    @yield timeout(sim, duration_hours)
    service_end = now(sim)
    @yield unlock(charger)
    push!(records, VehicleRecord(arrival_time, service_start, service_end))
end

@resumable function ev_arrival_process(sim::Simulation, charger::Resource, cfg::SiteSimConfig,
                                        records::Vector{VehicleRecord}, rng::AbstractRNG)
    interarrival_dist = Exponential(1.0 / cfg.arrival_rate_per_hour)
    while true
        @yield timeout(sim, rand(rng, interarrival_dist))

        soc0 = clamp(rand(rng, cfg.initial_soc_dist), 0.02, cfg.target_soc - 0.01)
        battery_kwh = rand(rng, cfg.battery_kwh_dist)
        duration = charge_duration_hours(soc0, cfg.target_soc, battery_kwh, cfg.rated_power_kw;
                                          temp_c=cfg.temp_c)

        @process ev_service_process(sim, charger, now(sim), duration, records)
    end
end

"""
    run_site_simulation(cfg; seed=42) -> Vector{VehicleRecord}

Runs one replication for `cfg.sim_duration_hours` and returns every vehicle that completed
charging within the window (vehicles still mid-charge when the window ends are not included —
they have no `service_end_time` to report).
"""
function run_site_simulation(cfg::SiteSimConfig; seed::Int=42)
    rng = MersenneTwister(seed)
    sim = Simulation()
    charger = Resource(sim, cfg.stall_count)
    records = VehicleRecord[]
    @process ev_arrival_process(sim, charger, cfg, records, rng)
    run(sim, cfg.sim_duration_hours)
    return records
end

"""
    SimulationSummary

Empirical statistics from a simulation run, structured to compare directly against a
`QueueingResult` from queueing.jl's analytical approximation.
"""
struct SimulationSummary
    n_completed::Int
    mean_wait_hours::Float64
    p_wait_gt_zero::Float64
    mean_service_hours::Float64
    var_service_hours::Float64
end

function summarize(records::Vector{VehicleRecord})
    isempty(records) && return SimulationSummary(0, 0.0, 0.0, 0.0, 0.0)

    waits = wait_hours.(records)
    services = service_hours.(records)
    mean_service = sum(services) / length(services)
    var_service = length(services) > 1 ?
        sum((s - mean_service)^2 for s in services) / (length(services) - 1) : 0.0

    return SimulationSummary(
        length(records),
        sum(waits) / length(waits),
        count(w -> w > 1e-9, waits) / length(waits),
        mean_service,
        var_service,
    )
end
