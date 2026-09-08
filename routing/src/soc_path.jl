"""
State-of-charge-feasible resource-constrained shortest path (the classic Electric Vehicle
Shortest Path Problem — EVSPP) over the real Tesla Supercharger network.

**Real**: site coordinates and `max_power_kw` (from the warehouse, via `VolterraGraph`); the
real-world edge topology this reuses is `VolterraGraph.build_resilience_graph`'s proximity-
threshold graph, already cross-validated against real FHWA corridor geometry in Phase 6 (see
`../../graph/README.md` and `../../warehouse/verify/corridor_alignment.py`) — not a third,
independently-invented graph representation; each real vehicle sampled from the real 1,189-EV EPA
distribution (`load_epa_vehicles`, Phase 6 — real `range_miles` and real per-mile consumption, not
derived assumptions); each stop's real charging-curve model (`VolterraSimulation.charge_duration_hours`,
Phase 3).

**Modeled**: driving distance is real great-circle distance times `CIRCUITY_FACTOR` (a standard
highway-network circuity estimate — real roads aren't straight lines — not itself measured for
this network); `AVG_HIGHWAY_SPEED_MPH`; `ORIGIN_SOC` (a full overnight charge assumption);
`MIN_SOC` (a driver safety-margin floor, never actually depleting to 0%); `CHARGE_TARGET_SOC_MAX`
(fast-charging stops target at most 80% SOC, matching `capacity_selection.jl`'s existing
`TARGET_SOC` convention — charging tapers hard above this, so real DC fast-charging behavior
rarely pushes further).

**Algorithm**: label-setting Dijkstra over an augmented state space `(site_id, soc_bucket)`, SOC
discretized into `SOC_BUCKET_STEP`-sized buckets (rounding down — conservative, never overstates
available energy). From each state, outgoing "edges" are either (a) drive to a real neighboring
site, consuming real energy for the real modeled distance, pruned if it would drop SOC below
`MIN_SOC`; or (b) charge in place to a higher SOC bucket, costing real charging-curve time. Every
edge weight (drive time or charge time) is non-negative, so ordinary Dijkstra correctness applies
— this finds the true minimum-time SOC-feasible path, not a heuristic approximation.
"""

using DataStructures
using VolterraGraph: GeoSite, ResilienceGraph, build_resilience_graph, haversine_miles
using VolterraSimulation: charge_duration_hours

const CIRCUITY_FACTOR = 1.2         # MODELED: real road distance / great-circle distance
const AVG_HIGHWAY_SPEED_MPH = 65.0  # MODELED
const SOC_BUCKET_STEP = 0.05
const MIN_SOC = 0.05                # MODELED: driver safety-margin floor
const ORIGIN_SOC = 0.90             # MODELED: full overnight home charge
const CHARGE_TARGET_SOC_MAX = 0.80  # MODELED: matches capacity_selection.jl's TARGET_SOC

"""
    driving_distance_mi(a, b) -> Float64

Real great-circle distance between two real sites, times the modeled circuity factor.
"""
function driving_distance_mi(a::GeoSite, b::GeoSite)
    return haversine_miles(a.latitude, a.longitude, b.latitude, b.longitude) * CIRCUITY_FACTOR
end

soc_bucket(soc::Float64) = floor(Int, soc / SOC_BUCKET_STEP + 1e-9)
bucket_soc(bucket::Int) = bucket * SOC_BUCKET_STEP

struct ChargeStop
    site_id::String
    soc_arrive::Float64
    soc_depart::Float64
    charge_hours::Float64
end

struct SOCPathResult
    feasible::Bool
    path::Vector{String}             # site_id sequence, origin -> destination
    charge_stops::Vector{ChargeStop} # only sites where the path actually charges
    total_time_hours::Float64
    total_distance_mi::Float64
end

abstract type PathAction end
struct DriveAction <: PathAction
    to_site::String
end
struct ChargeAction <: PathAction
    to_bucket::Int
end

