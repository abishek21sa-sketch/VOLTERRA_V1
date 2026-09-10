// Package mlpredictions reads ml/'s precomputed queue-risk predictions from a plain JSON file —
// NOT the DuckDB warehouse.
//
// This is deliberately a file read, not a warehouse query or a live call into Python/LightGBM.
// Two hard constraints rule those out: only warehouse/build_warehouse.py ever opens the DuckDB
// warehouse for writing (see ../../../warehouse/README.md's "Access discipline" — ml/ model
// output is not ingestion data and doesn't belong there), and there is no cross-language runtime
// bridge from Go to Python/LightGBM in this project (job orchestration over Redpanda, which would
// eventually enable live model calls, is still Phase 4+/11 — see the root CLAUDE.md). A plain
// JSON artifact under ml/data/ that this package reads directly is the same shape as every other
// cross-module handoff in this repo (dated snapshots, CSVs) — a file, not an RPC.
package mlpredictions

import (
	"encoding/json"
	"fmt"
	"os"
)

// Prediction is one (utilization level) row of a site's queue-risk grid. Every field here is
// MODELED — see ../../../ml/volterra_ml/queue_risk.py's module docstring for what that means and
// why (the training labels are simulation/'s own validated queueing model output, not real
// observed demand — Tesla publishes none).
type Prediction struct {
	Utilization     float64 `json:"utilization"`
	MeanWaitMin     float64 `json:"mean_wait_min"`
	PWait           float64 `json:"p_wait"`
	MeanQueueLength float64 `json:"mean_queue_length"`
	PWaitOver15Min  float64 `json:"p_wait_over_15min"`
}

// SitePredictions is one real site's queue-risk grid across the utilization sweep
// ml/examples/predict_real_sites_grid.py generated.
type SitePredictions struct {
	SiteID      string       `json:"site_id"`
	Name        string       `json:"name"`
	City        *string      `json:"city"`
	State       *string      `json:"state"`
	StallCount  int          `json:"stall_count"`
	MaxPowerKW  float64      `json:"max_power_kw"`
	TempC       float64      `json:"temp_c"`
	TempSource  string       `json:"temp_source"` // "noaa_nws" (real) or "assumed_20c_no_real_forecast" (modeled fallback) — never blurred together
	Predictions []Prediction `json:"predictions"`
}

// Load reads and parses the predictions file. Re-read per request, not cached in memory — same
// pattern as internal/warehouse.QuerySites, and the file is small (tens of KB, 20 real sites).
func Load(path string) ([]SitePredictions, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading %s: %w (has ml/examples/predict_real_sites_grid.py been run?)", path, err)
	}

	var predictions []SitePredictions
	if err := json.Unmarshal(data, &predictions); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", path, err)
	}
	return predictions, nil
}
