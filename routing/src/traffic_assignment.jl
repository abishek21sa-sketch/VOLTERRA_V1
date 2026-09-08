"""
System-optimal (SO) vs. user-equilibrium (UE) traffic assignment for EV charging-network routing
— the "price of anarchy" of drivers picking their own route vs. a centrally coordinated network.

**The real congestion mechanism**: reuses `VolterraOptimization.service_time_moments` (real
charging-curve Monte Carlo, already validated in Phase 4) for a site's charging-time moments, and
`VolterraSimulation.mgc_queue` (the same real M/G/c model validated in Phase 3) for the real
queueing-theory wait time as a function of how much traffic is routed to that site. This directly
inherits `service_time_moments`'s known, already-documented modeling gap — its battery-size
distribution is still the original arbitrary `Uniform(60,100)` demo assumption, not the real
EPA-bootstrap distribution `warehouse/verify/vehicle_mix_scenarios.jl` showed understates real
battery sizes by ~39% (Phase 6 finding) — not a new gap introduced here, the same one already
surfaced and left open project-wide.

**Real network structure, not an invented example**: the alternate real routes this module
compares (see `traffic_assignment_demo.jl`) are exactly the ones Phase 5's resilience analysis
already found: at a 250-mile range threshold, the Las Vegas <-> Nephi, UT corridor has three
distinct real paths through the real St. George/Beaver, UT waypoints, with real, partially
overlapping intermediate stops — genuine congestion externalities (a vehicle on one path can
congest a site another path also uses) fall directly out of real geography, not a scripted setup.

**Modeled**: `BASELINE_UTILIZATION` (mirrors `capacity_selection.jl`'s convention — duplicated
rather than imported since it isn't part of that module's public API, see that file), and the
trip demand volume itself (Tesla publishes no real EV-trip data to ground this — see
`traffic_assignment_demo.jl` for the specific modeled scenario).

**Algorithm**: Method of Successive Averages (MSA), the standard textbook procedure for computing
Wardrop equilibria on small-to-medium networks. UE repeatedly assigns fresh demand to whichever
path currently has the lowest AVERAGE cost and averages it into the running flow; SO does the same
but with MARGINAL cost (own cost plus the real congestion externality imposed on other vehicles
already at a shared site, `d(flow * wait(flow))/d(flow)`, via finite difference since the M/G/c
wait function has no simple closed-form derivative). SO = UE under marginal-cost pricing is a
standard, real result from transportation science (Beckmann et al.; Wardrop's second principle),
not an invented shortcut.
"""

using VolterraGraph: GeoSite, ResilienceGraph
using VolterraOptimization: service_time_moments
using VolterraSimulation: mgc_queue

const BASELINE_UTILIZATION = 0.65  # MODELED, mirrors capacity_selection.jl's convention

struct PathOption
    site_ids::Vector{String}            # full path, origin -> destination
    intermediate_sites::Vector{String}  # real charging stops along this path (excludes both endpoints)
    total_drive_hours::Float64          # real distance / modeled speed, flow-independent
end

"""
    build_path_option(site_of, path_ids) -> PathOption

`path_ids` is a real site sequence (e.g. from `all_simple_paths`). Drive time uses the same real
haversine-times-circuity-factor distance and modeled highway speed `soc_path.jl` uses, so a path's
drive time here is directly comparable to Part 1's SOC-feasible path results.
"""
function build_path_option(site_of::Dict{String,GeoSite}, path_ids::Vector{String})
    total_drive_hours = 0.0
    for i in 1:(length(path_ids) - 1)
        d = driving_distance_mi(site_of[path_ids[i]], site_of[path_ids[i + 1]])
        total_drive_hours += d / AVG_HIGHWAY_SPEED_MPH
    end
    return PathOption(path_ids, path_ids[2:(end - 1)], total_drive_hours)
end

"""
    site_wait_hours(site, added_arrival_rate_per_hour) -> Float64

Real M/G/c mean wait at `site` given its real stall count and real charging-curve service-time
moments, at the site's real modeled baseline arrival rate (`BASELINE_UTILIZATION`, same convention
`capacity_selection.jl` uses) PLUS `added_arrival_rate_per_hour` (this scenario's routed flow).
Returns `Inf` if that pushes the real queue past stability, rather than throwing — an unstable
queue is a real, reportable outcome here (mirrors Phase 8's `MOI.INFEASIBLE` handling), not a bug.
"""
function site_wait_hours(site::GeoSite, added_arrival_rate_per_hour::Float64)
    mean_s, var_s, _ = service_time_moments(site.max_power_kw)
    baseline_arrival = BASELINE_UTILIZATION * site.stall_count / mean_s
    total_arrival = baseline_arrival + added_arrival_rate_per_hour
    total_arrival * mean_s >= site.stall_count && return Inf
    return mgc_queue(total_arrival, site.stall_count, mean_s, var_s).mean_wait_hours
