"""
Network resilience: builds a graph over real charging sites and computes degree centrality,
betweenness centrality, and a Charging Criticality Index (connectivity loss from removing each
site), matching the project brief's framing: "A remote Interstate site with moderate utilization
may actually be more important than an urban site with enormous throughput because it preserves
long-distance network connectivity."

**Real**: every site's coordinates (from the warehouse).
**Modeled**: the edge rule. There is no real highway/corridor topology yet (FHWA data isn't
ingested until Phase 6 — see docs/roadmap.md) — an actual road-network graph would encode which
sites are reachable via real highway segments, not just geographic closeness. Until then, two
sites are connected here if they're within `range_threshold_mi` of each other as the crow flies:
a reasonable, clearly-labeled proxy for "plausible next EV hop," not a claim about real roads.
`range_threshold_mi` is a parameter, not a hardcoded constant — see the module's demo for how
much the resulting criticality findings depend on this modeled assumption, which is itself worth
knowing rather than hiding behind one arbitrary number.

**Target architecture, not yet reachable**: the project's design calls for this analysis to run
as Cypher against Memgraph (see ../load.cypher and ../README.md) — Docker's daemon was
unresponsive in this dev environment (confirmed hung, not a fast timeout) when this was built, so
it's implemented directly in Julia with Graphs.jl instead, which was already a declared
dependency. Swap this out for the Cypher path once Memgraph is reachable; the algorithms
(betweenness centrality, connectivity-loss-on-removal) are the same either way.
"""

using Graphs

struct ResilienceEdge
    site_id_a::String
    site_id_b::String
    distance_mi::Float64
end

struct ResilienceGraph
    sites::Vector{GeoSite}
    index_of::Dict{String,Int}     # site_id -> index into `sites` / graph vertex number
    g::SimpleGraph{Int}
    edges::Vector{ResilienceEdge}
    range_threshold_mi::Float64
end

"""
    build_resilience_graph(sites, range_threshold_mi) -> ResilienceGraph

Connects every pair of sites within `range_threshold_mi` of each other (great-circle distance).
"""
function build_resilience_graph(sites::Vector{GeoSite}, range_threshold_mi::Float64)
    index_of = Dict(site.site_id => i for (i, site) in enumerate(sites))
    g = SimpleGraph(length(sites))
    edges = ResilienceEdge[]

    for i in 1:length(sites), j in (i + 1):length(sites)
        d = haversine_miles(sites[i].latitude, sites[i].longitude, sites[j].latitude, sites[j].longitude)
        if d <= range_threshold_mi
            add_edge!(g, i, j)
            push!(edges, ResilienceEdge(sites[i].site_id, sites[j].site_id, d))
        end
    end

    return ResilienceGraph(sites, index_of, g, edges, range_threshold_mi)
end

struct SiteResilienceMetrics
    site::GeoSite
    degree::Int
    betweenness::Float64
    criticality::Int  # pairs of OTHER sites that become disconnected if this site is removed
end

"""
    pairs_within_components(components, excluding=0) -> Int

Given a partition of vertices into connected components, counts unordered pairs that fall in
the same component, ignoring any vertex equal to `excluding` (so it doesn't get counted as a
"pair" with itself's component-mates when the caller wants pairs of *other* vertices only).
"""
function pairs_within_components(components::Vector{Vector{Int}}; excluding::Int=0)
    total = 0
    for comp in components
        k = excluding == 0 ? length(comp) : count(!=(excluding), comp)
        total += k * (k - 1) ÷ 2
    end
    return total
end

"""
    criticality_index(rg::ResilienceGraph) -> Dict{String,Int}

For each site, counts how many pairs of the *other* sites are connected (directly, or via a
path — possibly through this site) in the full graph, versus connected once this site (and its
edges) is removed entirely. The difference is exactly the pairs whose only connecting path ran
through this site. Zero means removing the site changes nothing for anyone else; a site with
siblings on both sides of it in a chain, and no shortcut around it, scores high. This is the real
formalization of "removing a charging site disconnects journeys" from the project brief, not a
proxy metric.
"""
function criticality_index(rg::ResilienceGraph)
    n = nv(rg.g)
    result = Dict{String,Int}()

    full_components = connected_components(rg.g)  # over all n vertices; paths may use any vertex

    for v in 1:n
        others = [u for u in 1:n if u != v]
        before = pairs_within_components(full_components; excluding=v)

        g_minus_v, _ = induced_subgraph(rg.g, others)
        after = pairs_within_components(connected_components(g_minus_v))

        result[rg.sites[v].site_id] = before - after
    end

    return result
end

"""
    compute_resilience_metrics(rg::ResilienceGraph) -> Vector{SiteResilienceMetrics}

Degree centrality, betweenness centrality, and the criticality index, reported as separate
fields per site — never blended into one score, per this project's discipline (see root
CLAUDE.md and the Health Score / Network Protection Portfolio precedent it's modeled on).
"""
function compute_resilience_metrics(rg::ResilienceGraph)
    deg = degree(rg.g)
    betw = betweenness_centrality(rg.g)
    crit = criticality_index(rg)

    return [
        SiteResilienceMetrics(rg.sites[i], deg[i], betw[i], crit[rg.sites[i].site_id])
        for i in 1:length(rg.sites)
    ]
end
