"""
    VolterraGraph

Network resilience analysis over the real charging-site network (Phase 5).

- `geo.jl` — great-circle distance.
- `warehouse_io.jl` — real site coordinates from the warehouse via DuckDB.jl.
- `resilience_graph.jl` — geographic proximity graph, degree/betweenness centrality, and the
  Charging Criticality Index (connectivity loss from removing each site). See its module
  docstring for why this is a Julia/Graphs.jl implementation rather than the target Memgraph/
  Cypher path (Docker was unresponsive in this dev environment).
"""
module VolterraGraph

using Graphs
using DuckDB
using DBInterface

include("geo.jl")
include("warehouse_io.jl")
include("resilience_graph.jl")

export haversine_miles
export GeoSite, load_sites, DEFAULT_WAREHOUSE_PATH
export ResilienceEdge, ResilienceGraph, build_resilience_graph
export SiteResilienceMetrics, criticality_index, compute_resilience_metrics, pairs_within_components

end # module VolterraGraph
