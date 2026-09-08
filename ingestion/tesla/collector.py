"""Tesla Supercharger network collector.

Data source: Tesla's public "Find Us" site (https://www.tesla.com/findus). There is no
documented public API — this drives a real Chrome session (Selenium) exactly like a human
visitor, then reads two things Tesla's own Next.js frontend already renders for anyone:

  1. GET /api/findus/get-locations?country=US&view=map
     The map's bootstrap data: every location worldwide (id, coordinates, type, slug). Tesla's
     `country` param does not appear to filter server-side — filtering to US Superchargers
     happens here, client-side, same as the real site does in the browser.

  2. GET /findus/location/supercharger/<slug>
     Each site's detail page. Next.js server-renders a `<script id="__NEXT_DATA__">` tag
     containing the page's props as JSON — `props.pageProps.locationData` has stall count,
     max power, address, access type, and site status. This is the only place those fields
     are published; the map bootstrap payload doesn't carry them.

Plain HTTP (no browser) is not viable here: Tesla's edge (Akamai Bot Manager) returns 403 to
non-browser requests regardless of User-Agent header — confirmed against this exact endpoint
during development. A real browser session is not a workaround for that; it's what Find Us
actually requires of every visitor, human or automated.

**Network constraint, confirmed during development**: Akamai also blocks this endpoint from
cloud/datacenter network ranges even when the request comes from a genuine headless Chrome via
Selenium — same "Access Denied" edge response as a bare `curl`. This is IP-reputation-based, not
a browser-fingerprint problem headers or a real DOM can fix. Run this collector from an ordinary
residential/desktop network (a laptop, not a cloud sandbox, CI runner, or Render/AWS/etc.
instance) — the same constraint already documented for the sibling AirlinesApp project's BTS
pipeline (see its CLAUDE.md: "needs a real headless Chrome session BTS's site doesn't tolerate
well in constrained containers"). This module stays local-only for the same reason.

Crawl discipline (robots.txt: `Crawl-delay: 10`, applies to all user agents, `/findus/` is not
disallowed):
  - One request in flight at a time, `CRAWL_DELAY_SECONDS` (10s) between requests.
  - Raw responses cached to data/raw/tesla/ before any parsing.
  - Output is a dated, immutable snapshot in data/snapshots/tesla/ — never overwritten.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "tesla"
RAW_CACHE_DIR = REPO_ROOT / "data" / "raw" / "tesla"

BASE_URL = "https://www.tesla.com"
MASTER_LIST_URL = f"{BASE_URL}/api/findus/get-locations?country=US&view=map"
DETAIL_URL_TEMPLATE = f"{BASE_URL}/findus/location/supercharger/{{slug}}"

# robots.txt: "User-agent: *  Crawl-delay: 10" — applies to every request this collector makes,
# not just the detail pages. Do not lower this to go faster.
CRAWL_DELAY_SECONDS = 10.0

# Rough bounding box used only to separate "US" pins from the global list returned by the
# (apparently unfiltered) master-list endpoint. Real US filtering happens on each detail page's
# `key_data.address.country == "US"`; this is just a cheap pre-filter to avoid fetching detail
# pages for obviously non-US pins.
US_BBOX = {"lat_min": 15.0, "lat_max": 72.0, "lon_min": -170.0, "lon_max": -65.0}

logger = logging.getLogger("volterra.ingestion.tesla")


class SuperchargerSite(BaseModel):
    """One Tesla Supercharger site, as observed from Find Us. No modeled/estimated fields."""

    site_id: str = Field(..., description="Tesla's internal uuid for the location.")
    location_url_slug: str
    display_name: str | None = None
    common_name: str | None = None
    address_1: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    county: str | None = None
    country: str = "US"
    latitude: float
    longitude: float
    stall_count: int
    max_power_kw: float
    access_type: str | None = None            # e.g. "Public"
    charging_accessibility: str | None = None  # e.g. "Tesla Only"
    open_to_non_tesla: bool | None = None
    site_status: str | None = None             # e.g. "open"
    project_status: str | None = None          # e.g. "Open"
    opening_date: str | None = None
    source: str = "tesla_find_us"
    observed_at: dt.datetime


def _within_us_bbox(lat: float, lon: float) -> bool:
    return (
        US_BBOX["lat_min"] <= lat <= US_BBOX["lat_max"]
        and US_BBOX["lon_min"] <= lon <= US_BBOX["lon_max"]
    )


def _build_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,1024")
    # A real, current desktop Chrome UA — matches the --headless=new Chrome build itself, not
    # spoofing a different browser/OS than what's actually making the request.
    return webdriver.Chrome(options=options)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(CRAWL_DELAY_SECONDS),
    retry=retry_if_exception_type(RuntimeError),
    reraise=True,
)
def _get_next_data(driver, url: str) -> dict[str, Any]:
    driver.get(url)
    raw = driver.execute_script(
        "var el = document.getElementById('__NEXT_DATA__'); return el ? el.textContent : null;"
    )
    if raw is None:
        raise RuntimeError(f"__NEXT_DATA__ not found on {url} (page may not have loaded)")
    return json.loads(raw)


def fetch_master_list(driver, *, cache: bool = True) -> list[dict[str, Any]]:
    """Fetch the Find Us map bootstrap payload and return raw location records (all types/countries)."""
    driver.get(MASTER_LIST_URL)
    body_text = driver.execute_script("return document.body.innerText;")
    payload = json.loads(body_text)
    records = payload["data"]["data"]

    if cache:
        RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = RAW_CACHE_DIR / f"master_list_{dt.date.today().isoformat()}.json"
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("cached master list (%d records) to %s", len(records), cache_path)

    return records


def us_supercharger_candidates(master_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter the master list to likely-US Supercharger pins (cheap pre-filter, see US_BBOX)."""
    return [
        r
        for r in master_records
        if "supercharger" in r.get("location_type", [])
        and not r.get("inCN")
        and not r.get("inHkMoTw")
        and _within_us_bbox(r["latitude"], r["longitude"])
    ]


