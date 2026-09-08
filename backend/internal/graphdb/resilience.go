package graphdb

import (
	"context"
	"fmt"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
)

// SiteMetrics mirrors graph/src/resilience_graph.jl's SiteResilienceMetrics — three separate real
// fields, never blended into one score, per this project's cross-cutting discipline. JSON tags
// use snake_case to match every other tool's output shape in this package (see tools.go) and the
// warehouse.Site convention this replaced the resilience.SiteMetrics equivalent of.
type SiteMetrics struct {
	SiteID      string  `json:"site_id"`
	Name        string  `json:"name"`
	City        *string `json:"city"`
	State       *string `json:"state"`
	Degree      int     `json:"degree"`
	Betweenness float64 `json:"betweenness"`
	Criticality int     `json:"criticality"`
}

// ResilienceMetrics computes real degree centrality, betweenness centrality, and the Charging
// Criticality Index over whatever real graph LoadGraph most recently stored. Degree and
// betweenness are computed BY Memgraph (a native Cypher count and MAGE's betweenness_centrality
// procedure, respectively); criticality is computed here in Go from a Memgraph-sourced edge list
// — see this package's doc comment for why.
func (c *Client) ResilienceMetrics(ctx context.Context) ([]SiteMetrics, error) {
	nodeResult, err := neo4j.ExecuteQuery(ctx, c.driver, `
		MATCH (s:ChargingSite)
		OPTIONAL MATCH (s)--(neighbor)
		RETURN s.site_id AS site_id, s.name AS name, s.city AS city, s.state AS state,
		       count(neighbor) AS degree`,
		nil, neo4j.EagerResultTransformer)
	if err != nil {
		return nil, fmt.Errorf("querying real site nodes: %w", err)
	}

	metricsBySite := make(map[string]*SiteMetrics, len(nodeResult.Records))
	var order []string
	for _, rec := range nodeResult.Records {
		siteID, _ := rec.Get("site_id")
		name, _ := rec.Get("name")
		city, _ := rec.Get("city")
		state, _ := rec.Get("state")
		degree, _ := rec.Get("degree")

		m := &SiteMetrics{SiteID: siteID.(string), Name: name.(string), Degree: int(degree.(int64))}
		if city != nil {
			s := city.(string)
			m.City = &s
		}
		if state != nil {
			s := state.(string)
			m.State = &s
		}
		metricsBySite[m.SiteID] = m
		order = append(order, m.SiteID)
	}

	betweennessResult, err := neo4j.ExecuteQuery(ctx, c.driver, `
		CALL betweenness_centrality.get(false) YIELD node, betweenness_centrality
		RETURN node.site_id AS site_id, betweenness_centrality`,
		nil, neo4j.EagerResultTransformer)
	if err != nil {
		return nil, fmt.Errorf("querying real betweenness centrality: %w", err)
	}
	for _, rec := range betweennessResult.Records {
		siteID, _ := rec.Get("site_id")
		betweenness, _ := rec.Get("betweenness_centrality")
		if m, ok := metricsBySite[siteID.(string)]; ok {
			m.Betweenness = betweenness.(float64)
		}
	}

	edgeResult, err := neo4j.ExecuteQuery(ctx, c.driver, `
		MATCH (a:ChargingSite)-[:NEAR]->(b:ChargingSite)
		RETURN a.site_id AS from_id, b.site_id AS to_id`,
		nil, neo4j.EagerResultTransformer)
	if err != nil {
		return nil, fmt.Errorf("querying real edges for criticality: %w", err)
	}
	edges := make([][2]string, len(edgeResult.Records))
	for i, rec := range edgeResult.Records {
		fromID, _ := rec.Get("from_id")
		toID, _ := rec.Get("to_id")
		edges[i] = [2]string{fromID.(string), toID.(string)}
	}

	criticality := criticalityIndex(order, edges)
	for siteID, score := range criticality {
		metricsBySite[siteID].Criticality = score
	}

	metrics := make([]SiteMetrics, len(order))
	for i, siteID := range order {
		metrics[i] = *metricsBySite[siteID]
	}
	return metrics, nil
}

// criticalityIndex ports graph/src/resilience_graph.jl's criticality_index/pairs_within_components
// exactly: for each real site, how many pairs of the OTHER real sites are connected (directly, or
// via a path -- possibly through this site) in the full real graph, versus once this site (and
// its real edges) is removed. The difference is exactly the pairs whose only connecting path ran
// through this site.
func criticalityIndex(siteIDs []string, edges [][2]string) map[string]int {
	adjacency := make(map[string][]string, len(siteIDs))
	for _, id := range siteIDs {
		adjacency[id] = nil
	}
	for _, e := range edges {
		adjacency[e[0]] = append(adjacency[e[0]], e[1])
		adjacency[e[1]] = append(adjacency[e[1]], e[0])
	}

	fullComponents := connectedComponentsExcluding(siteIDs, adjacency, "")

	result := make(map[string]int, len(siteIDs))
	for _, v := range siteIDs {
		before := pairsWithinComponents(fullComponents, v)
		afterComponents := connectedComponentsExcluding(siteIDs, adjacency, v)
		after := pairsWithinComponents(afterComponents, "")
		result[v] = before - after
	}
	return result
}

// connectedComponentsExcluding returns the connected components of the real graph over
// `siteIDs`/`adjacency`, treating `exclude` (if non-empty) as removed entirely -- neither
// appearing in any component nor traversable through.
func connectedComponentsExcluding(siteIDs []string, adjacency map[string][]string, exclude string) [][]string {
	visited := make(map[string]bool, len(siteIDs))
	if exclude != "" {
		visited[exclude] = true
	}

	var components [][]string
	for _, start := range siteIDs {
		if visited[start] {
			continue
		}
		var component []string
		queue := []string{start}
		visited[start] = true
		for len(queue) > 0 {
			cur := queue[0]
			queue = queue[1:]
			component = append(component, cur)
			for _, nbr := range adjacency[cur] {
				if visited[nbr] {
					continue
				}
				visited[nbr] = true
				queue = append(queue, nbr)
			}
		}
		components = append(components, component)
	}
	return components
}

// pairsWithinComponents counts unordered pairs of site IDs that fall in the same component,
// ignoring any site equal to `excluding` (so it isn't counted as "paired" with its own
// component-mates when the caller wants pairs of OTHER sites only). Pass "" to exclude nothing.
func pairsWithinComponents(components [][]string, excluding string) int {
	total := 0
	for _, comp := range components {
		k := 0
		for _, id := range comp {
			if id != excluding {
				k++
			}
		}
		total += k * (k - 1) / 2
	}
	return total
}
