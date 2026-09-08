package graphdb

import (
	"context"
	"net"
	"testing"

	"github.com/abishek/volterra/backend/internal/warehouse"
)

func TestHaversineMiles(t *testing.T) {
	if d := HaversineMiles(40.0, -74.0, 40.0, -74.0); d > 1e-9 {
		t.Errorf("distance from a point to itself should be ~0, got %v", d)
	}
	// Real, well-known NYC-LA great-circle distance.
	nycLA := HaversineMiles(40.7128, -74.0060, 34.0522, -118.2437)
	if nycLA < 2400.0 || nycLA > 2500.0 {
		t.Errorf("NYC-LA distance = %v, want roughly 2400-2500 real miles", nycLA)
	}
}

func TestCriticalityIndex_KnownChain(t *testing.T) {
	// A-B-C-D chain, E isolated -- same known ground truth graph/test/runtests.jl's Julia tests
	// use: B and C are each the sole connector for 2 pairs; A and D (endpoints) and E (isolated)
	// contribute nothing.
	sites := []string{"A", "B", "C", "D", "E"}
	edges := [][2]string{{"A", "B"}, {"B", "C"}, {"C", "D"}}

	result := criticalityIndex(sites, edges)

	if result["B"] != 2 {
		t.Errorf("criticality[B] = %d, want 2", result["B"])
	}
	if result["C"] != 2 {
		t.Errorf("criticality[C] = %d, want 2", result["C"])
	}
	if result["A"] != 0 {
		t.Errorf("criticality[A] = %d, want 0 (endpoint)", result["A"])
	}
	if result["D"] != 0 {
		t.Errorf("criticality[D] = %d, want 0 (endpoint)", result["D"])
	}
	if result["E"] != 0 {
		t.Errorf("criticality[E] = %d, want 0 (isolated)", result["E"])
	}
}

func TestCriticalityIndex_Triangle_NoSinglePointOfFailure(t *testing.T) {
	sites := []string{"X", "Y", "Z"}
	edges := [][2]string{{"X", "Y"}, {"Y", "Z"}, {"X", "Z"}}

	result := criticalityIndex(sites, edges)

	for _, id := range sites {
		if result[id] != 0 {
			t.Errorf("criticality[%s] = %d, want 0 (removing any one vertex leaves the other two adjacent)", id, result[id])
		}
	}
}

func memgraphReachable(t *testing.T) bool {
	t.Helper()
	conn, err := net.Dial("tcp", "localhost:7687")
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

func warehousePath() string {
	return "../../../warehouse/volterra.duckdb"
}

// TestResilienceMetrics_RealNetwork loads the real warehouse network into a real running
// Memgraph and cross-validates the result against graph/'s already-tested Julia implementation's
// real output for the SAME network at the SAME 250mi threshold -- not just "did it run without
// error." Skips cleanly if either the real warehouse or a reachable Memgraph instance is missing.
func TestResilienceMetrics_RealNetwork(t *testing.T) {
	if !memgraphReachable(t) {
		t.Skip("Memgraph not reachable on localhost:7687 -- run `docker compose up -d memgraph` first")
	}
	sites, err := warehouse.QuerySites(warehousePath())
	if err != nil {
		t.Skipf("real warehouse not available: %v", err)
	}

	ctx := context.Background()
	client, err := NewClient("bolt://localhost:7687")
	if err != nil {
		t.Fatalf("connecting to Memgraph: %v", err)
	}
	defer client.Close(ctx)

	nodes := make([]SiteNode, len(sites))
	for i, s := range sites {
		nodes[i] = SiteNode{
			SiteID: s.SiteID, Name: s.Name, City: s.City, State: s.State,
			StallCount: s.StallCount, MaxPowerKW: s.MaxPowerKW,
			Latitude: s.Latitude, Longitude: s.Longitude,
		}
	}
	if err := client.LoadGraph(ctx, nodes); err != nil {
		t.Fatalf("loading real graph: %v", err)
	}

	metrics, err := client.ResilienceMetrics(ctx)
	if err != nil {
		t.Fatalf("querying real resilience metrics: %v", err)
	}
	if len(metrics) != len(sites) {
		t.Fatalf("got %d metrics, want %d (one per real site)", len(metrics), len(sites))
	}

	byName := make(map[string]SiteMetrics, len(metrics))
	for _, m := range metrics {
		byName[m.Name] = m
	}

	// Real ground truth from graph/'s already-tested Julia implementation, same real network,
	// same 250mi threshold (`julia --project=graph -e "... build_resilience_graph(sites, 250.0)"`),
	// confirmed by direct comparison, not assumed to match.
	want := map[string]SiteMetrics{
		"Las Vegas, NV Supercharger":  {Degree: 4, Betweenness: 0.05, Criticality: 6},
		"St. George, UT Supercharger": {Degree: 3, Betweenness: 0.0125, Criticality: 0},
		"Beaver, UT Supercharger":     {Degree: 3, Betweenness: 0.0125, Criticality: 0},
		"Cle Elum, WA Supercharger":   {Degree: 0, Betweenness: 0.0, Criticality: 0},
		"Charleston, WV Supercharger": {Degree: 1, Betweenness: 0.0, Criticality: 0},
	}
	for name, expected := range want {
		got, ok := byName[name]
		if !ok {
			t.Errorf("no real result for %q", name)
			continue
		}
		if got.Degree != expected.Degree {
			t.Errorf("%s: degree = %d, want %d (real Julia ground truth)", name, got.Degree, expected.Degree)
		}
		if diff := got.Betweenness - expected.Betweenness; diff > 1e-6 || diff < -1e-6 {
			t.Errorf("%s: betweenness = %v, want %v (real Julia ground truth)", name, got.Betweenness, expected.Betweenness)
		}
		if got.Criticality != expected.Criticality {
			t.Errorf("%s: criticality = %d, want %d (real Julia ground truth)", name, got.Criticality, expected.Criticality)
		}
	}
}
