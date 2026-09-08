"""
    VolterraRouting

Congestion-aware EV routing for VOLTERRA (Phase 9), over the real Tesla Supercharger network.

- `warehouse_io.jl` — real EPA vehicle data (`load_epa_vehicles`), the real 1,189-EV population
  `soc_path.jl` bootstrap-samples from. Site loading itself is reused from `VolterraGraph`, not
  duplicated.
- `soc_path.jl` — the resource-constrained shortest path problem: a real vehicle's minimum-time
  route between two real sites that never lets its real state of charge run out, charging at real
  intermediate sites (real charging-curve model) as needed. Also `all_simple_paths`, a small real
  route-discovery helper `traffic_assignment.jl` uses to find real alternate routes rather than
  hand-picking them.
- `traffic_assignment.jl` — system-optimal vs. user-equilibrium traffic assignment: when many
  vehicles share the real network, does everyone picking their own shortest route (user
  equilibrium) cost the network more than a centrally coordinated assignment (system optimal)?
  Real congestion cost comes from `VolterraSimulation`'s M/G/c queueing core and
  `VolterraOptimization`'s charging-curve service-time model, the same validated machinery
  `optimization/` already uses — not a new, invented congestion function.

Demand-growth/adoption-rate uncertainty (named in Phase 8's original scope) remains unmodeled here
too, for the same reason: no real historical series exists to ground it. Real EV *trip* demand is
also unmodeled — Tesla publishes none — so the traffic-assignment demand volume is a labeled
modeled scenario, not a real observed one.
"""
module VolterraRouting

using VolterraGraph
using VolterraOptimization
using VolterraSimulation

include("warehouse_io.jl")
include("soc_path.jl")
include("traffic_assignment.jl")

export RealEVSpec, load_epa_vehicles
export driving_distance_mi, soc_bucket, bucket_soc, ChargeStop, SOCPathResult,
       soc_feasible_shortest_path, all_simple_paths,
       CIRCUITY_FACTOR, AVG_HIGHWAY_SPEED_MPH, SOC_BUCKET_STEP, MIN_SOC, ORIGIN_SOC, CHARGE_TARGET_SOC_MAX
export PathOption, build_path_option, site_wait_hours, marginal_wait_hours,
       path_cost_hours, path_marginal_cost_hours, AssignmentResult, assign_traffic, BASELINE_UTILIZATION

end # module VolterraRouting
