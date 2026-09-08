"""Precomputes queue-risk predictions for all 17 real Tesla sites across a utilization grid, for
`backend/`'s `GET /api/demand` to serve to the frontend's DEMAND layer.

**Why precompute to a file instead of a live model-serving endpoint.** `backend/` is Go; there is
no cross-language runtime bridge to LightGBM in this project (Julia OR/simulation output isn't
served live either — see the root CLAUDE.md, job orchestration over Redpanda is still Phase 4+/11,
not built). And per this project's hard rule (`warehouse/README.md`'s "Access discipline"), only
`warehouse/build_warehouse.py` ever opens the DuckDB warehouse for writing — ml/ model output is
not ingestion data and does not belong in `volterra.duckdb` either. Writing a plain JSON artifact
under `ml/data/` (already the established location for this module's generated files) that `Go`
reads directly is the same shape as every other cross-module handoff in this repo (dated snapshots,
CSVs) — a file, not a live RPC.

**temp_c per site, and what it means when it's not real.** Each site's prediction grid uses that
site's nearest REAL current NOAA forecast temperature where one exists
(`../ingestion/supplemental/noaa.py` — 15 of 17 real sites have one; see that collector's README
for why 2 don't, real upstream 503s). The 2 sites without one fall back to 20.0C, explicitly
tagged `temp_source: "assumed_20c_no_real_forecast"` in the output — never silently presented as
if it were a real reading, per this project's real-vs-modeled discipline.

Run from ml/, after train_queue_risk_model.py has produced ../data/models/:
    python examples/predict_real_sites_grid.py
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from volterra_ml.queue_risk import load_models, predict_queue_risk

REPO_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "volterra.duckdb"
OUT_PATH = REPO_ROOT / "ml" / "data" / "queue_risk_predictions.json"

UTILIZATION_GRID = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50, 0.55, ..., 0.95


def load_real_sites_with_temp() -> list[dict]:
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        sites = con.execute("""
            SELECT site_id, name, city, state, stall_count, max_power_kw
            FROM charging_site ORDER BY name
        """).fetchall()
        columns = ["site_id", "name", "city", "state", "stall_count", "max_power_kw"]
        rows = [dict(zip(columns, row)) for row in sites]

        temps = dict(con.execute("""
            SELECT site_id, (temperature_f - 32.0) * 5.0 / 9.0 AS temp_c
            FROM noaa_forecast
            QUALIFY row_number() OVER (PARTITION BY site_id ORDER BY snapshot_date DESC, start_time ASC) = 1
        """).fetchall())

        for row in rows:
            if row["site_id"] in temps:
                row["temp_c"] = round(temps[row["site_id"]], 2)
                row["temp_source"] = "noaa_nws"
            else:
                row["temp_c"] = 20.0
                row["temp_source"] = "assumed_20c_no_real_forecast"
        return rows
    finally:
        con.close()


def main() -> None:
    print("=" * 100)
    print("VOLTERRA Phase 6 — precomputing queue-risk predictions for backend/'s DEMAND layer")
    print("=" * 100)

    sites = load_real_sites_with_temp()
    n_with_real_temp = sum(1 for s in sites if s["temp_source"] == "noaa_nws")
    print(f"\n{len(sites)} real sites loaded ({n_with_real_temp} with a real current NOAA "
          f"forecast temp; {len(sites) - n_with_real_temp} fall back to an assumed 20.0C, "
          f"clearly tagged as such in the output).")

    models = load_models()

    output = []
    for site in sites:
        predictions = [
            {"utilization": u, **predict_queue_risk(
                models, stall_count=site["stall_count"], max_power_kw=site["max_power_kw"],
                temp_c=site["temp_c"], utilization=u,
            )}
            for u in UTILIZATION_GRID
        ]
        output.append({
            "site_id": site["site_id"],
            "name": site["name"],
            "city": site["city"],
            "state": site["state"],
            "stall_count": site["stall_count"],
            "max_power_kw": site["max_power_kw"],
            "temp_c": site["temp_c"],
            "temp_source": site["temp_source"],
            "predictions": predictions,
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nWrote {len(output)} sites x {len(UTILIZATION_GRID)} utilization levels to {OUT_PATH}")


if __name__ == "__main__":
    main()
