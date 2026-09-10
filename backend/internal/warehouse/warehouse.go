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

// UNIONs charging_site (Tesla, populated only where Tesla's own site collection isn't blocked --
// see README's Deployment section on the Akamai bot-detection limitation) with nrel_station
// (NREL AFDC, real and not blocked) so the map still shows real charging sites even on a
// deployment where Tesla's own snapshot is empty. nrel_station has no max_power_kw field in the
// source data; Site.MaxPowerKW is a non-pointer float64 (matching the existing frontend
// contract), so those rows report 0 rather than a guessed value -- the frontend should treat
// max_power_kw == 0 as "not reported by this source", not "no charger present".
// Uses charging_site's plain latitude/longitude columns, not ST_Y(geom)/ST_X(geom) -- schema.sql
// documents these as deliberately redundant with geom for exactly this reason: LOADing the
// spatial extension crashes some clients in this environment, and Render's runtime container has
// no cached copy of it either (confirmed live: "Extension spatial.duckdb_extension not found").
const sitesQuery = `
SELECT site_id, operator_id, name, city, state,
       latitude, longitude,
       stall_count, max_power_kw, access_restricted
FROM charging_site
UNION ALL
SELECT 'nrel-' || station_id AS site_id,
       'nrel_afdc' AS operator_id,
       station_name AS name, city, state,
       latitude, longitude,
       COALESCE(ev_dc_fast_num, 0) + COALESCE(ev_level1_evse_num, 0) + COALESCE(ev_level2_evse_num, 0) AS stall_count,
       0.0 AS max_power_kw,
       (access_code IS NOT NULL AND access_code != 'public') AS access_restricted
FROM nrel_station
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