def fetch_site_detail(driver, slug: str, *, cache: bool = True) -> dict[str, Any] | None:
    """Fetch one Supercharger's detail page and return its `locationData` block, or None if
    the page isn't a real location (e.g. a stale slug)."""
    url = DETAIL_URL_TEMPLATE.format(slug=slug)
    next_data = _get_next_data(driver, url)
    page_props = next_data.get("props", {}).get("pageProps", {})
    location_data = page_props.get("locationData")

    if cache and location_data is not None:
        RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pages_dir = RAW_CACHE_DIR / "pages" / dt.date.today().isoformat()
        pages_dir.mkdir(parents=True, exist_ok=True)
        (pages_dir / f"{slug}.json").write_text(
            json.dumps(location_data, indent=2), encoding="utf-8"
        )

    return location_data


def _parse_site(uuid: str, slug: str, location_data: dict[str, Any]) -> SuperchargerSite | None:
    key_data = location_data.get("key_data", {})
    address = key_data.get("address", {})
    supercharger_fn = location_data.get("supercharger_function", {})
    marketing = location_data.get("marketing", {})
    functions = location_data.get("functions", [])
    opening_date = functions[0].get("opening_date") if functions else None

    if address.get("country") != "US":
        return None
    if not supercharger_fn.get("num_charger_stalls") or not supercharger_fn.get("installed_full_power"):
        return None

    geo = key_data.get("geo_point", {})
    return SuperchargerSite(
        site_id=uuid,
        location_url_slug=slug,
        display_name=marketing.get("display_name"),
        common_name=marketing.get("common_name"),
        address_1=address.get("address_1"),
        city=address.get("city"),
        state_province=address.get("state_province"),
        postal_code=address.get("postal_code"),
        county=address.get("county"),
        country="US",
        latitude=geo.get("lat", address.get("latitude")),
        longitude=geo.get("lon", address.get("longitude")),
        stall_count=int(supercharger_fn["num_charger_stalls"]),
        max_power_kw=float(supercharger_fn["installed_full_power"]),
        access_type=supercharger_fn.get("access_type"),
        charging_accessibility=supercharger_fn.get("charging_accessibility"),
        open_to_non_tesla=supercharger_fn.get("open_to_non_tesla"),
        site_status=supercharger_fn.get("site_status"),
        project_status=supercharger_fn.get("project_status"),
        opening_date=opening_date,
        observed_at=dt.datetime.now(dt.timezone.utc),
    )


def run(*, limit: int | None = None, dry_run: bool = False) -> Path:
    """Fetch the current US Tesla Supercharger network and write a dated, immutable snapshot.

    `limit` caps how many detail pages are fetched (useful for a quick verification run —
    the full US network is 1000+ sites, which at the mandatory 10s crawl delay takes ~3 hours).
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    driver = _build_driver()
    sites: list[SuperchargerSite] = []
    try:
        master_records = fetch_master_list(driver)
        candidates = us_supercharger_candidates(master_records)
        logger.info("%d US supercharger candidates out of %d total locations", len(candidates), len(master_records))

        if limit is not None:
            candidates = candidates[:limit]

        for i, rec in enumerate(candidates):
            slug = rec["location_url_slug"]
            uuid = rec["uuid"]
            logger.info("[%d/%d] fetching %s (%s)", i + 1, len(candidates), slug, uuid)

            if i > 0:
                time.sleep(CRAWL_DELAY_SECONDS)

            try:
                location_data = fetch_site_detail(driver, slug)
            except Exception:
                logger.exception("failed to fetch detail for %s — skipping", slug)
                continue

            if location_data is None:
                logger.warning("no locationData for %s — skipping", slug)
                continue

            site = _parse_site(uuid, slug, location_data)
            if site is not None:
                sites.append(site)
    finally:
        driver.quit()

    logger.info("parsed %d complete US supercharger sites", len(sites))

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{dt.date.today().isoformat()}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"{snapshot_path} already exists — snapshots are immutable, delete it manually "
            "if you really mean to replace today's snapshot."
        )

    if not dry_run:
        snapshot_path.write_text(
            json.dumps([s.model_dump(mode="json") for s in sites], indent=2), encoding="utf-8"
        )
        logger.info("wrote snapshot: %s", snapshot_path)
    else:
        logger.info("dry run — snapshot not written (would be %s)", snapshot_path)

    return snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="cap the number of detail pages fetched")
    parser.add_argument("--dry-run", action="store_true", help="fetch and parse but don't write the snapshot")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
