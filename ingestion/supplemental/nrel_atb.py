"""NREL/NLR Annual Technology Baseline (ATB) battery storage cost collector.

Data source: NREL's Annual Technology Baseline (`atb.nlr.gov` — same NREL-to-"National Laboratory
of the Rockies" rename already documented in `nrel.py`; the ATB subdomain moved too, confirmed by
DNS: `atb.nrel.gov` no longer resolves at all, `atb.nlr.gov` does). ATB is the real, publicly
published, annually-updated U.S. government baseline for electricity generation and storage
technology costs — used throughout real utility/DOE/national-lab technoeconomic modeling, not a
niche source. Real bulk CSV, found the same "read what the live site actually calls, not the
docs" way as every other source here: the ATB web app (a Tableau dashboard) links to
`https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/2024/v3.0.0/ATBe.csv` on the public
Open Energy Data Initiative (OEDI) S3 data lake — ~98.5MB, keyless, single request, same
"one request gets everything" shape as `fhwa.py`/`epa.py`.

**Why this matters for VOLTERRA specifically**: `optimization/src/capacity_selection.jl`'s
`COST_PER_STALL_USD` (charger capex) has always been an "industry-order-of-magnitude estimate,
not Tesla-published capex" — explicitly and honestly modeled, never real. Battery storage capex
for the planned Phase 7 storage-sizing model doesn't have to repeat that: ATB is a real, citable,
government-published cost baseline, not an invented number.

**Filtered to `technology == "Commercial Battery Storage"`** (not Residential or Utility-Scale —
VOLTERRA's sites are commercial DC fast charging facilities, not homes or grid-scale plants),
across all 5 real published durations (1/2/4/6/8 hour), `scenario == "Moderate"` (ATB's
middle-of-the-road case — deliberately not cherry-picking the optimistic "Advanced" or pessimistic
"Conservative" scenario), `core_metric_case == "Market"` (current market financing assumptions,
not the "R&D" case), and a configurable projection year (default: the current year, i.e. "what
would this cost to build starting now" — not a distant 2050 projection). `CAPEX` here is ATB's own
complete real figure (confirmed by cross-checking: `CAPEX = OCC + CFC` exactly, to the cent, across
every duration) — not a partial cost. `crpyears` (cost recovery period, 20 vs 30) doesn't affect
these raw $/kW figures at all (confirmed identical), so this collector fixes it at 20 arbitrarily.

Feeds a planned real battery-storage sizing/dispatch cost term in `optimization/` (Phase 7,
alongside the already-real demand-charge data from `openei.py`) — battery storage's main real
economic case at a DC fast charging site is shaving the peak demand this project already has a
real per-site $/kW charge for.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import logging
from pathlib import Path

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "nrel_atb"
RAW_CACHE_DIR = REPO_ROOT / "data" / "raw" / "nrel_atb"

BULK_CSV_URL = "https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/2024/v3.0.0/ATBe.csv"
TECHNOLOGY = "Commercial Battery Storage"
SCENARIO = "Moderate"
CASE = "Market"
CRP_YEARS = "20"  # arbitrary of {20, 30} -- confirmed the raw $/kW figures don't depend on this

logger = logging.getLogger("volterra.ingestion.nrel_atb")


class BatteryStorageCost(BaseModel):
    """One real duration variant's ATB-published cost. Real NREL/NLR observation (a published
    government technology-cost baseline), not modeled — see docs/data-sources.md. Not
    site-specific or Tesla-specific: a national technology-class baseline, same role
    `eia_electricity_price` plays for energy cost."""

    duration_hours: int
    capex_usd_per_kw: float
    fixed_om_usd_per_kw_year: float
    projection_year: int
    atb_dataset_year: int  # which year's ATB release this came from (2024 = v3.0.0)
    scenario: str = SCENARIO
    financing_case: str = CASE  # named to avoid the SQL reserved word `case` downstream
    source: str = "nrel_atb"
    observed_at: dt.datetime


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(httpx.HTTPError))
def _fetch_csv() -> bytes:
    resp = httpx.get(BULK_CSV_URL, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def run(*, projection_year: int, dry_run: bool = False) -> Path:
    """Download the real ATB bulk CSV, filter to Commercial Battery Storage at every real
    duration for `projection_year`, and write a dated, immutable snapshot."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    observed_at = dt.datetime.now(dt.timezone.utc)

    logger.info("downloading NREL/NLR ATB bulk CSV (~98MB, single request)")
    raw_csv = _fetch_csv()

    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_CACHE_DIR / f"{dt.date.today().isoformat()}_ATBe.csv").write_bytes(raw_csv)

    reader = csv.DictReader(io.StringIO(raw_csv.decode("utf-8", errors="replace")))
    capex_by_duration: dict[int, float] = {}
    om_by_duration: dict[int, float] = {}
    atb_dataset_year = None

    for row in reader:
        if (row.get("technology") != TECHNOLOGY or row.get("scenario") != SCENARIO or
                row.get("core_metric_case") != CASE or row.get("crpyears") != CRP_YEARS or
                row.get("core_metric_variable") != str(projection_year)):
            continue

        duration = int(row["techdetail"].split("Hr")[0])
        atb_dataset_year = int(row["atb_year"])
        if row["core_metric_parameter"] == "CAPEX":
            capex_by_duration[duration] = float(row["value"])
        elif row["core_metric_parameter"] == "Fixed O&M":
            om_by_duration[duration] = float(row["value"])

    costs = [
        BatteryStorageCost(
            duration_hours=duration,
            capex_usd_per_kw=capex_by_duration[duration],
            fixed_om_usd_per_kw_year=om_by_duration[duration],
            projection_year=projection_year,
            atb_dataset_year=atb_dataset_year,
            observed_at=observed_at,
        )
        for duration in sorted(capex_by_duration)
        if duration in om_by_duration
    ]
    logger.info("collected %d real battery-storage duration variants for projection year %d "
                "(ATB %s release)", len(costs), projection_year, atb_dataset_year)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{dt.date.today().isoformat()}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"{snapshot_path} already exists — snapshots are immutable, delete it manually "
            "if you really mean to replace today's snapshot."
        )

    if not dry_run:
        import json
        snapshot_path.write_text(
            json.dumps([c.model_dump(mode="json") for c in costs], indent=2), encoding="utf-8"
        )
        logger.info("wrote snapshot: %s", snapshot_path)
    else:
        logger.info("dry run — snapshot not written (would be %s)", snapshot_path)

    return snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection-year", type=int, default=dt.date.today().year,
                         help="ATB projection year to pull (default: current year)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(projection_year=args.projection_year, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
