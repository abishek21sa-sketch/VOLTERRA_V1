"""
Phase 5 demo: network resilience over the real 17-site network, at two range thresholds, to show
how much the Charging Criticality Index depends on the modeled "plausible next hop" assumption —
a real result worth seeing, not something to hide behind one arbitrary number (see
resilience_graph.jl's module docstring).

Run from the graph/ directory:
    julia --project=. examples/resilience_demo.jl
"""

using VolterraGraph

const THRESHOLDS_MI = [150.0, 250.0]

function print_report(sites, threshold)
    println("\n", "-"^100)
    println("Range threshold: $(threshold) mi")
    rg = build_resilience_graph(sites, threshold)
    println("Edges: $(length(rg.edges))")

    metrics = compute_resilience_metrics(rg)
    sort!(metrics, by=m -> -m.criticality)

    println(rpad("site", 45), rpad("degree", 8), rpad("betweenness", 14), "criticality (pairs disconnected)")
    for m in metrics
        m.degree == 0 && continue
        println(
            rpad(m.site.name, 45),
            rpad(string(m.degree), 8),
            rpad(string(round(m.betweenness, digits=3)), 14),
            m.criticality,
        )
    end

    isolated = [m.site.name for m in metrics if m.degree == 0]
    isempty(isolated) || println("\nIsolated at this threshold (no other site within range): ", join(isolated, ", "))
end

function main()
    sites = load_sites()
    println("="^100)
    println("VOLTERRA Phase 5 — network resilience (real coordinates, modeled range threshold)")
    println("$(length(sites)) real sites. See resilience_graph.jl for what's real vs. modeled.")
    println("="^100)

    for threshold in THRESHOLDS_MI
        print_report(sites, threshold)
    end

    println("\n", "="^100)
    println("Note the shift: at 150mi, St. George/Beaver UT are the critical bottleneck on the")
    println("Las Vegas <-> Nephi corridor (no direct hop is short enough to skip them). At 250mi,")
    println("a direct Las Vegas<->Beaver and St.George<->Nephi hop becomes viable, so neither")
    println("intermediate stop is a single point of failure any more -- Las Vegas itself becomes")
    println("the higher-criticality node instead, as the hub linking the SoCal cluster to Utah.")
    println("This is real network-science behavior (cut-vertex structure appearing and")
    println("disappearing as the graph gets denser) emerging from real coordinates, not a")
    println("scripted outcome.")
end

main()
