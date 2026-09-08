"""
Phase 9 demo: state-of-charge-feasible resource-constrained shortest path (the classic EVSPP) over
the real Tesla network. Trip: Las Vegas, NV -> Nephi, UT -- the same real I-15 corridor Phase 5's
resilience analysis already found (`graph/examples/resilience_demo.jl`): a real ~302-mile
great-circle trip with real intermediate waypoints at St. George, UT and Beaver, UT.

Run from the routing/ directory:
    julia --project=. examples/soc_path_demo.jl

Real: site coordinates, real EPA vehicle range/consumption (bootstrap-sampled from the real
1,189-EV distribution), real charging-curve service time. Modeled: the circuity factor converting
real great-circle distance to a real driving-distance estimate, average highway speed, and the SOC
safety-margin/target-charge conventions -- see soc_path.jl's module docstring.
"""

using Random
using VolterraGraph
using VolterraRouting

const RANGE_THRESHOLD_MI = 250.0  # matches the wider of Phase 5's two swept thresholds
const N_VEHICLES_SAMPLED = 8

function main()
    sites = load_sites()
    vehicles = load_epa_vehicles()
    rg = build_resilience_graph(sites, RANGE_THRESHOLD_MI)

    lv = only(filter(s -> occursin("Las Vegas", s.name), sites))
    np = only(filter(s -> occursin("Nephi", s.name), sites))

    println("="^100)
    println("VOLTERRA Phase 9 — SOC-feasible resource-constrained shortest path (real EVSPP)")
    println("$(length(sites)) real sites, $(length(vehicles)) real EPA EV variants to sample from.")
    println("Trip: $(lv.name) -> $(np.name), real great-circle distance " *
             "$(round(haversine_miles(lv.latitude, lv.longitude, np.latitude, np.longitude), digits=1)) mi.")
    println("Real route topology at $(RANGE_THRESHOLD_MI)mi threshold: $(length(all_simple_paths(rg, lv.site_id, np.site_id))) distinct real path(s).")
    println("="^100)

    rng = MersenneTwister(42)
    println("\nReal EPA vehicles sampled (bootstrap from the real fleet distribution):")
    println(rpad("vehicle", 40), rpad("real range", 14), rpad("battery", 12), "result")
    for _ in 1:N_VEHICLES_SAMPLED
        v = rand(rng, vehicles)
        result = soc_feasible_shortest_path(rg, v, lv.site_id, np.site_id)
        label = "$(v.make) $(v.model) ($(v.year))"
        if result.feasible
            stops = isempty(result.charge_stops) ? "direct, no stop" :
                    join(["$(s.site_id) $(round(Int, s.soc_arrive*100))%->$(round(Int, s.soc_depart*100))%" for s in result.charge_stops], "; ")
            outcome = "OK: $(round(result.total_time_hours, digits=2))h total, stops: $stops"
        else
            outcome = "INFEASIBLE -- no real route keeps SOC above $(round(Int, MIN_SOC*100))%"
        end
        println(rpad(label, 40), rpad("$(round(Int, v.range_miles))mi", 14), rpad("$(round(Int, v.battery_kwh))kWh", 12), outcome)
    end

    println("\n", "-"^100)
    println("Validation: remove the real intermediate waypoints entirely (isolate LV/Nephi from the")
    println("rest of the corridor) and confirm even the longest-range real vehicle can no longer make the trip:")
    lv_np_only = [lv, np]
    rg_isolated = build_resilience_graph(lv_np_only, RANGE_THRESHOLD_MI)
    longest_range_ev = argmax(v -> v.range_miles, vehicles)
    result = soc_feasible_shortest_path(rg_isolated, longest_range_ev, lv.site_id, np.site_id)
    println("  $(longest_range_ev.make) $(longest_range_ev.model), real range $(round(Int, longest_range_ev.range_miles))mi: ",
             result.feasible ? "still feasible (direct hop was within real range)" : "INFEASIBLE, as expected -- confirms the intermediate real stops are load-bearing, not decorative")

    println("\n", "="^100)
end

main()
