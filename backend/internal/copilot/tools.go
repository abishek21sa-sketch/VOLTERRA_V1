package copilot

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strconv"
	"strings"

	"github.com/abishek/volterra/backend/internal/graphdb"
	"github.com/abishek/volterra/backend/internal/warehouse"
)

// ToolsConfig is every real-data dependency the tools below need — same env-var-overridable-with-
// a-dev-default pattern main.go already uses for the warehouse and demand-predictions paths.
type ToolsConfig struct {
	WarehousePath    string
	GraphDBClient    *graphdb.Client // real, live Memgraph connection — see networkResilienceTool
	PythonExecutable string          // path to ml/'s venv python (needs volterra_ml installed)
	PredictOneScript string          // path to ml/examples/predict_one.py
}

// NewTools builds the real tool set the copilot can call. Four tools, deliberately not more:
// two are fast Go-native warehouse queries, one is a live Python subprocess call (roughly 10-20
// real measured seconds, variable across runs — see predict_one.py's doc comment), and one is a
// live Cypher query against a real, always-on Memgraph instance (internal/graphdb) — real graph
// algorithms (degree, betweenness centrality) computed server-side, sub-second, cross-validated
// against graph/'s already-tested Julia implementation for the same real network (see
// internal/graphdb's tests). This used to read a precomputed JSON artifact instead, because the
// equivalent LIVE JULIA subprocess call measured ~53s end to end — far too slow for a synchronous
// tool call; Memgraph sidesteps that entirely by staying warm as a real running service, the same
// fix a Redpanda-backed async job worker would give for slower Julia computations, without needing
// the async machinery for a query this cheap. A live, on-demand capacity-optimization tool was
// considered and dropped for the JuMP/HiGHS-via-Julia-subprocess latency reason (JuMP/HiGHS
// itself solves in under a second, but Julia's own startup/JIT warmup dominates); that stays a
// real future upgrade via job orchestration over Redpanda (scaffolded, not yet wired to any Go
// code), not something this phase pretends is already solved.
func NewTools(cfg ToolsConfig) []Tool {
	return []Tool{
		listSitesTool(cfg),
		siteDetailsTool(cfg),
		queueRiskPredictionTool(cfg),
		networkResilienceTool(cfg),
	}
}

func listSitesTool(cfg ToolsConfig) Tool {
	type siteSummary struct {
		Name       string  `json:"name"`
		City       *string `json:"city"`
		State      *string `json:"state"`
		StallCount int     `json:"stall_count"`
		MaxPowerKW float64 `json:"max_power_kw"`
	}

	return Tool{
		Definition: ToolDefinition{
			Name:        "list_sites",
			Description: "Lists every real Tesla Supercharger site in the VOLTERRA network (name, city, state, stall count, max charger power). No input needed.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{}}`),
		},
		Execute: func(_ context.Context, _ json.RawMessage) (string, error) {
			sites, err := warehouse.QuerySites(cfg.WarehousePath)
			if err != nil {
				return "", fmt.Errorf("querying warehouse: %w", err)
			}
			summaries := make([]siteSummary, len(sites))
			for i, s := range sites {
				summaries[i] = siteSummary{Name: s.Name, City: s.City, State: s.State, StallCount: s.StallCount, MaxPowerKW: s.MaxPowerKW}
			}
			out, err := json.Marshal(summaries)
			if err != nil {
				return "", fmt.Errorf("marshaling result: %w", err)
			}
			return string(out), nil
		},
	}
}

