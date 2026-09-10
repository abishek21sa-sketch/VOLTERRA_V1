"""Precomputes queue-risk predictions for real charging sites across a utilization grid, for
`backend/`'s `GET /api/demand` to serve to the frontend's DEMAND layer.

**Why NREL AFDC sites, not `charging_site` (Tesla's own Find Us feed).** This script originally
read `charging_site`, populated by replaying `data/snapshots/tesla/*.json`. Those snapshots were
never committed (see `.gitignore`) and were collected once, interactively, during earlier
development -- `ingestion/tesla/collector.py` cannot reproduce them because Tesla's edge (Akamai
Bot Manager) returns 403 Access Denied to `tesla.com/api/findus/*` from this environment (confirmed
directly: a bare `curl` to that endpoint gets the same 403 a browser session would). With that
local-only snapshot gone, `charging_site` is empty in the current warehouse and this script would
previously have silently written an empty predictions file. NREL's AFDC feed (`nrel_station`,
already in the warehouse and used by `GET /api/sites`) includes many of the same real physical
Supercharger locations under NREL's own registry -- `station_name` frequently reads literally
"<city>, <state> - Tesla Supercharger" -- and is not subject to the same block, so this is real
site data, just from a different (unblocked) public source than before.

**What's real here and what isn't.** Site names, cities, states, coordinates, and DC-fast stall
counts (`ev_dc_fast_num`) are NREL AFDC's real published values. `temp_c` is a real current NWS
forecast temperature fetched live for each site's coordinates (same public, keyless
`api.weather.gov` source as `ingestion/supplemental/noaa.py`, called directly here since that
module is wired to `charging_site` site IDs rather than NREL's). `max_power_kw` has no equivalent
in NREL's public feed (it lists connector counts, not per-stall power ratings), so it uses a
disclosed assumed value for Tesla's V3 Supercharger hardware (250kW rated, widely published) rather
than inventing a fabricated per-site figure -- tagged `power_source` in the output, never blurred
together with the real fields, per this project's real-vs-modeled discipline (see `temp_source`
below for the same pattern already established for temperature).

Run from ml/, after train_queue_risk_model.py has produced ../data/models/:
    python examples/predict_real_sites_grid.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from volterra_ml.queue_risk import load_models, predict_queue_risk

REPO_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "volterra.duckdb"
OUT_PATH = REPO_ROOT / "ml" / "data" / "queue_risk_predictions.json"

UTILIZATION_GRID = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50, 0.55, ..., 0.95

ASSUMED_MAX_POWER_KW = 250.0  # Tesla V3 Supercharger rated power; NREL AFDC has no per-stall figure
MAX_SITES = 20
MIN_DCFC_STALLS = 4

NWS_USER_AGENT = "VOLTERRA-research/0.1 (github.com, EV charging network research project)"
NWS_REQUEST_DELAY_SECONDS = 1.0


def load_real_sites() -> list[dict]:
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        rows = con.execute("""
            SELECT station_id, station_name, city, state, latitude, longitude,
                   COALESCE(ev_dc_fast_num, 0) AS stall_count
            FROM nrel_station
            WHERE COALESCE(ev_dc_fast_num, 0) >= ?
            ORDER BY ev_dc_fast_num DESC
            LIMIT ?
        """, [MIN_DCFC_STALLS, MAX_SITES]).fetchall()
        columns = ["site_id", "name", "city", "state", "latitude", "longitude", "stall_count"]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        con.close()


@retry(retry=retry_if_exception_type(httpx.HTTPError), stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_current_temp_c(client: httpx.Client, lat: float, lon: float) -> float | None:
    points = client.get(f"https://api.weather.gov/points/{lat},{lon}")
    points.raise_for_status()
    forecast_url = points.json()["properties"]["forecast"]
    forecast = client.get(forecast_url)
    forecast.raise_for_status()
    period = forecast.json()["properties"]["periods"][0]
    temp_f = float(period["temperature"])
    return round((temp_f - 32.0) * 5.0 / 9.0, 2)


def attach_real_temps(sites: list[dict]) -> None:
    with httpx.Client(headers={"User-Agent": NWS_USER_AGENT}, timeout=15.0, follow_redirects=True) as client:
        for i, site in enumerate(sites):
            try:
                temp_c = _fetch_current_temp_c(client, site["latitude"], site["longitude"])
                site["temp_c"] = temp_c
                site["temp_source"] = "noaa_nws"
            except httpx.HTTPError as exc:
                site["temp_c"] = 20.0
                site["temp_source"] = "assumed_20c_no_real_forecast"
                print(f"  NOAA fetch failed for {site['name']!r}: {exc}; using assumed 20.0C")
            if i < len(sites) - 1:
                time.sleep(NWS_REQUEST_DELAY_SECONDS)


def main() -> None:
    print("=" * 100)
    print("VOLTERRA Phase 6 — precomputing queue-risk predictions for backend/'s DEMAND layer")
    print("=" * 100)

    sites = load_real_sites()
    print(f"\n{len(sites)} real NREL AFDC sites loaded (>= {MIN_DCFC_STALLS} real DC-fast stalls).")

    attach_real_temps(sites)
    n_with_real_temp = sum(1 for s in sites if s["temp_source"] == "noaa_nws")
    print(f"{n_with_real_temp}/{len(sites)} sites got a real current NOAA forecast temp; "
          f"{len(sites) - n_with_real_temp} fell back to an assumed 20.0C, clearly tagged as such.")

    models = load_models()

    output = []
    for site in sites:
        predictions = [
            {"utilization": u, **predict_queue_risk(
                models, stall_count=site["stall_count"], max_power_kw=ASSUMED_MAX_POWER_KW,
                temp_c=site["temp_c"], utilization=u,
            )}
            for u in UTILIZATION_GRID
        ]
        output.append({
            "site_id": str(site["site_id"]),
            "name": site["name"],
            "city": site["city"],
            "state": site["state"],
            "stall_count": site["stall_count"],
            "max_power_kw": ASSUMED_MAX_POWER_KW,
            "power_source": "assumed_250kw_v3_supercharger_no_real_per_stall_rating",
            "temp_c": site["temp_c"],
            "temp_source": site["temp_source"],
            "predictions": predictions,
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nWrote {len(output)} sites x {len(UTILIZATION_GRID)} utilization levels to {OUT_PATH}")


if __name__ == "__main__":
    main()
