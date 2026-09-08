// Package graphdb is a real Memgraph (Cypher over Bolt) client — the target architecture Phase 5
// originally called for (see ../../../graph/load.cypher and ../../../docker-compose.yml), built
// once Docker was confirmed working again in this dev environment (Phase 11's "confirmed hung"
// note no longer held). `graph/`'s Julia/Graphs.jl implementation was always a documented,
// tested stand-in for this, not a permanent architecture choice — this package is the real swap,
// not a second, independent reimplementation: every real number this package computes is
// cross-validated against `graph/`'s already-tested Julia output for the same real network (see
// this package's tests and `backend/README.md`'s "Graph database" section).
//
// **Degree and betweenness centrality are computed BY Memgraph**, server-side, via MAGE
// (Memgraph Advanced Graph Extensions, bundled in the `memgraph-platform` image) — real graph
// algorithms running in the graph database, not reimplemented in Go. **The Charging Criticality
// Index is computed in Go** from topology fetched via Cypher: MAGE has no single built-in
// procedure for "connectivity pairs lost if this node is removed," and `graph/`'s own Julia
// implementation is itself a custom algorithm layered on top of a connected-components primitive
// (not a single library call either) — this package does the same real translation, sourcing its
// input graph from Memgraph rather than reimplementing graph storage a third time.
package graphdb

import (
	"context"
	"fmt"
	"math"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
)

const earthRadiusMi = 3958.8

// HaversineMiles is the same real great-circle-distance formula graph/src/geo.jl and
// ml/volterra_ml/rl_routing.py already use — ported here rather than called via a bridge, for the
// same reason those files give: no cross-language runtime bridge exists in this project.
func HaversineMiles(lat1, lon1, lat2, lon2 float64) float64 {
	phi1, phi2 := lat1*math.Pi/180, lat2*math.Pi/180
	dphi := (lat2 - lat1) * math.Pi / 180
	dlambda := (lon2 - lon1) * math.Pi / 180
	a := math.Sin(dphi/2)*math.Sin(dphi/2) + math.Cos(phi1)*math.Cos(phi2)*math.Sin(dlambda/2)*math.Sin(dlambda/2)
	return 2 * earthRadiusMi * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
}

// RangeThresholdMi matches routing/'s Phase 9 choice and the retired graph/data/resilience.json
// artifact's threshold, for direct comparability across this project's modules.
const RangeThresholdMi = 250.0

type Client struct {
	driver neo4j.DriverWithContext
}

func NewClient(uri string) (*Client, error) {
	driver, err := neo4j.NewDriverWithContext(uri, neo4j.NoAuth())
	if err != nil {
		return nil, fmt.Errorf("creating Memgraph driver: %w", err)
	}
	return &Client{driver: driver}, nil
}

func (c *Client) Close(ctx context.Context) error {
	return c.driver.Close(ctx)
}

func (c *Client) VerifyConnectivity(ctx context.Context) error {
	return c.driver.VerifyConnectivity(ctx)
}

// SiteNode is the real data loaded into a Memgraph :ChargingSite node — see LoadGraph.
type SiteNode struct {
	SiteID     string
	Name       string
	City       *string
	State      *string
	StallCount int
	MaxPowerKW float64
	Latitude   float64
	Longitude  float64
}

// LoadGraph clears any existing :ChargingSite graph and reloads it from `sites`, real coordinates
// only — same MODELED geographic-proximity edge rule graph/'s Julia code uses (connect two real
// sites if their real haversine distance is within RangeThresholdMi), not real corridor topology
// (that data exists via FHWA, Phase 6, but nothing in this project consumes it for edge placement
// yet — a separate, larger gap, see docs/roadmap.md). Each real undirected proximity relationship
// is stored as ONE directed :NEAR edge in a canonical direction (lower site_id -> higher); every
// query in this package treats relationships as undirected regardless, matching the real
// semantics (geographic proximity has no direction) — storing it twice per pair would double-count
// degree.
func (c *Client) LoadGraph(ctx context.Context, sites []SiteNode) error {
	session := c.driver.NewSession(ctx, neo4j.SessionConfig{})
	defer session.Close(ctx)

	_, err := session.ExecuteWrite(ctx, func(tx neo4j.ManagedTransaction) (any, error) {
		if _, err := tx.Run(ctx, "MATCH (n:ChargingSite) DETACH DELETE n", nil); err != nil {
			return nil, fmt.Errorf("clearing existing graph: %w", err)
		}

		for _, s := range sites {
			_, err := tx.Run(ctx, `
				MERGE (s:ChargingSite {site_id: $site_id})
				SET s.name = $name, s.city = $city, s.state = $state,
				    s.stall_count = $stall_count, s.max_power_kw = $max_power_kw,
				    s.lat = $lat, s.lon = $lon`,
				map[string]any{
					"site_id": s.SiteID, "name": s.Name, "city": s.City, "state": s.State,
					"stall_count": s.StallCount, "max_power_kw": s.MaxPowerKW,
					"lat": s.Latitude, "lon": s.Longitude,
				})
			if err != nil {
				return nil, fmt.Errorf("creating site node %s: %w", s.SiteID, err)
			}
		}

		for i, a := range sites {
			for _, b := range sites[i+1:] {
				d := HaversineMiles(a.Latitude, a.Longitude, b.Latitude, b.Longitude)
				if d > RangeThresholdMi {
					continue
				}
				from, to := a.SiteID, b.SiteID
				if from > to {
					from, to = to, from
				}
				_, err := tx.Run(ctx, `
					MATCH (x:ChargingSite {site_id: $from_id}), (y:ChargingSite {site_id: $to_id})
					MERGE (x)-[:NEAR {distance_mi: $distance_mi}]->(y)`,
					map[string]any{"from_id": from, "to_id": to, "distance_mi": d})
				if err != nil {
					return nil, fmt.Errorf("creating edge %s-%s: %w", a.SiteID, b.SiteID, err)
				}
			}
		}
		return nil, nil
	})
	return err
}
