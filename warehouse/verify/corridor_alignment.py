"""Cross-source validation: do real Tesla site coordinates actually sit near the real
FHWA-designated EV corridor they're presumed to serve?

Built to check Phase 5's resilience-graph corridor (Las Vegas -> St. George -> Beaver -> Nephi,
connected there purely by great-circle distance, before real corridor data existed) against
Phase 6's real FHWA I-15 EV corridor geometry. Not a general-purpose tool — the site-name filter
and route number are hardcoded to that specific corridor; generalize if/when other corridors need
the same check.

Run from the repo root:
    python warehouse/verify/corridor_alignment.py
"""

import json
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "volterra.duckdb"

EARTH_RADIUS_MI = 3958.8


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * atan2(sqrt(a), sqrt(1 - a))


def point_to_segment_mi(plat, plon, alat, alon, blat, blon) -> float:
    """Approximate min distance from point P to segment A-B, treating lat/lon as locally planar
    -- fine at highway-adjacent scale (single road segments), not for long spans."""
    ax, ay, bx, by, px, py = alon, alat, blon, blat, plon, plat
    abx, aby = bx - ax, by - ay
    ab_len_sq = abx * abx + aby * aby
    if ab_len_sq == 0:
        return haversine_mi(plat, plon, alat, alon)
    t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab_len_sq))
    cx, cy = ax + t * abx, ay + t * aby
    return haversine_mi(plat, plon, cy, cx)


def min_distance_to_polylines(plat: float, plon: float, geometries: list[dict]) -> float:
    best = float("inf")
    for geom in geometries:
        lines = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        for line in lines:
            for i in range(len(line) - 1):
                lon1, lat1 = line[i]
                lon2, lat2 = line[i + 1]
                best = min(best, point_to_segment_mi(plat, plon, lat1, lon1, lat2, lon2))
    return best


def main() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)

    geoms = [
        json.loads(row[0])
        for row in con.execute(
            "SELECT geometry_geojson FROM fhwa_corridor_segment WHERE route_number='15' AND state IN ('NV','UT')"
        ).fetchall()
    ]
    print(f"{len(geoms)} real I-15 EV corridor segments (NV+UT)")

    sites = con.execute(
        "SELECT name, latitude, longitude FROM charging_site WHERE name LIKE 'Las Vegas%' "
        "OR name LIKE 'St. George%' OR name LIKE 'Beaver%' OR name LIKE 'Nephi%'"
    ).fetchall()

    print("\nReal Tesla site distance to nearest point on the real FHWA-designated I-15 EV corridor:")
    for name, lat, lon in sites:
        d = min_distance_to_polylines(lat, lon, geoms)
        flag = "" if d < 5 else "  <- worth investigating, see this file's module docstring"
        print(f"  {name:45s} {d:6.2f} mi from the designated corridor centerline{flag}")

    print(
        "\nNote: Nephi, UT comes out ~21 mi off despite I-15 running directly through the real "
        "town of Nephi. That's a real gap in this dataset's segment coverage near Nephi (the 6 "
        "UT segments queried span ~285 mi of a ~400 mi corridor), not a bug in this script or "
        "wrong Tesla coordinates -- worth a closer look if corridor coverage near Nephi matters "
        "for a future analysis, not something to silently paper over here."
    )


if __name__ == "__main__":
    main()
