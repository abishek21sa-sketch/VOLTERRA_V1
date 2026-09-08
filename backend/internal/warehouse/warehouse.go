// Package warehouse reads VOLTERRA's DuckDB warehouse read-only.
//
// There is no CGO-based DuckDB driver in play here — this dev environment has no C compiler
// (CGO_ENABLED builds need one on Windows), and go-duckdb requires CGO. Instead this shells out
// to the duckdb CLI (a single portable binary, no CGO) with -json output. That keeps the Go
// process a true read-only client of the warehouse file, same as every other module, without
// pulling in a C toolchain dependency. Revisit if/when a pure-Go DuckDB driver exists.
package warehouse

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

// Site mirrors the columns QuerySites selects from charging_site — see ../../../warehouse/schema.sql.
type Site struct {
	SiteID           string  `json:"site_id"`
	OperatorID       string  `json:"operator_id"`
	Name             string  `json:"name"`
	City             *string `json:"city"`
	State            *string `json:"state"`
	Latitude         float64 `json:"latitude"`
	Longitude        float64 `json:"longitude"`
	StallCount       int     `json:"stall_count"`
	MaxPowerKW       float64 `json:"max_power_kw"`
	AccessRestricted bool    `json:"access_restricted"`
}

const sitesQuery = `LOAD spatial;
SELECT site_id, operator_id, name, city, state,
       ST_Y(geom) AS latitude, ST_X(geom) AS longitude,
       stall_count, max_power_kw, access_restricted
FROM charging_site
ORDER BY name;`

func duckdbExecutable() string {
	if configured := os.Getenv("VOLTERRA_DUCKDB_PATH"); configured != "" {
		return configured
	}
	if resolved, err := exec.LookPath("duckdb"); err == nil {
		return resolved
	}
	// Windows package managers commonly install DuckDB outside PATH. Resolve the
	// official WinGet layout so a clean developer shell and CI runner get the
	// same read-only warehouse behavior as an interactive shell with PATH set.
	if runtime.GOOS == "windows" {
		if localAppData := os.Getenv("LOCALAPPDATA"); localAppData != "" {
			pattern := filepath.Join(localAppData, "Microsoft", "WinGet", "Packages", "DuckDB.cli_*", "duckdb.exe")
			if matches, err := filepath.Glob(pattern); err == nil && len(matches) > 0 {
				return matches[0]
			}
		}
	}
	return "duckdb"
}

// QuerySites returns every charging site currently in the warehouse.
func QuerySites(dbPath string) ([]Site, error) {
	cmd := exec.Command(duckdbExecutable(), dbPath, "-json", "-readonly", "-c", sitesQuery)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("duckdb query failed: %w (output: %s)", err, out)
	}

	var sites []Site
	if err := json.Unmarshal(out, &sites); err != nil {
		return nil, fmt.Errorf("parsing duckdb json output: %w", err)
	}
	return sites, nil
}
