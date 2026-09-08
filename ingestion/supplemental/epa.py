"""EPA/DOE vehicle efficiency and range collector.

Data source: fueleconomy.gov's public bulk vehicle database (jointly maintained by EPA and DOE),
`https://www.fueleconomy.gov/feg/epadata/vehicles.csv` — a single ~22MB CSV covering every vehicle
EPA has certified fuel-economy figures for, back to 1984. No API key, no account, no rate limit.

**Bulk CSV chosen over the per-vehicle JSON API on purpose.** fueleconomy.gov also exposes a
nested REST API (`/ws/rest/vehicle/menu/model?year=&make=` → `.../menu/options?...` → a real
per-vehicle JSON at `/ws/rest/vehicle/{id}`) — that's how this module was first explored
(confirmed real, keyless, e.g. 2024 Tesla Model 3 RWD = vehicle id 47909). But it's one HTTP round
trip per model variant with no bulk-by-fuel-type endpoint, whereas the CSV is the same underlying
database in one request — confirmed by cross-checking id 47909's CSV row against the JSON API
response for the same id: identical `range`/`combE`/`charge240`. Same "one request gets
everything" shape as `fhwa.py`'s corridor pull, so the CSV wins here.

Filtered to `fuelType1 == "Electricity"` (battery-electric only — no PHEV/hybrid) and
`year >= MIN_MODEL_YEAR` (current-generation vehicles actually being sold and charged today, not
every EV EPA has ever certified back to early-2010s short-range models that skew a "what's
plugging into a Supercharger in 2026" fleet-mix sample).

**No published battery-kWh field.** EPA's `battery` column is `-1` (unavailable) for every row
checked. `battery_kwh_estimate` here is DERIVED — `range_miles * comb_e_kwh_per_100mi / 100` —
not itself an EPA-published figure, so it's tagged `source='epa_fueleconomy_gov_derived'`
(distinct from the directly-published fields' `epa_fueleconomy_gov`) per this project's
provenance discipline. It approximates energy drawn from the wall for a full charge (EPA's combE
already reflects charging losses), which is arguably the more useful real quantity for a queueing
model anyway — session energy delivered, not raw pack capacity.

Feeds `simulation/src/charging_curve.jl`'s `BATTERY_KWH_DIST` — every prior run in this codebase
used an arbitrary `Uniform(60.0, 100.0)` demo assumption, never grounded in a real vehicle-mix
sample. See `warehouse/verify/vehicle_mix_scenarios.jl`.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import logging
from pathlib import Path

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "epa"
RAW_CACHE_DIR = REPO_ROOT / "data" / "raw" / "epa"

BULK_CSV_URL = "https://www.fueleconomy.gov/feg/epadata/vehicles.csv"
MIN_MODEL_YEAR = 2023  # current-generation EV fleet, not EPA's full history back to 1984

logger = logging.getLogger("volterra.ingestion.epa")


class EpaVehicle(BaseModel):
    """One real EV model-year variant's EPA-certified efficiency/range. `battery_kwh_estimate` is
    DERIVED from two real EPA fields, not itself a published EPA figure — see module docstring."""

    make: str
    model: str
    year: int
    range_miles: float | None = None
    comb_e_kwh_per_100mi: float | None = None
    charge240_hours: float | None = None
    battery_kwh_estimate: float | None = None
    source: str = "epa_fueleconomy_gov"
    observed_at: dt.datetime


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(httpx.HTTPError))
def _fetch_csv() -> bytes:
    resp = httpx.get(BULK_CSV_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _to_float(raw: str) -> float | None:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None  # EPA uses 0/-1 for "not applicable/unavailable"


def run(*, min_model_year: int = MIN_MODEL_YEAR, dry_run: bool = False) -> Path:
    """Download the real EPA/DOE bulk vehicle CSV, filter to battery-electric vehicles of
    `min_model_year` or newer, and write a dated, immutable snapshot."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    observed_at = dt.datetime.now(dt.timezone.utc)

    logger.info("downloading EPA/DOE bulk vehicle CSV (~22MB, single request)")
    raw_csv = _fetch_csv()

    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_CACHE_DIR / f"{dt.date.today().isoformat()}_vehicles.csv").write_bytes(raw_csv)

    reader = csv.DictReader(io.StringIO(raw_csv.decode("utf-8", errors="replace")))
    vehicles: list[EpaVehicle] = []
    for row in reader:
        if row.get("fuelType1") != "Electricity":
            continue
        year_raw = row.get("year", "")
        if not year_raw.isdigit() or int(year_raw) < min_model_year:
            continue

        range_miles = _to_float(row.get("range", ""))
        comb_e = _to_float(row.get("combE", ""))
        battery_kwh_estimate = (
            round(range_miles * comb_e / 100.0, 1) if range_miles and comb_e else None
        )

        vehicles.append(EpaVehicle(
            make=row["make"],
            model=row["model"],
            year=int(year_raw),
            range_miles=range_miles,
            comb_e_kwh_per_100mi=comb_e,
            charge240_hours=_to_float(row.get("charge240", "")),
            battery_kwh_estimate=battery_kwh_estimate,
            source="epa_fueleconomy_gov_derived" if battery_kwh_estimate else "epa_fueleconomy_gov",
            observed_at=observed_at,
        ))

    logger.info("collected %d real EV model-year variants (model year >= %d)", len(vehicles), min_model_year)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{dt.date.today().isoformat()}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"{snapshot_path} already exists — snapshots are immutable, delete it manually "
            "if you really mean to replace today's snapshot."
        )

    if not dry_run:
        snapshot_path.write_text(
            json.dumps([v.model_dump(mode="json") for v in vehicles], indent=2), encoding="utf-8"
        )
        logger.info("wrote snapshot: %s", snapshot_path)
    else:
        logger.info("dry run — snapshot not written (would be %s)", snapshot_path)

    return snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-model-year", type=int, default=MIN_MODEL_YEAR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(min_model_year=args.min_model_year, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
