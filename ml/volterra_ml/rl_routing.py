"""Phase 10: RL charging-guidance agent, evaluated against Phase 9's static SOC-feasible shortest
path (the "mathematical optimizer" baseline named in the roadmap — technically a routing
algorithm, not the MILP; see below for why that's still the right comparison) and a greedy
shortest-time baseline, per the root CLAUDE.md discipline that RL is evaluated against real
baselines, never presented standalone.

**Why RL has a legitimate edge here, rather than trying to "beat" a provably optimal algorithm.**
Phase 9's `soc_feasible_shortest_path` is provably optimal for a STATIC, fully-known network — but
it commits to a full route once, at trip start, using each site's long-run BASELINE congestion
assumption. It cannot react to the fact that a specific real site is unusually busy TODAY. This
module's environment gives the RL agent (and a greedy baseline) something Phase 9's static planner
structurally cannot use: an observed, per-episode, per-site REALIZED congestion level at decision
time — closer to a real driver checking a live trip-planning app before committing to the next
leg than to Phase 9's offline planning problem. The research question isn't "can RL beat an exact
algorithm at its own game" (a category error), it's "does reacting to real-time information beat
committing to a plan blind to it, and does a farsighted learned policy beat a myopic reactive one."

**Real, not invented, congestion signal.** Realized congestion is expressed as a multiplier on
`BASELINE_UTILIZATION` fed into `queue_risk.py`'s already-validated, real LightGBM surrogate for
`../simulation/`'s M/G/c queueing model — reusing real, tested infrastructure rather than building
a third independent congestion model (after the Julia M/G/c model and its LightGBM surrogate).
Site topology, real distances (haversine x a modeled circuity factor), and the real charging-curve
model are ported directly from `../../graph/src/geo.jl`, `../../routing/src/soc_path.jl`, and
`../../simulation/src/charging_curve.jl` respectively — same formulas, same constants, not
re-derived — because no Python<->Julia bridge exists in this project (see `backend/internal/
mlpredictions`'s doc comment for the same constraint from the other language direction) and this
environment needs microsecond-scale queries for RL training, which a live Julia process per step
would not give.

**Scope**: trained and evaluated over the same real 4-site Las Vegas <-> Nephi, UT corridor
Phase 9's demos already used and validated (not the full 17-site network — most of those sites are
geographically isolated from each other at any real threshold, so a national-scale RL routing
problem would need a materially different, larger real network first). Real: site coordinates,
stall counts, `max_power_kw`. Modeled: the per-episode congestion-multiplier distribution itself
(Tesla publishes no real per-site occupancy time series to ground this) and everything already
modeled in `routing/`'s SOC-feasible path (circuity factor, highway speed, SOC conventions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "volterra.duckdb"

# --- Real geography (ported from graph/src/geo.jl) -------------------------------------------

EARTH_RADIUS_MI = 3958.8


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# --- Modeled driving conventions (matches routing/src/soc_path.jl exactly, for consistency) ----

CIRCUITY_FACTOR = 1.2
AVG_HIGHWAY_SPEED_MPH = 65.0
SOC_BUCKET_STEP = 0.05
MIN_SOC = 0.05
ORIGIN_SOC = 0.90
CHARGE_TARGET_SOC_MAX = 0.80

# --- Real charging-curve model (ported from simulation/src/charging_curve.jl) ------------------

TAPER_BREAKPOINT_SOC = 0.5
TAPER_FLOOR_FRACTION = 0.2
COLD_DERATE_FLOOR_TEMP_C = -20.0
COLD_DERATE_START_TEMP_C = 10.0
COLD_DERATE_FLOOR_FRACTION = 0.5


def charging_power_fraction(soc: float) -> float:
    if soc <= TAPER_BREAKPOINT_SOC:
        return 1.0
    frac_into_taper = (soc - TAPER_BREAKPOINT_SOC) / (1.0 - TAPER_BREAKPOINT_SOC)
    return 1.0 - frac_into_taper * (1.0 - TAPER_FLOOR_FRACTION)


def temperature_derate(temp_c: float) -> float:
    if temp_c >= COLD_DERATE_START_TEMP_C:
        return 1.0
    if temp_c <= COLD_DERATE_FLOOR_TEMP_C:
        return COLD_DERATE_FLOOR_FRACTION
    frac = (temp_c - COLD_DERATE_FLOOR_TEMP_C) / (COLD_DERATE_START_TEMP_C - COLD_DERATE_FLOOR_TEMP_C)
    return COLD_DERATE_FLOOR_FRACTION + frac * (1.0 - COLD_DERATE_FLOOR_FRACTION)


def charge_duration_hours(soc_start: float, soc_target: float, battery_kwh: float,
                           rated_power_kw: float, *, temp_c: float = 20.0, n_steps: int = 200) -> float:
    assert soc_target > soc_start, f"soc_target ({soc_target}) must exceed soc_start ({soc_start})"
    derate = temperature_derate(temp_c)
    step = (soc_target - soc_start) / n_steps
    hours = 0.0
    for i in range(n_steps):
        soc_mid = soc_start + (i + 0.5) * step
        power_kw = rated_power_kw * charging_power_fraction(soc_mid) * derate
        power_kw = max(power_kw, 1.0)
        hours += (battery_kwh * step) / power_kw
    return hours


def driving_distance_mi(a: "RealSite", b: "RealSite") -> float:
    return haversine_miles(a.latitude, a.longitude, b.latitude, b.longitude) * CIRCUITY_FACTOR


def soc_bucket(soc: float) -> int:
    return math.floor(soc / SOC_BUCKET_STEP + 1e-9)


def bucket_soc(bucket: int) -> float:
    return bucket * SOC_BUCKET_STEP


# --- Real network -------------------------------------------------------------------------------

BASELINE_UTILIZATION = 0.65  # MODELED, mirrors capacity_selection.jl's convention throughout this project

CORRIDOR_NAME_FILTERS = ["Las Vegas", "St. George", "Beaver", "Nephi"]


@dataclass(frozen=True)
class RealSite:
    site_id: str
    name: str
    stall_count: int
    max_power_kw: float
    latitude: float
    longitude: float


def load_corridor_sites(warehouse_path: Path = DEFAULT_WAREHOUSE_PATH) -> list[RealSite]:
    """Reads the same real Las Vegas / St. George / Beaver / Nephi corridor sites Phase 9's demos
    already used and validated -- see this module's docstring for why the RL agent trains on this
    real sub-network rather than the full 17-site graph."""
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT site_id, name, stall_count, max_power_kw, latitude, longitude FROM charging_site"
        ).fetchall()
    finally:
        con.close()

    sites = [RealSite(*row) for row in rows]
    matched = [s for s in sites if any(f in s.name for f in CORRIDOR_NAME_FILTERS)]
    if len(matched) != len(CORRIDOR_NAME_FILTERS):
        raise RuntimeError(
            f"expected {len(CORRIDOR_NAME_FILTERS)} real corridor sites, found {len(matched)}: "
            f"{[s.name for s in matched]}"
        )
    return matched


@dataclass(frozen=True)
class RealVehicle:
    battery_kwh: float
    consumption_kwh_per_mile: float


def build_corridor_graph(sites: list[RealSite], threshold_mi: float = 250.0) -> dict[str, list[str]]:
    """Real proximity-threshold edge topology, matching graph/src/resilience_graph.jl's rule
    exactly (undirected, connect if real great-circle distance <= threshold_mi)."""
    adjacency: dict[str, list[str]] = {s.site_id: [] for s in sites}
    for i, a in enumerate(sites):
        for b in sites[i + 1:]:
            d = haversine_miles(a.latitude, a.longitude, b.latitude, b.longitude)
            if d <= threshold_mi:
                adjacency[a.site_id].append(b.site_id)
                adjacency[b.site_id].append(a.site_id)
    return adjacency
