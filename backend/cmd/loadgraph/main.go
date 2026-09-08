// Command loadgraph reads every real site from the warehouse and loads it into Memgraph as a
// real :ChargingSite graph (see internal/graphdb's doc comment for the edge rule and what's real
// vs. modeled). Run this once after `docker compose up -d memgraph` and whenever the warehouse's
// real site data changes — internal/graphdb.LoadGraph clears and reloads the whole graph each run,
// so it's always safe to re-run.
//
//	go run ./cmd/loadgraph
package main

import (
	"context"
	"log"
	"os"

	"github.com/abishek/volterra/backend/internal/graphdb"
	"github.com/abishek/volterra/backend/internal/warehouse"
)

func main() {
	dbPath := os.Getenv("VOLTERRA_WAREHOUSE_PATH")
	if dbPath == "" {
		dbPath = "../warehouse/volterra.duckdb"
	}
	memgraphURI := os.Getenv("VOLTERRA_MEMGRAPH_URI")
	if memgraphURI == "" {
		memgraphURI = "bolt://localhost:7687"
	}

	sites, err := warehouse.QuerySites(dbPath)
	if err != nil {
		log.Fatalf("querying real warehouse sites: %v", err)
	}
	log.Printf("loaded %d real sites from the warehouse", len(sites))

	nodes := make([]graphdb.SiteNode, len(sites))
	for i, s := range sites {
		nodes[i] = graphdb.SiteNode{
			SiteID: s.SiteID, Name: s.Name, City: s.City, State: s.State,
			StallCount: s.StallCount, MaxPowerKW: s.MaxPowerKW,
			Latitude: s.Latitude, Longitude: s.Longitude,
		}
	}

	ctx := context.Background()
	client, err := graphdb.NewClient(memgraphURI)
	if err != nil {
		log.Fatalf("connecting to Memgraph at %s: %v", memgraphURI, err)
	}
	defer client.Close(ctx)

	if err := client.VerifyConnectivity(ctx); err != nil {
		log.Fatalf("Memgraph not reachable at %s: %v (is `docker compose up -d memgraph` running?)", memgraphURI, err)
	}

	if err := client.LoadGraph(ctx, nodes); err != nil {
		log.Fatalf("loading real graph into Memgraph: %v", err)
	}
	log.Printf("loaded %d real sites and their real proximity edges (threshold=%.0fmi) into Memgraph", len(nodes), graphdb.RangeThresholdMi)
}
