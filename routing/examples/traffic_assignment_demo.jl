"""
Phase 9 demo: system-optimal (SO) vs. user-equilibrium (UE) traffic assignment over real
alternate routes between Las Vegas, NV and Nephi, UT. At a 250-mile range threshold, Phase 5's
resilience analysis already found this corridor gains real alternate paths through St. George and
Beaver, UT that don't exist at a tighter threshold -- this demo discovers those real paths
programmatically (`all_simple_paths`) and asks: if a modeled volume of EV trips shares this real
corridor, does everyone picking their own fastest route (UE) cost the network more in aggregate
than a centrally coordinated assignment (SO)? The gap is the real "price of anarchy."

Run from the routing/ directory:
    julia --project=. examples/traffic_assignment_demo.jl

Real: site coordinates, stall counts, max_power_kw, and the M/G/c queueing + charging-curve
machinery computing congestion cost. Modeled: the demand volume itself (Tesla publishes no real
EV-trip data) and BASELINE_UTILIZATION -- see traffic_assignment.jl's module docstring.
"""

using VolterraGraph
using VolterraRouting

const RANGE_THRESHOLD_MI = 250.0
const DEMAND_SWEEP_VEH_PER_HOUR = [0.5, 1.0, 2.0, 4.0, 6.0, 8.0]

function main()
    sites = load_sites()
    site_of = Dict(s.site_id => s for s in sites)
    rg = build_resilience_graph(sites, RANGE_THRESHOLD_MI)

    lv = only(filter(s -> occursin("Las Vegas", s.name), sites))
    np = only(filter(s -> occursin("Nephi", s.name), sites))

    raw_paths = all_simple_paths(rg, lv.site_id, np.site_id; max_hops=5)
    println("="^100)
    println("VOLTERRA Phase 9 — system-optimal vs. user-equilibrium traffic assignment")
    println("Real corridor: $(lv.name) -> $(np.name), $(length(raw_paths)) real alternate path(s) at $(RANGE_THRESHOLD_MI)mi threshold.")
    println("="^100)

    if length(raw_paths) < 2
        println("\nFewer than 2 real alternate paths at this threshold -- nothing to compare. Try a wider RANGE_THRESHOLD_MI.")
        return
    end

    path_options = [build_path_option(site_of, p) for p in raw_paths]
    println("\nReal alternate routes discovered:")
    for (i, opt) in enumerate(path_options)
        println("  [$i] ", join(opt.site_ids, " -> "), "  (", round(opt.total_drive_hours, digits=2),
                 "h real drive time, ", length(opt.intermediate_sites), " real charging stop(s))")
    end

    println("\nDemand sweep (vehicles/hour on this OD pair) -- UE vs. SO total system cost:")
    println(rpad("demand", 10), rpad("UE total (veh-hr)", 20), rpad("SO total (veh-hr)", 20),
             rpad("price of anarchy", 18), "UE flow split")
    for demand in DEMAND_SWEEP_VEH_PER_HOUR
        ue = assign_traffic(path_options, site_of, demand; marginal_cost=false)
        so = assign_traffic(path_options, site_of, demand; marginal_cost=true)
        if !ue.stable || !so.stable
            println(rpad(string(demand), 10), "UNSTABLE at this demand level (no path keeps every real site's queue stable)")
            continue
        end
        poa = ue.total_cost_hours / so.total_cost_hours
        split = join([string(round(f, digits=2)) for f in ue.flow_per_hour], " / ")
        println(
            rpad(string(demand), 10),
            rpad(string(round(ue.total_cost_hours, digits=3)), 20),
            rpad(string(round(so.total_cost_hours, digits=3)), 20),
            rpad(string(round(poa, digits=4)), 18),
            split,
        )
    end

    println("\n", "="^100)
end

main()
