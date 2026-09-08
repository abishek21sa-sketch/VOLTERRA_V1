"""EIA electricity retail price collector.

Data source: the U.S. Energy Information Administration's public "Electricity Retail Sales" API
(v2), part of api.eia.gov. Real, documented, public REST API — no browser workaround needed. Pulls
average commercial-sector retail electricity price ($/kWh) per state — the closest real published
figure to what a Supercharger site actually pays per kWh delivered (Tesla doesn't publish site-
level electricity contracts; commercial retail rate is a reasonable real proxy, not a modeled
guess — see docs/data-sources.md).

**Same rate-limit story as NREL.** `DEMO_KEY` is capped at 10 requests/hour here too — confirmed
via `X-Ratelimit-Limit`/`X-Ratelimit-Remaining` headers, and the response carries the same
`api-umbrella` gateway signature NLR's API does (Via: api-umbrella), suggesting shared underlying
government API-gateway infrastructure even though the domains differ. Fetch every state of
interest in one request via repeated `facets[stateid][]=` params — EIA's API supports multiple
facet values per request, so this collector never needs one request per state. Get a free real
key in seconds at https://www.eia.gov/opendata/register.php and set `EIA_API_KEY` for anything
beyond a handful of states.

Feeds a real operating-cost term into `optimization/src/capacity_selection.jl` (electricity price
× modeled energy consumption) — previously that model's objective was capex + waiting cost only,
with no cost of the electricity itself. See that module for how it's used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "eia"
RAW_CACHE_DIR = REPO_ROOT / "data" / "raw" / "eia"

BASE_URL = "https://api.eia.gov/v2/electricity/retail-sales/data/"
DEFAULT_API_KEY = "DEMO_KEY"

logger = logging.getLogger("volterra.ingestion.eia")


class EiaElectricityPrice(BaseModel):
    """One state's average commercial electricity retail price for one year. Real EIA
    observation (Forms EIA-826/861/861M), not modeled — see docs/data-sources.md."""

    state: str
    state_description: str | None = None
    sector_id: str
    sector_name: str | None = None
    period_year: int
    price_cents_per_kwh: float
    source: str = "eia"
    observed_at: dt.datetime


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(httpx.HTTPError))
def _fetch(*, api_key: str, states: list[str], sector: str, year: int) -> dict[str, Any]:
    params = [
        ("frequency", "annual"),
        ("data[0]", "price"),
        ("start", str(year)),
        ("end", str(year)),
        ("api_key", api_key),
    ] + [("facets[stateid][]", s) for s in states] + [("facets[sectorid][]", sector)]

    resp = httpx.get(BASE_URL, params=params, timeout=30)
    remaining = resp.headers.get("X-Ratelimit-Remaining")
    if remaining is not None:
        logger.info("rate limit remaining: %s / %s", remaining, resp.headers.get("X-Ratelimit-Limit"))
    resp.raise_for_status()
    return resp.json()


def run(*, states: list[str], sector: str = "COM", year: int | None = None,
        api_key: str | None = None, dry_run: bool = False) -> Path:
    """Fetch commercial (default) electricity retail prices for the given states/year and write
    a dated, immutable snapshot. `sector`: 'COM' commercial, 'IND' industrial, 'RES' residential."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = api_key or os.environ.get("EIA_API_KEY", DEFAULT_API_KEY)
    if key == DEFAULT_API_KEY:
        logger.warning("using DEMO_KEY (10 requests/hour) — get a free real key at "
                        "https://www.eia.gov/opendata/register.php and set EIA_API_KEY")

    year = year or (dt.date.today().year - 1)  # most recent full year typically available
    observed_at = dt.datetime.now(dt.timezone.utc)

    logger.info("fetching EIA retail electricity prices: states=%s sector=%s year=%d", states, sector, year)
    payload = _fetch(api_key=key, states=states, sector=sector, year=year)

    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_CACHE_DIR / f"{dt.date.today().isoformat()}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    rows = payload.get("response", {}).get("data", [])
    prices = [
        EiaElectricityPrice(
            state=row["stateid"],
            state_description=row.get("stateDescription"),
            sector_id=row["sectorid"],
            sector_name=row.get("sectorName"),
            period_year=int(row["period"]),
            price_cents_per_kwh=float(row["price"]),
            observed_at=observed_at,
        )
        for row in rows
    ]
    logger.info("collected %d state electricity prices", len(prices))

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{dt.date.today().isoformat()}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"{snapshot_path} already exists — snapshots are immutable, delete it manually "
            "if you really mean to replace today's snapshot."
        )

    if not dry_run:
        snapshot_path.write_text(
            json.dumps([p.model_dump(mode="json") for p in prices], indent=2), encoding="utf-8"
        )
        logger.info("wrote snapshot: %s", snapshot_path)
    else:
        logger.info("dry run — snapshot not written (would be %s)", snapshot_path)

    return snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", default="NV,UT,CT,NY,NJ,PA,WV,FL,CA,AL,WA,WI",
                         help="comma-separated state codes (default: states VOLTERRA has real Tesla sites in)")
    parser.add_argument("--sector", default="COM", help="COM / IND / RES")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(states=args.states.split(","), sector=args.sector, year=args.year, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