"""
    soc_feasible_shortest_path(rg, vehicle, from_id, to_id) -> SOCPathResult

Minimum-time path from `from_id` to `to_id` that never lets `vehicle`'s real state of charge drop
below `MIN_SOC`, charging at real sites along the way as needed. `rg` is a
`VolterraGraph.ResilienceGraph` (real topology at whatever `range_threshold_mi` the caller built
it with — a tighter threshold means fewer, longer real hops).
"""
function soc_feasible_shortest_path(rg::ResilienceGraph, vehicle::RealEVSpec, from_id::String, to_id::String)
    site_of = Dict(s.site_id => s for s in rg.sites)
    neighbors = Dict{String,Vector{Tuple{String,Float64}}}()
    for e in rg.edges
        d = driving_distance_mi(site_of[e.site_id_a], site_of[e.site_id_b])
        push!(get!(neighbors, e.site_id_a, Tuple{String,Float64}[]), (e.site_id_b, d))
        push!(get!(neighbors, e.site_id_b, Tuple{String,Float64}[]), (e.site_id_a, d))
    end

    max_bucket = soc_bucket(CHARGE_TARGET_SOC_MAX)
    origin_bucket = min(soc_bucket(ORIGIN_SOC), max_bucket)

    dist = Dict{Tuple{String,Int},Float64}()
    prev = Dict{Tuple{String,Int},Tuple{Tuple{String,Int},PathAction}}()
    start = (from_id, origin_bucket)
    dist[start] = 0.0
    pq = PriorityQueue{Tuple{String,Int},Float64}()
    pq[start] = 0.0

    while !isempty(pq)
        (site_id, bucket) = dequeue!(pq)
        t = dist[(site_id, bucket)]

        if site_id == to_id
            return _reconstruct(prev, start, (site_id, bucket), site_of, vehicle)
        end

        # Option A: drive to each real neighbor at the current (bucket-quantized) SOC.
        soc_now = bucket_soc(bucket)
        for (nbr_id, d_mi) in get(neighbors, site_id, Tuple{String,Float64}[])
            energy_needed = d_mi * vehicle.consumption_kwh_per_mile
            soc_needed = energy_needed / vehicle.battery_kwh
            soc_after = soc_now - soc_needed
            soc_after < MIN_SOC && continue  # infeasible hop from this SOC level -- pruned, not an error
            nbr_bucket = soc_bucket(soc_after)
            drive_time = d_mi / AVG_HIGHWAY_SPEED_MPH
            cand = t + drive_time
            key = (nbr_id, nbr_bucket)
            if cand < get(dist, key, Inf)
                dist[key] = cand
                prev[key] = ((site_id, bucket), DriveAction(nbr_id))
                pq[key] = cand
            end
        end

        # Option B: charge in place, one SOC bucket at a time -- not "to any target bucket in one
        # jump". Restricting to single-bucket steps makes a multi-bucket charge session's total
        # time additive BY CONSTRUCTION (there's only one way to compute it: chain the steps), so
        # Dijkstra can't be tempted by a spurious few-microsecond quadrature difference between a
        # direct wide-interval numerical integration and a chain of narrower ones over the same
        # real interval (`charge_duration_hours` integrates numerically, so those two aren't
        # bit-identical) -- a real bug caught during Phase 9 development, see routing/README.md.
        site = site_of[site_id]
        if bucket < max_bucket
            target_bucket = bucket + 1
            charge_time = charge_duration_hours(bucket_soc(bucket), bucket_soc(target_bucket),
                                                 vehicle.battery_kwh, site.max_power_kw)
            cand = t + charge_time
            key = (site_id, target_bucket)
            if cand < get(dist, key, Inf)
                dist[key] = cand
                prev[key] = ((site_id, bucket), ChargeAction(target_bucket))
                pq[key] = cand
            end
        end
    end

    return SOCPathResult(false, String[], ChargeStop[], Inf, Inf)
end

function _reconstruct(prev, start, goal, site_of, vehicle::RealEVSpec)
    states = [goal]
    while states[end] != start
        states = push!(states, prev[states[end]][1])
    end
    reverse!(states)

    actions = PathAction[]
    for i in 1:(length(states) - 1)
        push!(actions, prev[states[i + 1]][2])
    end

    path = String[states[1][1]]
    charge_stops = ChargeStop[]
    total_distance = 0.0
    # Each ChargeAction here is a single SOC bucket step (see the search loop above); consecutive
    # steps at the same real site are one real charging session, not several, so they're merged
    # into one ChargeStop with ONE consolidated charge_duration_hours call over the full real
    # interval -- consistent with how the search itself only ever computes single-step charge
    # times, never a direct wide-interval one.
    pending_site = nothing
    pending_soc_arrive = 0.0
    function flush_pending!(soc_depart::Float64)
        pending_site === nothing && return
        charge_hours = charge_duration_hours(pending_soc_arrive, soc_depart, vehicle.battery_kwh,
                                              site_of[pending_site].max_power_kw)
        push!(charge_stops, ChargeStop(pending_site, pending_soc_arrive, soc_depart, charge_hours))
        pending_site = nothing
    end

    for (i, a) in enumerate(actions)
        (site_id, bucket) = states[i]
        if a isa DriveAction
            flush_pending!(bucket_soc(bucket))
            push!(path, a.to_site)
            total_distance += driving_distance_mi(site_of[site_id], site_of[a.to_site])
        else
            pending_site === nothing && (pending_site = site_id; pending_soc_arrive = bucket_soc(bucket))
        end
    end
    flush_pending!(bucket_soc(states[end][2]))

    # Recomputed from the reported (merged) components rather than returned as Dijkstra's raw
    # accumulated cost: the search itself sums many single-bucket charge_duration_hours calls
    # (that's what its optimality guarantee is based on), but the ONE consolidated call per merged
    # ChargeStop above integrates over the same real interval with the same n_steps, so it isn't
    # bit-identical to that chain -- recomputing here keeps the reported total exactly equal to
    # the sum of the reported parts, so nothing in the printed output looks internally inconsistent.
    total_drive_hours = total_distance / AVG_HIGHWAY_SPEED_MPH
    total_charge_hours = sum(cs.charge_hours for cs in charge_stops; init=0.0)
    return SOCPathResult(true, path, charge_stops, total_drive_hours + total_charge_hours, total_distance)
end

"""
    all_simple_paths(rg, from_id, to_id; max_hops=4) -> Vector{Vector{String}}

Every simple (no-repeated-site) path from `from_id` to `to_id` in `rg`'s real edge topology, up to
`max_hops` real hops — plain DFS over a small real graph (17 sites), not a heavy algorithm. Used
to discover the real alternate routes `traffic_assignment.jl` needs, rather than hand-picking them.
"""
function all_simple_paths(rg::ResilienceGraph, from_id::String, to_id::String; max_hops::Int=4)
    adjacency = Dict{String,Vector{String}}()
    for e in rg.edges
        push!(get!(adjacency, e.site_id_a, String[]), e.site_id_b)
        push!(get!(adjacency, e.site_id_b, String[]), e.site_id_a)
    end

    results = Vector{String}[]
    path = [from_id]
    visited = Set([from_id])

    function dfs(current::String)
        if current == to_id
            push!(results, copy(path))
            return
        end
        length(path) > max_hops && return
        for nbr in get(adjacency, current, String[])
            nbr in visited && continue
            push!(path, nbr)
            push!(visited, nbr)
            dfs(nbr)
            pop!(path)
            delete!(visited, nbr)
        end
    end

    dfs(from_id)
    return results
end
