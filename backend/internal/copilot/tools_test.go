package copilot

import (
	"context"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/abishek/volterra/backend/internal/graphdb"
	"github.com/abishek/volterra/backend/internal/warehouse"
)

// realToolsConfig points at the real warehouse/Python venv this repo's dev setup produces,
// relative to this package (backend/internal/copilot/ -> repo root is ../../../). Every test in
// this file is guarded and skips if its real prerequisite isn't present, matching the pattern
// this project's Julia and Python test suites already use for real-warehouse integration tests.
func realToolsConfig(t *testing.T) ToolsConfig {
	t.Helper()
	root := filepath.Join("..", "..", "..")
	return ToolsConfig{
		WarehousePath:    filepath.Join(root, "warehouse", "volterra.duckdb"),
		PythonExecutable: filepath.Join(root, "ml", ".venv", "Scripts", "python.exe"),
		PredictOneScript: filepath.Join(root, "ml", "examples", "predict_one.py"),
	}
}

func skipIfMissing(t *testing.T, path, reason string) {
	t.Helper()
	if _, err := os.Stat(path); err != nil {
		t.Skipf("%s not present (%s) -- skipping", path, reason)
	}
}

func skipIfMemgraphUnreachable(t *testing.T) {
	t.Helper()
	conn, err := net.Dial("tcp", "localhost:7687")
	if err != nil {
		t.Skip("Memgraph not reachable on localhost:7687 -- run `docker compose up -d memgraph` first")
	}
	conn.Close()
}

func TestListSitesTool_RealWarehouse(t *testing.T) {
	cfg := realToolsConfig(t)
	skipIfMissing(t, cfg.WarehousePath, "warehouse/build_warehouse.py hasn't been run")

	tool := listSitesTool(cfg)
	out, err := tool.Execute(context.Background(), nil)
	if err != nil {
		t.Fatalf("list_sites failed: %v", err)
	}

	var sites []map[string]any
	if err := json.Unmarshal([]byte(out), &sites); err != nil {
		t.Fatalf("list_sites output isn't valid JSON: %v (output: %s)", err, out)
	}
	if len(sites) == 0 {
		t.Error("expected at least one real site, got none")
	}
	for _, s := range sites {
		if _, ok := s["stall_count"]; !ok {
			t.Errorf("expected every site to have a stall_count field, got %+v", s)
		}
	}
}

func TestSiteDetailsTool_RealWarehouse(t *testing.T) {
	cfg := realToolsConfig(t)
	skipIfMissing(t, cfg.WarehousePath, "warehouse/build_warehouse.py hasn't been run")

	tool := siteDetailsTool(cfg)

	t.Run("real match", func(t *testing.T) {
		out, err := tool.Execute(context.Background(), json.RawMessage(`{"name_query":"Las Vegas"}`))
		if err != nil {
			t.Fatalf("site_details failed: %v", err)
		}
		if !strings.Contains(out, "Las Vegas") {
			t.Errorf("expected output to mention Las Vegas, got %s", out)
		}
	})

	t.Run("no match", func(t *testing.T) {
		out, err := tool.Execute(context.Background(), json.RawMessage(`{"name_query":"Nonexistent Site XYZ"}`))
		if err != nil {
			t.Fatalf("site_details failed: %v", err)
		}
		if !strings.Contains(out, `"matches": []`) {
			t.Errorf("expected an empty-matches note, got %s", out)
		}
	})

	t.Run("empty query rejected", func(t *testing.T) {
		if _, err := tool.Execute(context.Background(), json.RawMessage(`{"name_query":""}`)); err == nil {
			t.Error("expected an error for an empty name_query, got nil")
		}
	})
}

