// VOLTERRA graph model (target schema — no loader script exists yet, see README.md).
//
// Run against Memgraph once warehouse/volterra.duckdb has real charging_site + corridor data
// (Phase 5 depends on Phase 1 sites and Phase 6 FHWA corridor ingestion).

CREATE CONSTRAINT ON (s:ChargingSite) ASSERT s.site_id IS UNIQUE;
CREATE CONSTRAINT ON (c:Corridor) ASSERT c.corridor_id IS UNIQUE;

// Node and edge creation is driven by a future loader (Python or Go) that reads
// warehouse/volterra.duckdb and issues parameterized MERGE statements shaped like:
//
// MERGE (s:ChargingSite {site_id: $site_id})
// SET s.operator_id = $operator_id, s.stall_count = $stall_count,
//     s.max_power_kw = $max_power_kw, s.lat = $lat, s.lon = $lon;
//
// MERGE (a:ChargingSite {site_id: $from_id})
// MERGE (b:ChargingSite {site_id: $to_id})
// MERGE (a)-[:NEAREST_ALONG_CORRIDOR {distance_km: $distance_km}]->(b);

// Example resilience query (target shape): sites whose removal disconnects the most
// nearest-neighbor pairs — i.e. a first-pass Charging Criticality Index.
//
// MATCH (s:ChargingSite)
// CALL betweenness_centrality.get() YIELD node, betweenness_centrality
// WHERE node = s
// RETURN s.site_id, s.name, betweenness_centrality
// ORDER BY betweenness_centrality DESC
// LIMIT 25;
