"""OpenEI U.S. Utility Rate Database (URDB) demand-charge collector.

Data source: NREL/OpenEI's public "Utility Rates" REST API (`api.openei.org/utility_rates`) — the
canonical, real, publicly documented archive of actual utility tariffs (rate name, utility,
approval status, source PDF/URL, and the real rate structure — energy $/kWh AND demand $/kW).
Same `api-umbrella` gateway signature and `DEMO_KEY` (10 requests/hour) story as NREL/EIA — see
`nrel.py`/`eia.py`.

**Why this matters for VOLTERRA specifically, not just "more real data"**: DC fast charging's
dominant real-world operating cost is often the demand charge (a per-kW-peak monthly fee), not the
per-kWh energy price already ingested in Phase 6 (`eia.py`) — a well-known pain point in the real
EV charging industry, and exactly what `optimization/`'s Phase 7 grid-aware work needs a real
number for. `eia.py`'s state-level average commercial rate has no demand-charge component at all.

**Per-site, not per-state.** Unlike EIA's price data, URDB rates are genuinely utility- and
often feeder-class-specific, and the API supports a real lat/lon + radius lookup — so this
collector queries each real Tesla site's own coordinates directly (`sector=Commercial`), rather
than approximating with a state average. Confirmed via a real example: the real Chino Hills, CA
site resolves to a real Southern California Edison "TOU-GS-3" commercial demand-metered tariff
with real on-peak/off-peak/partial-peak demand rates ($11.24-$17.13/kW/month) sourced from SCE's
own published rate sheet.

**Rate structure parsing.** A rate's demand charge lives in `demandratestructure` (time-of-use,
one list of tiers per period) or `flatdemandstructure` (a flat monthly $/kW charge, no TOU
splits) — never both meaningfully populated at once in practice. This collector takes the MAX real
$/kW value across whichever structure is present: DC fast charging's whole problem is peaky load,
so the worst-case (on-peak) real rate is the economically relevant one for a facility-sizing
decision, not an average that would understate real peak-demand risk.

**DEMO_KEY's 10/hour ceiling means partial coverage per run, same as nrel.py/eia.py.** With 17
real sites and one request each, a single run will not complete the full network on DEMO_KEY;
get a free real key at https://openei.org/services/api/signup/ and set OPENEI_API_KEY for a full
pull. Re-running (a new day's snapshot) picks up more sites over time.
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
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "openei"
RAW_CACHE_DIR = REPO_ROOT / "data" / "raw" / "openei"

BASE_URL = "https://api.openei.org/utility_rates"
DEFAULT_API_KEY = "DEMO_KEY"
SEARCH_RADIUS_MILES = 20

logger = logging.getLogger("volterra.ingestion.openei")


class GridDemandCharge(BaseModel):
    """One real site's nearest applicable commercial demand-charge tariff. Real OpenEI/URDB
    observation (a published, approved utility rate), not modeled — see docs/data-sources.md.
    `max_demand_charge_usd_per_kw` is the max real $/kW value found across the rate's real
    structure (TOU tiers or flat) — see module docstring for why max, not average."""

    site_id: str
    utility_name: str
    rate_name: str
    max_demand_charge_usd_per_kw: float
    sector: str
    openei_label: str
    source_url: str | None = None
    source: str = "openei_urdb"
    observed_at: dt.datetime


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(httpx.HTTPError))
def _fetch(*, api_key: str, latitude: float, longitude: float) -> dict[str, Any]:
    params: dict[str, Any] = {
        "version": 8,
        "format": "json",
        "api_key": api_key,
        "lat": latitude,
        "lon": longitude,
        "radius": SEARCH_RADIUS_MILES,
        "sector": "Commercial",
        "limit": 10,
        "detail": "full",
    }
    resp = httpx.get(BASE_URL, params=params, timeout=30)
    remaining = resp.headers.get("X-Ratelimit-Remaining")
    if remaining is not None:
        logger.info("rate limit remaining: %s / %s", remaining, resp.headers.get("X-Ratelimit-Limit"))
    resp.raise_for_status()
    return resp.json()


def _max_rate(structure: list[list[dict[str, Any]]] | None) -> float | None:
    """Max real $/kW value across a demandratestructure/flatdemandstructure's nested tiers."""
    if not structure:
        return None
    rates = [tier["rate"] for period in structure for tier in period if "rate" in tier]
    return max(rates) if rates else None