end

"""
    marginal_wait_hours(site, added_arrival_rate_per_hour; h=1e-3) -> Float64

Finite-difference estimate of `d(added_arrival_rate * wait(added_arrival_rate)) / d(added_arrival_rate)`
at `site` — the real congestion externality one more vehicle/hour imposes on every vehicle already
routed there, in the same hours units as `site_wait_hours` (so directly addable to path cost).
"""
function marginal_wait_hours(site::GeoSite, added_arrival_rate_per_hour::Float64; h::Float64=1e-3)
    total0 = added_arrival_rate_per_hour * site_wait_hours(site, added_arrival_rate_per_hour)
    total1 = (added_arrival_rate_per_hour + h) * site_wait_hours(site, added_arrival_rate_per_hour + h)
    return (total1 - total0) / h
end

function path_cost_hours(opt::PathOption, site_of::Dict{String,GeoSite}, added_flow_by_site::Dict{String,Float64})
    cost = opt.total_drive_hours
    for site_id in opt.intermediate_sites
        site = site_of[site_id]
        mean_s, _, _ = service_time_moments(site.max_power_kw)
        cost += mean_s + site_wait_hours(site, get(added_flow_by_site, site_id, 0.0))
    end
    return cost
end

function path_marginal_cost_hours(opt::PathOption, site_of::Dict{String,GeoSite}, added_flow_by_site::Dict{String,Float64})
    cost = opt.total_drive_hours
    for site_id in opt.intermediate_sites
        site = site_of[site_id]
        mean_s, _, _ = service_time_moments(site.max_power_kw)
        cost += mean_s + marginal_wait_hours(site, get(added_flow_by_site, site_id, 0.0))
    end
    return cost
end

struct AssignmentResult
    flow_per_hour::Vector{Float64}     # vehicles/hour on each path, same order as the input paths
    total_cost_hours::Float64          # sum over vehicles of real travel+wait time (vehicle-hours/hour)
    stable::Bool                       # false if every path was queue-unstable at this demand
end

"""
    assign_traffic(paths, site_of, demand_per_hour; marginal_cost=false, n_iterations=300) -> AssignmentResult

Method of Successive Averages. `marginal_cost=false` computes the user-equilibrium (Wardrop)
assignment; `marginal_cost=true` computes the system-optimal assignment (SO = UE under marginal
cost, a standard result — see module docstring).
"""
function assign_traffic(paths::Vector{PathOption}, site_of::Dict{String,GeoSite}, demand_per_hour::Float64;
                         marginal_cost::Bool=false, n_iterations::Int=300)
    k = length(paths)
    flow = fill(demand_per_hour / k, k)

    for n in 1:n_iterations
        added_flow_by_site = Dict{String,Float64}()
        for (i, opt) in enumerate(paths), site_id in opt.intermediate_sites
            added_flow_by_site[site_id] = get(added_flow_by_site, site_id, 0.0) + flow[i]
        end

        costs = [
            marginal_cost ? path_marginal_cost_hours(paths[i], site_of, added_flow_by_site) :
                             path_cost_hours(paths[i], site_of, added_flow_by_site)
            for i in 1:k
        ]
        all(isinf, costs) && return AssignmentResult(flow, Inf, false)

        best = argmin(costs)
        target = zeros(k)
        target[best] = demand_per_hour
        step = 1.0 / (n + 1)
        flow = (1 - step) .* flow .+ step .* target
    end

    added_flow_by_site = Dict{String,Float64}()
    for (i, opt) in enumerate(paths), site_id in opt.intermediate_sites
        added_flow_by_site[site_id] = get(added_flow_by_site, site_id, 0.0) + flow[i]
    end
    total_cost = sum(flow[i] * path_cost_hours(paths[i], site_of, added_flow_by_site) for i in 1:k)
    return AssignmentResult(flow, total_cost, true)
end