func siteDetailsTool(cfg ToolsConfig) Tool {
	type input struct {
		NameQuery string `json:"name_query"`
	}

	return Tool{
		Definition: ToolDefinition{
			Name:        "site_details",
			Description: "Real details (coordinates, stall count, max charger power) for real Tesla Supercharger site(s) whose name contains name_query (case-insensitive substring match).",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"name_query":{"type":"string","description":"substring to match against real site names, e.g. \"Las Vegas\""}},"required":["name_query"]}`),
		},
		Execute: func(_ context.Context, raw json.RawMessage) (string, error) {
			var in input
			if err := json.Unmarshal(raw, &in); err != nil {
				return "", fmt.Errorf("parsing input: %w", err)
			}
			if strings.TrimSpace(in.NameQuery) == "" {
				return "", fmt.Errorf("name_query must not be empty")
			}

			sites, err := warehouse.QuerySites(cfg.WarehousePath)
			if err != nil {
				return "", fmt.Errorf("querying warehouse: %w", err)
			}

			query := strings.ToLower(in.NameQuery)
			var matches []warehouse.Site
			for _, s := range sites {
				if strings.Contains(strings.ToLower(s.Name), query) {
					matches = append(matches, s)
				}
			}
			if len(matches) == 0 {
				return fmt.Sprintf(`{"matches": [], "note": "no real site name contains %q"}`, in.NameQuery), nil
			}
			out, err := json.Marshal(matches)
			if err != nil {
				return "", fmt.Errorf("marshaling result: %w", err)
			}
			return string(out), nil
		},
	}
}

func queueRiskPredictionTool(cfg ToolsConfig) Tool {
	type input struct {
		StallCount  int     `json:"stall_count"`
		MaxPowerKW  float64 `json:"max_power_kw"`
		TempC       float64 `json:"temp_c"`
		Utilization float64 `json:"utilization"`
	}

	return Tool{
		Definition: ToolDefinition{
			Name:        "queue_risk_prediction",
			Description: "MODELED (not observed) queue-risk prediction — mean wait time, probability of waiting, mean queue length, and probability of waiting over 15 minutes — for a charging site with the given stall count, max charger power, ambient temperature, and utilization level, from a LightGBM surrogate trained on simulation/'s validated M/G/c queueing model. Takes several real seconds to run (a live model call, not a lookup).",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"stall_count":{"type":"integer","description":"number of charging stalls, must be positive"},"max_power_kw":{"type":"number","description":"max charger power in kW, must be positive"},"temp_c":{"type":"number","description":"ambient temperature in Celsius"},"utilization":{"type":"number","description":"load level as a fraction, must be strictly between 0 and 1"}},"required":["stall_count","max_power_kw","temp_c","utilization"]}`),
		},
		Execute: func(ctx context.Context, raw json.RawMessage) (string, error) {
			var in input
			if err := json.Unmarshal(raw, &in); err != nil {
				return "", fmt.Errorf("parsing input: %w", err)
			}

			cmd := exec.CommandContext(ctx, cfg.PythonExecutable, cfg.PredictOneScript,
				strconv.Itoa(in.StallCount),
				strconv.FormatFloat(in.MaxPowerKW, 'f', -1, 64),
				strconv.FormatFloat(in.TempC, 'f', -1, 64),
				strconv.FormatFloat(in.Utilization, 'f', -1, 64),
			)
			out, err := cmd.Output()
			if err != nil {
				// predict_one.py always prints a JSON error object to stdout on invalid input
				// (not a Python traceback), so surface that if present rather than just the
				// Go-side exec error.
				if len(out) > 0 {
					return "", fmt.Errorf("queue_risk_prediction: %s", strings.TrimSpace(string(out)))
				}
				return "", fmt.Errorf("running predict_one.py: %w", err)
			}
			return strings.TrimSpace(string(out)), nil
		},
	}
}

func networkResilienceTool(cfg ToolsConfig) Tool {
	type input struct {
		NameQuery string `json:"name_query"`
	}

	return Tool{
		Definition: ToolDefinition{
			Name:        "network_resilience",
			Description: "Real network-resilience metrics (degree centrality, betweenness centrality, and the Charging Criticality Index — how many pairs of other real sites become disconnected if this one is removed) for real site(s) whose name contains name_query, computed live against the real network graph at a 250-mile geographic-proximity threshold. Reported as three separate numbers, never blended into one score.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"name_query":{"type":"string","description":"substring to match against real site names; leave empty to get every real site's metrics"}}}`),
		},
		Execute: func(ctx context.Context, raw json.RawMessage) (string, error) {
			var in input
			if len(raw) > 0 {
				if err := json.Unmarshal(raw, &in); err != nil {
					return "", fmt.Errorf("parsing input: %w", err)
				}
			}

			metrics, err := cfg.GraphDBClient.ResilienceMetrics(ctx)
			if err != nil {
				return "", fmt.Errorf("querying real resilience metrics from Memgraph: %w", err)
			}

			if strings.TrimSpace(in.NameQuery) == "" {
				out, err := json.Marshal(metrics)
				if err != nil {
					return "", fmt.Errorf("marshaling result: %w", err)
				}
				return string(out), nil
			}

			query := strings.ToLower(in.NameQuery)
			var matches []graphdb.SiteMetrics
			for _, s := range metrics {
				if strings.Contains(strings.ToLower(s.Name), query) {
					matches = append(matches, s)
				}
			}
			if len(matches) == 0 {
				return fmt.Sprintf(`{"matches": [], "note": "no real site name contains %q"}`, in.NameQuery), nil
			}
			out, err := json.Marshal(matches)
			if err != nil {
				return "", fmt.Errorf("marshaling result: %w", err)
			}
			return string(out), nil
		},
	}
}
