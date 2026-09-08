"""FHWA Alternative Fuel Corridors collector.

Data source: FHWA's officially designated Alternative Fuel Corridors (23 U.S.C. 151) — highway
segments along the National Highway System designated "Corridor Ready" or "Corridor Pending" for
EV charging (and separately CNG/LNG/LPG/hydrogen). Published as part of USDOT/BTS's National
Transportation Atlas Database (NTAD), served as a public ArcGIS Feature Service — no API key, no
rate limit hit during development (unlike NREL's DEMO_KEY), real LineString geometry per segment.

**Found the same way Tesla's and NREL's real endpoints were found: by following the live page,
not trusting a doc.** FHWA's own site (https://www.fhwa.dot.gov/environment/alternative_fuel_corridors/maps/)
just points to an ArcGIS Hub page; that page's actual data lives in ArcGIS Online's public item
catalog. The concrete path: search https://www.arcgis.com/sharing/rest/search for "Alternative
Fuel Corridors", find the authoritative one (owner `USDOT_BTS`, title matches the FHWA program
exactly, part of NTAD), fetch its item metadata for the real `url`
(https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_Alternative_Fuel_Corridors/FeatureServer),
then query layer 0 directly. Community-published corridor layers (state DOTs, individual
contributors) show up in the same search — this collector intentionally hardcodes the
`USDOT_BTS`-owned service, not a fuzzy search match, so it never silently picks up an
unauthoritative one.

**Public domain.** Per the item's `licenseInfo`: "a work of the United States government... not
protected by any U.S. copyrights... available for unrestricted public use."

This is exactly the real corridor topology `graph/`'s Phase 5 resilience analysis used a
geographic-proximity *proxy* for, absent real data (see graph/README.md) — e.g. it can now
confirm I-15 through NV/UT (VOLTERRA's Las Vegas / St. George / Beaver / Nephi corridor) is a
real FHWA "Signage Ready" EV corridor, Round 1 designation, not just four points that happen to
be near each other.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "fhwa"
RAW_CACHE_DIR = REPO_ROOT / "data" / "raw" / "fhwa"

# Resolved once via https://www.arcgis.com/sharing/rest/search?q=Alternative+Fuel+Corridors — see
# module docstring. If this ever 404s, re-resolve via that search rather than guessing a new URL.
QUERY_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "NTAD_Alternative_Fuel_Corridors/FeatureServer/0/query"
)
MAX_RECORD_COUNT = 2000  # server-reported ceiling per request (layer metadata's maxRecordCount)

OUT_FIELDS = [
    "OBJECTID", "AFC_NUMBER", "ROADTYPE", "NUMBER", "PRIMARY_NA", "ALT_NAME_1", "ALT_NAME_2",
    "LOCAL_NAME", "STATE", "EV_ROUND", "ELECTRICVE", "Len_miles",
]

logger = logging.getLogger("volterra.ingestion.fhwa")


class FhwaCorridorSegment(BaseModel):
    """One EV-designated Alternative Fuel Corridor highway segment. Real FHWA/NTAD observation,
    not modeled — see docs/data-sources.md."""

    object_id: int                     # ArcGIS's own unique feature id -- the real natural key
    afc_number: str
    road_type: str | None = None       # 'I' (Interstate) / 'U' (US Route) / 'S' (State route)
    route_number: str | None = None
    primary_name: str | None = None    # e.g. "I15"
    alt_name_1: str | None = None
    alt_name_2: str | None = None
    local_name: str | None = None
    state: str | None = None
    ev_round: int | None = None        # which annual designation round (1-8+)
    ev_status: str | None = None       # e.g. "Signage Ready" (corridor-ready) or pending-style text
    length_miles: float | None = None
    geometry_geojson: dict[str, Any]   # GeoJSON LineString/MultiLineString, [lon, lat] pairs
    source: str = "fhwa_ntad"
    observed_at: dt.datetime


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(httpx.HTTPError))
def _query(*, where: str, result_offset: int = 0) -> dict[str, Any]:
    params = {
        "where": where,
        "outFields": ",".join(OUT_FIELDS),
        "returnGeometry": "true",
        "f": "geojson",
        "resultRecordCount": MAX_RECORD_COUNT,
        "resultOffset": result_offset,
    }
    resp = httpx.get(QUERY_URL, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _parse_segment(feature: dict[str, Any], observed_at: dt.datetime) -> FhwaCorridorSegment:
    p = feature["properties"]
    return FhwaCorridorSegment(
        object_id=p["OBJECTID"],
        afc_number=p["AFC_NUMBER"],
        road_type=p.get("ROADTYPE"),
        route_number=p.get("NUMBER"),
        primary_name=p.get("PRIMARY_NA"),
        alt_name_1=p.get("ALT_NAME_1"),
        alt_name_2=p.get("ALT_NAME_2"),
        local_name=p.get("LOCAL_NAME"),
        state=p.get("STATE"),
        ev_round=p.get("EV_ROUND"),
        ev_status=p.get("ELECTRICVE"),
        length_miles=p.get("Len_miles"),
        geometry_geojson=feature["geometry"],
        observed_at=observed_at,
    )


def run(*, states: list[str] | None = None, dry_run: bool = False) -> Path:
    """Fetch FHWA's currently-designated EV Alternative Fuel Corridor segments and write a dated,
    immutable snapshot. `states` filters to specific two-letter codes; omit for the full national
    network (994 segments as of 2026-08-27 — well under one query's maxRecordCount, so this is a
    single request, no pagination loop needed at that volume)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    observed_at = dt.datetime.now(dt.timezone.utc)

    where = "EV=1"
    if states:
        state_list = ",".join(f"'{s}'" for s in states)
        where = f"EV=1 AND STATE IN ({state_list})"

    logger.info("querying FHWA NTAD Alternative Fuel Corridors: %s", where)
    payload = _query(where=where)

    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_CACHE_DIR / f"{dt.date.today().isoformat()}.geojson").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    features = payload.get("features", [])
    segments = [_parse_segment(f, observed_at) for f in features]
    logger.info("collected %d EV corridor segments", len(segments))

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{dt.date.today().isoformat()}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"{snapshot_path} already exists — snapshots are immutable, delete it manually "
            "if you really mean to replace today's snapshot."
        )

    if not dry_run:
        snapshot_path.write_text(
            json.dumps([s.model_dump(mode="json") for s in segments], indent=2), encoding="utf-8"
        )
        logger.info("wrote snapshot: %s", snapshot_path)
    else:
        logger.info("dry run — snapshot not written (would be %s)", snapshot_path)

    return snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", default=None,
                         help="comma-separated state codes (default: full national network)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    states = args.states.split(",") if args.states else None
    run(states=states, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
