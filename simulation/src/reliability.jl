"""
Phase 12: component-level reliability — per-stall failure/repair modeled as a continuous-time
up/down process, calibrated against Tesla's REAL published aggregate Supercharger uptime (99.95%,
2024 Impact Report — see `../../docs/data-sources.md`, which explicitly flags this as a
network-wide benchmark, never disaggregated into a fake per-site figure Tesla doesn't publish).
This module respects that: `MTBF_HOURS`/`MTTR_HOURS` below are ONE uniform, network-wide pair
applied identically to every site, not derived per-site from data that doesn't exist.

**A genuinely different resilience question from `graph/`'s Phase 5 network-topology
resilience.** Phase 5 asks "what happens to network connectivity if an entire SITE goes offline"
(a discrete, all-or-nothing site removal). This module asks "what happens to QUEUEING performance
at a site whose individual stalls fail and get repaired over time" — a component-level question
`queueing.jl` and `des.jl` don't otherwise account for (both assume all `c` stalls are always
available). Redundancy (more stalls per site) is the connecting idea: a site with more stalls is
both more resilient to Phase 5's single-point-of-failure question AND less affected by any one
stall's downtime here — two different real mechanisms, reported separately, never blended into
one score, per this project's discipline.

**Real**: Tesla's 2024 Impact Report published aggregate Supercharger uptime (99.95%).
**Modeled**: `MTTR_HOURS` (mean time to repair — a real-world-plausible technician-dispatch
assumption; Tesla publishes no component-level repair-time figure to ground this in). `MTBF_HOURS`
is then DERIVED, not independently modeled — `mtbf_for_target_availability` inverts the standard
two-state availability formula so this module's steady-state per-stall availability exactly
reproduces the real 99.95% target, rather than guessing a failure rate directly.
**A stated simplification, not hidden**: stalls fail independently of each other (no shared-cause
correlation, e.g. a grid outage or heat event taking out several stalls at once) — a standard
reliability-engineering baseline assumption, but real correlated failures would make the
site-level full-outage probability below an underestimate, not an overestimate.
"""

using ConcurrentSim
using ResumableFunctions
using Distributions
using Random

const TESLA_NETWORK_UPTIME = 0.9995  # REAL, 2024 Impact Report — see ../../docs/data-sources.md
const MTTR_HOURS = 24.0               # MODELED: real-world-plausible technician dispatch/repair time

"""
    stall_availability(mtbf_hours, mttr_hours) -> Float64

Steady-state availability of a single repairable component under the standard two-state (up/down)
reliability model: `A = MTBF / (MTBF + MTTR)`. Real, textbook reliability engineering (the same
formula industrial/telecom reliability analyses use), not invented for this project.
"""
function stall_availability(mtbf_hours::Float64, mttr_hours::Float64)
    return mtbf_hours / (mtbf_hours + mttr_hours)
end

"""
    mtbf_for_target_availability(target_availability, mttr_hours) -> Float64

Inverts `stall_availability`: the MTBF that, paired with `mttr_hours`, produces exactly
`target_availability`. Used to calibrate this module's failure rate against Tesla's real published
aggregate uptime rather than guessing an MTBF directly — Tesla publishes network-wide uptime, not
a component failure rate.
"""
function mtbf_for_target_availability(target_availability::Float64, mttr_hours::Float64)
    @assert 0.0 < target_availability < 1.0 "target_availability must be in (0,1), got $target_availability"
    return mttr_hours * target_availability / (1.0 - target_availability)
end

const MTBF_HOURS = mtbf_for_target_availability(TESLA_NETWORK_UPTIME, MTTR_HOURS)

"""
    site_full_outage_probability(stall_count, mtbf_hours=MTBF_HOURS, mttr_hours=MTTR_HOURS) -> Float64

Real closed-form probability that ALL `stall_count` real stalls at a site are simultaneously down
at a random point in time, under the stated independent-failure simplification:
`(1 - stall_availability(mtbf_hours, mttr_hours))^stall_count`. A tail-risk metric, reported
separately from the queueing-degradation metrics below — this project never blends a rare
catastrophic-outage probability with an expected-case service-quality number into one score.
"""
function site_full_outage_probability(stall_count::Int; mtbf_hours::Float64=MTBF_HOURS,
                                       mttr_hours::Float64=MTTR_HOURS)
    return (1.0 - stall_availability(mtbf_hours, mttr_hours))^stall_count
end

"""
    stall_failure_process(sim, charger, mtbf_hours, mttr_hours, rng)

A `ConcurrentSim.jl` process representing ONE physical stall's failure/repair cycle, modeled by
having it periodically occupy a slot in the SAME `Resource` real EV arrivals compete for — while
"failed," a stall holds a slot exactly as a very long charging session would, correctly reducing
the site's real effective capacity for real arrivals during that window. This is a standard,
tractable DES modeling technique for capacity degradation (not a literal claim that a real driver
queues behind a broken charger the way they'd queue behind an occupied one — see this file's
module docstring) — it reuses `des.jl`'s existing `Resource`-based engine rather than requiring a
new capacity-tracking abstraction, and produces the same aggregate effective-capacity reduction
either way.
"""
@resumable function stall_failure_process(sim::Simulation, charger::Resource, mtbf_hours::Float64,
                                           mttr_hours::Float64, rng::AbstractRNG)
    uptime_dist = Exponential(mtbf_hours)
    downtime_dist = Exponential(mttr_hours)
    while true
        @yield timeout(sim, rand(rng, uptime_dist))
        @yield lock(charger)
        @yield timeout(sim, rand(rng, downtime_dist))
        @yield unlock(charger)
    end
end

"""
    run_site_simulation_with_failures(cfg; mtbf_hours=MTBF_HOURS, mttr_hours=MTTR_HOURS, seed=42) -> Vector{VehicleRecord}

Same real charging/arrival mechanics as `des.jl`'s `run_site_simulation`, plus one
`stall_failure_process` per real stall competing for the same `Resource` — real EV wait times
reported here already reflect the real network-wide reliability target's queueing consequences,
not just the idealized always-available-capacity assumption `run_site_simulation` uses.
"""
function run_site_simulation_with_failures(cfg::SiteSimConfig; mtbf_hours::Float64=MTBF_HOURS,
                                            mttr_hours::Float64=MTTR_HOURS, seed::Int=42)
    rng = MersenneTwister(seed)
    sim = Simulation()
    charger = Resource(sim, cfg.stall_count)
    records = VehicleRecord[]
    @process ev_arrival_process(sim, charger, cfg, records, rng)
    for _ in 1:cfg.stall_count
        @process stall_failure_process(sim, charger, mtbf_hours, mttr_hours, rng)
    end
    run(sim, cfg.sim_duration_hours)
    return records
end