func TestNetworkResilienceTool_RealMemgraph(t *testing.T) {
	cfg := realToolsConfig(t)
	skipIfMissing(t, cfg.WarehousePath, "warehouse/build_warehouse.py hasn't been run")
	skipIfMemgraphUnreachable(t)

	ctx := context.Background()
	client, err := graphdb.NewClient("bolt://localhost:7687")
	if err != nil {
		t.Fatalf("connecting to Memgraph: %v", err)
	}
	defer client.Close(ctx)

	sites, err := warehouse.QuerySites(cfg.WarehousePath)
	if err != nil {
		t.Fatalf("querying real warehouse: %v", err)
	}
	nodes := make([]graphdb.SiteNode, len(sites))
	for i, s := range sites {
		nodes[i] = graphdb.SiteNode{
			SiteID: s.SiteID, Name: s.Name, City: s.City, State: s.State,
			StallCount: s.StallCount, MaxPowerKW: s.MaxPowerKW,
			Latitude: s.Latitude, Longitude: s.Longitude,
		}
	}
	if err := client.LoadGraph(ctx, nodes); err != nil {
		t.Fatalf("loading real graph into Memgraph: %v", err)
	}
	cfg.GraphDBClient = client

	tool := networkResilienceTool(cfg)

	t.Run("real match", func(t *testing.T) {
		out, err := tool.Execute(context.Background(), json.RawMessage(`{"name_query":"Las Vegas"}`))
		if err != nil {
			t.Fatalf("network_resilience failed: %v", err)
		}
		var matches []map[string]any
		if err := json.Unmarshal([]byte(out), &matches); err != nil {
			t.Fatalf("output isn't valid JSON: %v (output: %s)", err, out)
		}
		if len(matches) == 0 {
			t.Fatal("expected a real match for Las Vegas")
		}
		for _, field := range []string{"degree", "betweenness", "criticality"} {
			if _, ok := matches[0][field]; !ok {
				t.Errorf("expected field %q in the result, got %+v", field, matches[0])
			}
		}
	})

	t.Run("empty query returns every site", func(t *testing.T) {
		out, err := tool.Execute(context.Background(), json.RawMessage(`{}`))
		if err != nil {
			t.Fatalf("network_resilience failed: %v", err)
		}
		var allSites []map[string]any
		if err := json.Unmarshal([]byte(out), &allSites); err != nil {
			t.Fatalf("output isn't valid JSON: %v (output: %s)", err, out)
		}
		if len(allSites) != 17 {
			t.Errorf("expected all 17 real sites, got %d", len(allSites))
		}
	})
}

func TestQueueRiskPredictionTool_RealModel(t *testing.T) {
	cfg := realToolsConfig(t)
	skipIfMissing(t, cfg.PythonExecutable, "ml/.venv hasn't been created (pip install -e .[dev])")
	skipIfMissing(t, cfg.PredictOneScript, "ml/examples/predict_one.py is missing")

	tool := queueRiskPredictionTool(cfg)

	t.Run("valid input", func(t *testing.T) {
		out, err := tool.Execute(context.Background(),
			json.RawMessage(`{"stall_count":12,"max_power_kw":150.0,"temp_c":20.0,"utilization":0.7}`))
		if err != nil {
			t.Fatalf("queue_risk_prediction failed: %v", err)
		}
		var pred map[string]float64
		if err := json.Unmarshal([]byte(out), &pred); err != nil {
			t.Fatalf("output isn't valid JSON: %v (output: %s)", err, out)
		}
		for _, field := range []string{"mean_wait_min", "p_wait", "mean_queue_length", "p_wait_over_15min"} {
			if _, ok := pred[field]; !ok {
				t.Errorf("expected field %q in the real prediction, got %+v", field, pred)
			}
		}
	})

	t.Run("invalid utilization surfaces the real python-side error, not a crash", func(t *testing.T) {
		_, err := tool.Execute(context.Background(),
			json.RawMessage(`{"stall_count":12,"max_power_kw":150.0,"temp_c":20.0,"utilization":1.5}`))
		if err == nil {
			t.Fatal("expected an error for utilization=1.5, got nil")
		}
		if !strings.Contains(err.Error(), "utilization") {
			t.Errorf("expected the error to mention utilization, got: %v", err)
		}
	})
}
