"""
    VolterraSimulation

Charging-site queueing theory and discrete-event simulation for VOLTERRA (Phase 3, extended
Phase 12).

- `charging_curve.jl` — modeled vehicle charging-power taper and service-time derivation.
- `queueing.jl` — analytical M/G/c model (Erlang-C + Allen-Cunneen).
- `des.jl` — ConcurrentSim.jl discrete-event simulation, validated against the analytical model.
- `reliability.jl` (Phase 12) — per-stall failure/repair, calibrated against Tesla's real
  published aggregate Supercharger uptime (99.95%, 2024 Impact Report), layered onto `des.jl`'s
  engine — a genuinely different resilience question from `graph/`'s Phase 5 network-topology
  resilience (component-level failure/repair vs. whole-site connectivity loss).
"""
module VolterraSimulation

include("charging_curve.jl")
include("queueing.jl")
include("des.jl")
include("reliability.jl")

export charging_power_fraction, temperature_derate, charge_duration_hours
export QueueingResult, erlang_c, mgc_queue, prob_wait_exceeds
export SiteSimConfig, VehicleRecord, run_site_simulation, SimulationSummary, summarize,
       wait_hours, service_hours
export TESLA_NETWORK_UPTIME, MTTR_HOURS, MTBF_HOURS, stall_availability,
       mtbf_for_target_availability, site_full_outage_probability, run_site_simulation_with_failures

end # module VolterraSimulation