def _pick_best_rate(payload: dict[str, Any]) -> dict[str, Any] | None:
    """First approved Commercial rate in the response with a usable real demand-rate structure —
    OpenEI returns rates ordered by relevance, and most nearby results are either not approved,
    not sector=Commercial (radius search isn't sector-exact), or energy-only with no demand
    component at all (common for smaller accounts) — skip those rather than misrepresenting them."""
    for rate in payload.get("items", []):
        if not rate.get("approved") or rate.get("sector") != "Commercial":
            continue
        if _max_rate(rate.get("demandratestructure")) or _max_rate(rate.get("flatdemandstructure")):
            return rate
    return None


def fetch_demand_charge_for_site(site_id: str, latitude: float, longitude: float,
                                  api_key: str, observed_at: dt.datetime) -> GridDemandCharge | None:
    payload = _fetch(api_key=api_key, latitude=latitude, longitude=longitude)

    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_CACHE_DIR / f"{dt.date.today().isoformat()}_{site_id}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    rate = _pick_best_rate(payload)
    if rate is None:
        logger.warning("no usable Commercial demand-rate found near site %s — skipping", site_id)
        return None

    max_rate = _max_rate(rate.get("demandratestructure")) or _max_rate(rate.get("flatdemandstructure"))
    return GridDemandCharge(
        site_id=site_id,
        utility_name=rate.get("utility", "unknown"),
        rate_name=rate.get("name", "unknown"),
        max_demand_charge_usd_per_kw=max_rate,
        sector=rate.get("sector", "Commercial"),
        openei_label=rate["label"],
        source_url=rate.get("uri"),
        observed_at=observed_at,
    )


def run(*, sites: list[tuple[str, float, float]], api_key: str | None = None,
        dry_run: bool = False, request_delay_seconds: float = 1.0) -> Path:
    """Fetch the real nearest commercial demand-charge tariff for each (site_id, lat, lon) and
    write a dated, immutable snapshot. Per-site failures (including DEMO_KEY exhaustion mid-run)
    are logged and skipped, not fatal — same pattern as noaa.py."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = api_key or os.environ.get("OPENEI_API_KEY", DEFAULT_API_KEY)
    if key == DEFAULT_API_KEY:
        logger.warning("using DEMO_KEY (10 requests/hour) — get a free real key at "
                        "https://openei.org/services/api/signup/ and set OPENEI_API_KEY for a full pull")

    observed_at = dt.datetime.now(dt.timezone.utc)
    charges: list[GridDemandCharge] = []

    for i, (site_id, lat, lon) in enumerate(sites):
        logger.info("[%d/%d] fetching demand charge for site %s (%.4f, %.4f)", i + 1, len(sites), site_id, lat, lon)
        if i > 0:
            time.sleep(request_delay_seconds)

        try:
            charge = fetch_demand_charge_for_site(site_id, lat, lon, key, observed_at)
            if charge:
                charges.append(charge)
        except Exception:
            logger.exception("failed to fetch demand charge for site %s — skipping", site_id)
            continue

    logger.info("collected %d of %d sites' real demand charges", len(charges), len(sites))

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{dt.date.today().isoformat()}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"{snapshot_path} already exists — snapshots are immutable, delete it manually "
            "if you really mean to replace today's snapshot."
        )

    if not dry_run:
        snapshot_path.write_text(
            json.dumps([c.model_dump(mode="json") for c in charges], indent=2), encoding="utf-8"
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
        sites = con.execute("SELECT site_id, latitude, longitude FROM charging_site ORDER BY name").fetchall()
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
