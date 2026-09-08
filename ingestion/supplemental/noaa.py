"""NOAA National Weather Service forecast collector.

Data source: the NWS API (api.weather.gov) — genuinely public, no API key or account required at
all (unlike NOAA's other API, Climate Data Online / CDO, which needs a free registered token —
tried that first, this is the keyless alternative). Real-time forecast data, not a historical
climate archive: this collector pulls the current multi-day forecast (temperature per day/night
period) for each real charging site, not "typical January temperature" style climate normals.

**Two calls per site, no bulk endpoint.** `/points/{lat},{lon}` resolves a coordinate to its
forecast office + grid cell; `/gridpoints/{office}/{x},{y}/forecast` returns that cell's current
forecast. NWS has no documented per-key rate limit (it's built for public/high-volume use, unlike
NREL/EIA's `DEMO_KEY`), but it does *require* an identifying `User-Agent` header with contact
info per its own docs — this collector sets one and paces requests as a courtesy regardless.

Feeds real temperature data into `../../simulation/src/charging_curve.jl`'s `temperature_derate`
function, which previously only ever saw a hardcoded 20.0°C — see
`warehouse/verify/temperature_scenarios.jl` for a real seasonal/regional queueing comparison this
enables (e.g. real forecast cold at a WA/WI site vs. real forecast heat at a FL/NV site).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "noaa"
RAW_CACHE_DIR = REPO_ROOT / "data" / "raw" / "noaa"

# NWS API docs ask for a real identifying User-Agent, not a generic library default. Must be
# ASCII-only -- httpx encodes headers as ASCII by default and raises on anything outside it
# (confirmed by reproduction: an em-dash here broke every single request with UnicodeEncodeError).
USER_AGENT = "VOLTERRA-research/0.1 (github.com, EV charging network research project)"
REQUEST_DELAY_SECONDS = 1.0  # courtesy pacing -- NWS publishes no hard per-key limit to respect

logger = logging.getLogger("volterra.ingestion.noaa")


class ForecastPeriod(BaseModel):
    """One real NWS forecast period (~12h day/night block) for one site. Real observation, not
    modeled — see docs/data-sources.md. Live forecast data, not a historical climate normal."""

    site_id: str
    period_name: str          # e.g. "Tonight", "Friday"
    start_time: dt.datetime
    temperature_f: float
    temperature_unit: str
    short_forecast: str
    source: str = "noaa_nws"
    observed_at: dt.datetime


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(httpx.HTTPError))
def _get(url: str) -> dict[str, Any]:
    # follow_redirects=True is required, not cosmetic: /points/{lat},{lon} always 301s to NWS's
    # own canonicalized 4-decimal-place coordinate (e.g. 37.1272069,-113.6033535 -> 37.1272,
    # -113.6034), and httpx does not follow redirects by default (unlike requests) -- confirmed by
    # reproduction, every single request in the first real run failed with HTTPStatusError on the
    # bare 301 before this was added.
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def fetch_forecast_for_site(site_id: str, latitude: float, longitude: float) -> list[ForecastPeriod]:
    """Real two-step NWS lookup: coordinate -> grid cell -> forecast periods. Caches both raw
    responses before parsing."""
    point = _get(f"https://api.weather.gov/points/{latitude},{longitude}")
    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_CACHE_DIR / f"{dt.date.today().isoformat()}_{site_id}_point.json").write_text(
        json.dumps(point, indent=2), encoding="utf-8"
    )

    forecast = _get(point["properties"]["forecast"])
    (RAW_CACHE_DIR / f"{dt.date.today().isoformat()}_{site_id}_forecast.json").write_text(
        json.dumps(forecast, indent=2), encoding="utf-8"
    )

    observed_at = dt.datetime.now(dt.timezone.utc)
    return [
        ForecastPeriod(
            site_id=site_id,
            period_name=p["name"],
            start_time=p["startTime"],
            temperature_f=float(p["temperature"]),
            temperature_unit=p["temperatureUnit"],
            short_forecast=p["shortForecast"],
            observed_at=observed_at,
        )
        for p in forecast["properties"]["periods"]
    ]


def run(*, sites: list[tuple[str, float, float]], dry_run: bool = False) -> Path:
    """Fetch the current NWS forecast for each (site_id, latitude, longitude) and write a dated,
    immutable snapshot. `sites` is small by nature — this is live forecast data, not a bulk
    historical pull, so re-running daily (not accumulating a giant one-time archive) is the
    intended usage pattern."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    all_periods: list[ForecastPeriod] = []

    for i, (site_id, lat, lon) in enumerate(sites):
        logger.info("[%d/%d] fetching forecast for site %s (%.4f, %.4f)", i + 1, len(sites), site_id, lat, lon)
        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)

        try:
            all_periods.extend(fetch_forecast_for_site(site_id, lat, lon))
        except Exception:
            logger.exception("failed to fetch forecast for site %s — skipping", site_id)
            continue

    logger.info("collected %d forecast periods across %d sites", len(all_periods), len(sites))

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{dt.date.today().isoformat()}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"{snapshot_path} already exists — snapshots are immutable, delete it manually "
            "if you really mean to replace today's snapshot."
        )

    if not dry_run:
        snapshot_path.write_text(
            json.dumps([p.model_dump(mode="json") for p in all_periods], indent=2), encoding="utf-8"
        )
        logger.info("wrote snapshot: %s", snapshot_path)
    else:
        logger.info("dry run — snapshot not written (would be %s)", snapshot_path)

    return snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-warehouse", action="store_true",
                         help="pull site_id/latitude/longitude from the warehouse's charging_site table")
    parser.add_argument("--site", action="append", default=[],
                         help="site_id,lat,lon triple; repeatable. Alternative to --from-warehouse")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.from_warehouse:
        import duckdb
        warehouse_path = REPO_ROOT / "warehouse" / "volterra.duckdb"
        con = duckdb.connect(str(warehouse_path), read_only=True)
        sites = con.execute("SELECT site_id, latitude, longitude FROM charging_site").fetchall()
        con.close()
    else:
        sites = []
        for s in args.site:
            site_id, lat, lon = s.split(",")
            sites.append((site_id, float(lat), float(lon)))

    if not sites:
        raise SystemExit("no sites given — use --from-warehouse or one or more --site site_id,lat,lon")

    run(sites=sites, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
