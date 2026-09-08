"""NREL Alternative Fuels Data Center (AFDC) station collector.

Data source: the AFDC's public "Alternative Fuel Stations" REST API. This is a genuine,
documented, public JSON API — no Selenium/browser workaround needed, unlike ingestion/tesla
(Tesla has no API at all; this one is real and intentional).

**The documented hostname (developer.nrel.gov) does not resolve — because NREL itself doesn't
exist under that name any more.** Confirmed via https://developer.nlr.gov/docs/nlr-domain-transition/:
NREL was renamed the **National Laboratory of the Rockies (NLR)**, and `developer.nrel.gov` was
retired on **2026-05-29** in favor of **developer.nlr.gov** (old API keys keep working — only the
hostname changed). Found by reading the still-branded-as-"AFDC" site's
(https://afdc.energy.gov/stations) own JS bundle, which calls `developer.nlr.gov/api/alt-fuel-stations/v1/...`
directly — AFDC itself kept its familiar domain; only the underlying developer/API portal
rebranded. Re-verify at https://developer.nlr.gov/docs/nlr-domain-transition/ if this ever
changes again.

**Rate limits are real and tighter than documented for this specific endpoint.** NLR's
site-wide documented default for `DEMO_KEY` (https://developer.nlr.gov/docs/rate-limits) is 30
requests/hour, 50/day — but this particular endpoint empirically enforces **10/hour** via its
`X-Ratelimit-Limit` response header (services are allowed to override the site default, per that
same doc). Enough for a small, focused pull, not a full national sweep. Get a free real key in
seconds at https://developer.nlr.gov/signup for anything beyond spot-checking, and set
`NREL_API_KEY`.

Unlike Tesla, this data is genuinely OEM-neutral at the source — AFDC tracks every network
(Tesla included, tagged `ev_network: "Tesla"`) plus non-networked stations. That's exactly the
"non-Tesla competitors, connector standards, station comparisons" role this source is meant to
play for VOLTERRA (see docs/data-sources.md).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "nrel"
RAW_CACHE_DIR = REPO_ROOT / "data" / "raw" / "nrel"

BASE_URL = "https://developer.nlr.gov/api/alt-fuel-stations/v1.json"
MAX_LIMIT_PER_REQUEST = 200  # API-enforced ceiling; confirmed via a 422 on limit=500

# api.data.gov-style demo keys are shared across every unauthenticated caller and rate-limited
# hard — see module docstring. Never assume DEMO_KEY has real headroom; check response headers.
DEFAULT_API_KEY = "DEMO_KEY"

logger = logging.getLogger("volterra.ingestion.nrel")


class NrelStation(BaseModel):
    """One AFDC-tracked EV charging station. Real AFDC observation, not modeled — see
    docs/data-sources.md. `ev_network` is real and may be "Tesla" — AFDC tracks every network."""

    station_id: int
    station_name: str
    ev_network: str | None = None
    latitude: float
    longitude: float
    city: str | None = None
    state: str | None = None
    street_address: str | None = None
    zip_code: str | None = None
    access_code: str | None = None  # "public" / "private"
    ev_connector_types: list[str] = Field(default_factory=list)
    ev_dc_fast_num: int | None = None
    ev_level1_evse_num: int | None = None
    ev_level2_evse_num: int | None = None
    ev_pricing: str | None = None
    facility_type: str | None = None
    open_date: str | None = None
    status_code: str | None = None       # "E" = available/open
    owner_type_code: str | None = None
    date_last_confirmed: str | None = None
    source: str = "nrel_afdc"
    observed_at: dt.datetime


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(httpx.HTTPError))
def _fetch_page(*, api_key: str, states: str, offset: int, limit: int,
                 fuel_type: str, ev_charging_level: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "api_key": api_key,
        "fuel_type": fuel_type,
        "state": states,
        "limit": limit,
        "offset": offset,
    }
    if ev_charging_level:
        params["ev_charging_level"] = ev_charging_level

    resp = httpx.get(BASE_URL, params=params, timeout=30)
    remaining = resp.headers.get("X-Ratelimit-Remaining")
    if remaining is not None:
        logger.info("rate limit remaining: %s / %s", remaining, resp.headers.get("X-Ratelimit-Limit"))
    resp.raise_for_status()
    return resp.json()


def _parse_station(raw: dict[str, Any], observed_at: dt.datetime) -> NrelStation:
    return NrelStation(
        station_id=raw["id"],
        station_name=raw["station_name"],
        ev_network=raw.get("ev_network"),
        latitude=raw["latitude"],
        longitude=raw["longitude"],
        city=raw.get("city"),
        state=raw.get("state"),
        street_address=raw.get("street_address"),
        zip_code=raw.get("zip"),
        access_code=raw.get("access_code"),
        ev_connector_types=raw.get("ev_connector_types") or [],
        ev_dc_fast_num=raw.get("ev_dc_fast_num"),
        ev_level1_evse_num=raw.get("ev_level1_evse_num"),
        ev_level2_evse_num=raw.get("ev_level2_evse_num"),
        ev_pricing=raw.get("ev_pricing"),
        facility_type=raw.get("facility_type"),
        open_date=raw.get("open_date"),
        status_code=raw.get("status_code"),
        owner_type_code=raw.get("owner_type_code"),
        date_last_confirmed=raw.get("date_last_confirmed"),
        observed_at=observed_at,
    )


def run(*, states: str, fuel_type: str = "ELEC", ev_charging_level: str | None = "dc_fast",
        max_records: int = 200, api_key: str | None = None, dry_run: bool = False,
        request_delay_seconds: float = 1.0) -> Path:
    """Fetch AFDC stations for the given states and write a dated, immutable snapshot.

    `max_records` caps total records fetched (paginated `MAX_LIMIT_PER_REQUEST` at a time) — stay
    well under DEMO_KEY's 10-requests/hour ceiling unless `api_key` is a real key.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = api_key or os.environ.get("NREL_API_KEY", DEFAULT_API_KEY)
    if key == DEFAULT_API_KEY:
        logger.warning("using DEMO_KEY (10 requests/hour) — get a free real key at "
                        "https://developer.nlr.gov/signup and set NREL_API_KEY for a real pull")

    observed_at = dt.datetime.now(dt.timezone.utc)
    stations: list[NrelStation] = []
    offset = 0
    total_results: int | None = None

    while len(stations) < max_records:
        page_limit = min(MAX_LIMIT_PER_REQUEST, max_records - len(stations))
        logger.info("fetching offset=%d limit=%d", offset, page_limit)
        payload = _fetch_page(api_key=key, states=states, offset=offset, limit=page_limit,
                               fuel_type=fuel_type, ev_charging_level=ev_charging_level)

        if RAW_CACHE_DIR.exists() or not dry_run:
            RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (RAW_CACHE_DIR / f"page_{dt.date.today().isoformat()}_offset{offset}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )

        total_results = payload.get("total_results", total_results)
        page = payload.get("fuel_stations", [])
        if not page:
            break

        for raw in page:
            stations.append(_parse_station(raw, observed_at))

        offset += len(page)
        if total_results is not None and offset >= total_results:
            break
        if len(stations) < max_records:
            time.sleep(request_delay_seconds)

    logger.info("collected %d of %s matching stations (states=%s)", len(stations), total_results, states)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{dt.date.today().isoformat()}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"{snapshot_path} already exists — snapshots are immutable, delete it manually "
            "if you really mean to replace today's snapshot."
        )

    if not dry_run:
        snapshot_path.write_text(
            json.dumps([s.model_dump(mode="json") for s in stations], indent=2), encoding="utf-8"
        )
        logger.info("wrote snapshot: %s", snapshot_path)
    else:
        logger.info("dry run — snapshot not written (would be %s)", snapshot_path)

    return snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", default="NV,UT,CT,NY,NJ,PA,WV,FL,CA,AL,WA,WI",
                         help="comma-separated state codes (default: states VOLTERRA has real Tesla sites in)")
    parser.add_argument("--max-records", type=int, default=200)
    parser.add_argument("--fuel-type", default="ELEC")
    parser.add_argument("--ev-charging-level", default="dc_fast",
                         help="dc_fast / level2 / level1, or omit for all levels")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(states=args.states, fuel_type=args.fuel_type, ev_charging_level=args.ev_charging_level,
        max_records=args.max_records, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
