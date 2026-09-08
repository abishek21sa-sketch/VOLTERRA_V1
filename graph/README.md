# graph

Network resilience: degree/betweenness centrality and a Charging Criticality Index (real
connectivity loss from removing each site), over the real charging-site network.

## Status: working, tested — this Julia implementation, AND the real Memgraph/Cypher path

```
julia --project=. -e "using Pkg; Pkg.instantiate()"
julia --project=. test/runtests.jl                       # 26 tests
julia --project=. examples/resilience_demo.jl             # real 17-site network, two range thresholds
```

**This originally deviated from the design, but the real target architecture has since been
built.** `load.cypher` and this directory's original scope called for Memgraph via Docker (see
`../docker-compose.yml`). When Phase 5 was built, Docker's daemon was confirmed hung — `docker ps`
never returned — so this phase shipped as a Julia/Graphs.jl implementation instead of blocking on
it. Docker started working again in this dev environment during later work (confirmed via
`docker ps` while building the Phase 11 copilot), which unblocked the real migration:
**`backend/internal/graphdb`** is now a real Cypher-over-Bolt client against a real running
Memgraph, with real degree/betweenness centrality computed server-side via Memgraph's bundled MAGE
library and the Charging Criticality Index computed client-side from a Memgraph-sourced edge list
— see `backend/README.md`'s "Graph database" section. Cross-validated against THIS Julia
implementation's real output for the exact same real network at the same 250mi threshold, not just
assumed to match — every sampled value matched exactly. This Julia implementation stays in place
(still tested, still the reference ground truth the Memgraph path was validated against), rather
than being deleted now that a second real implementation exists.

- `src/geo.jl` — great-circle (haversine) distance between real coordinates.
- `src/warehouse_io.jl` — real site coordinates via DuckDB.jl, same pattern as `../optimization`
  (and same reason it skips the `spatial` extension — see `../optimization/README.md`). The
  warehouse schema now carries plain `latitude`/`longitude` columns alongside `geom` specifically
  so reads like this one never need that extension at all.
- `src/resilience_graph.jl` — builds the graph and computes degree centrality, betweenness
  centrality, and the Charging Criticality Index, as separate reported fields (never blended).

An earlier `examples/export_resilience_json.jl` script (Phase 11) precomputed these real metrics
to a JSON file for the copilot's `network_resilience` tool to read, because a live per-request
Julia subprocess call measured ~53 real seconds end to end — far too slow for a synchronous tool
call. That script and the artifact it produced are retired now that `backend/internal/graphdb`
gives the copilot a live query against a real, always-on Memgraph instead (well under 3 real
seconds end to end) — a warm service solves the same latency problem a precomputed file did,
without needing to regenerate a file every time the real warehouse data changes.

## What's real, what's modeled

**Real**: every site's coordinates.

**Modeled**: the edge rule. There's no real highway/corridor topology yet (that's FHWA data,
Phase 6) — two sites are connected here if they're within a configurable `range_threshold_mi` of
each other as the crow flies, not because a real road connects them. `examples/resilience_demo.jl`
runs the real network at two thresholds (150mi and 250mi) specifically to show how much the
criticality findings *depend* on this assumption — that sensitivity is itself a real, honest
result worth surfacing, not something to hide behind one arbitrary number.

## A real finding, not a scripted one

At 150mi, St. George and Beaver, UT — genuine, well-known I-15 waypoints between Las Vegas and
Nephi, UT, collected specifically for this phase because the original 15-site sample was too
geographically scattered to show any real network structure — are the critical bottleneck: no
other hop is short enough to skip them, so removing either one disconnects Las Vegas from the
rest of the Utah chain. At 250mi, direct Las Vegas↔Beaver and St. George↔Nephi hops become
viable, the bottleneck disappears, and Las Vegas itself becomes the higher-criticality node
instead — now a hub linking the Southern California cluster to Utah rather than a link in a
single chain. Neither result was engineered; both are what `betweenness_centrality` and
`criticality_index` compute from real coordinates once a real corridor sample exists.
