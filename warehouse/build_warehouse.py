"""Build/incrementally update volterra.duckdb from ingestion snapshots.

Only this script ever opens the warehouse for writing — see README.md and the root CLAUDE.md.

Tesla: replays every dated snapshot in data/snapshots/tesla/ in order — each snapshot's sites go
into the immutable charging_site_snapshot history, and charging_site is upserted to the latest
known state per site.

NREL AFDC: upserts data/snapshots/nrel/*.json into nrel_station (latest-known-state only, no
immutable history table yet — see schema.sql's comment on why).

FHWA: upserts data/snapshots/fhwa/*.json into fhwa_corridor_segment (latest-known-state only,
same reasoning as NREL — corridor designations don't change often enough to need per-snapshot
history yet).

EIA: upserts data/snapshots/eia/*.json into eia_electricity_price (natural key is state/sector/
year, which is itself already a real historical series — no separate snapshot-history table
needed, re-running just fills in more (state, sector, year) rows over time).

NOAA: inserts data/snapshots/noaa/*.json into noaa_forecast, one row per (site, forecast period,
snapshot date) — unlike NREL/FHWA/EIA this is genuinely insert-only, not an upsert, since each
snapshot is a point-in-time observation of a live forecast, not a latest-known-state record (see
schema.sql's comment on noaa_forecast).

EPA: upserts data/snapshots/epa/*.json into epa_vehicle (natural key is make/model/year, which is
itself a real historical series like EIA's — re-running just fills in newer model years over time).

OpenEI: upserts data/snapshots/openei/*.json into grid_demand_charge (natural key is site_id —
one real current rate per site, latest-known-state, same pattern as EIA/EPA).

NREL ATB: upserts data/snapshots/nrel_atb/*.json into battery_storage_cost (natural key is
duration_hours/projection_year — a national technology-class baseline, not per-site).

Re-running is safe — every insert is idempotent on its natural key.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "volterra.duckdb"
SCHEMA_PATH = REPO_ROOT / "warehouse" / "schema.sql"
TESLA_SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "tesla"
NREL_SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "nrel"
FHWA_SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "fhwa"
EIA_SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "eia"
NOAA_SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "noaa"
EPA_SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "epa"
OPENEI_SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "openei"
NREL_ATB_SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots" / "nrel_atb"


def _build_tesla(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "INSERT INTO network_operator (operator_id, display_name, homepage_url) "
        "VALUES ('tesla', 'Tesla Supercharger', 'https://www.tesla.com/findus') "
        "ON CONFLICT (operator_id) DO NOTHING"
    )

    for snapshot_path in sorted(TESLA_SNAPSHOT_DIR.glob("*.json")):
        snapshot_date = snapshot_path.stem  # filename is YYYY-MM-DD.json
        sites = json.loads(snapshot_path.read_text(encoding="utf-8"))
        print(f"loading {snapshot_path.name}: {len(sites)} sites")

        for site in sites:
            # charging_site first: charging_site_snapshot has a foreign key to it.
            con.execute(
                """
                INSERT INTO charging_site (
                    site_id, operator_id, name, street_address, city, state,
                    geom, latitude, longitude, geom_source, stall_count, stall_count_source,
                    max_power_kw, max_power_kw_source, access_restricted,
                    first_seen_snapshot, last_seen_snapshot
                )
                VALUES (
                    ?, 'tesla', ?, ?, ?, ?,
                    ST_Point(?, ?), ?, ?, 'tesla_find_us', ?, 'tesla_find_us',
                    ?, 'tesla_find_us', ?,
                    ?, ?
                )
                ON CONFLICT (site_id) DO UPDATE SET
                    name = excluded.name,
                    stall_count = excluded.stall_count,
                    max_power_kw = excluded.max_power_kw,
                    access_restricted = excluded.access_restricted,
                    last_seen_snapshot = excluded.last_seen_snapshot
                """,
                [
                    site["site_id"],
                    site.get("display_name") or site.get("common_name") or site["location_url_slug"],
                    site.get("address_1"),
                    site.get("city"),
                    site.get("state_province"),
                    site["longitude"],
                    site["latitude"],
                    site["latitude"],
                    site["longitude"],
                    site["stall_count"],
                    site["max_power_kw"],
                    site.get("access_type") != "Public",
                    snapshot_date,
                    snapshot_date,
                ],
            )

            con.execute(
                """
                INSERT INTO charging_site_snapshot
                    (site_id, snapshot_date, stall_count, max_power_kw, access_restricted, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (site_id, snapshot_date) DO NOTHING
                """,
                [
                    site["site_id"],
                    snapshot_date,
                    site["stall_count"],
                    site["max_power_kw"],
                    site.get("access_type") != "Public",
                    site.get("source", "tesla_find_us"),
                ],
            )

    n_sites = con.execute("SELECT count(*) FROM charging_site").fetchone()[0]
    print(f"warehouse now has {n_sites} distinct Tesla charging sites")


def _build_nrel(con: duckdb.DuckDBPyConnection) -> None:
    for snapshot_path in sorted(NREL_SNAPSHOT_DIR.glob("*.json")):
        snapshot_date = snapshot_path.stem
        stations = json.loads(snapshot_path.read_text(encoding="utf-8"))
        print(f"loading {snapshot_path.name}: {len(stations)} NREL stations")

        for s in stations:
            con.execute(
                """
                INSERT INTO nrel_station (
                    station_id, station_name, ev_network, latitude, longitude, city, state,
                    street_address, zip_code, access_code, ev_connector_types, ev_dc_fast_num,
                    ev_level1_evse_num, ev_level2_evse_num, ev_pricing, facility_type, open_date,
                    status_code, owner_type_code, date_last_confirmed, source, snapshot_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (station_id) DO UPDATE SET
                    station_name = excluded.station_name,
                    ev_network = excluded.ev_network,
                    ev_dc_fast_num = excluded.ev_dc_fast_num,
                    ev_level1_evse_num = excluded.ev_level1_evse_num,
                    ev_level2_evse_num = excluded.ev_level2_evse_num,
                    status_code = excluded.status_code,
                    date_last_confirmed = excluded.date_last_confirmed,
                    snapshot_date = excluded.snapshot_date
                """,
                [
                    s["station_id"], s["station_name"], s.get("ev_network"),
                    s["latitude"], s["longitude"], s.get("city"), s.get("state"),
                    s.get("street_address"), s.get("zip_code"), s.get("access_code"),
                    s.get("ev_connector_types") or [], s.get("ev_dc_fast_num"),
                    s.get("ev_level1_evse_num"), s.get("ev_level2_evse_num"), s.get("ev_pricing"),
                    s.get("facility_type"), s.get("open_date"), s.get("status_code"),
                    s.get("owner_type_code"), s.get("date_last_confirmed"),
                    s.get("source", "nrel_afdc"), snapshot_date,
                ],
            )

    n_stations = con.execute("SELECT count(*) FROM nrel_station").fetchone()[0]
    print(f"warehouse now has {n_stations} NREL AFDC stations")


def _build_fhwa(con: duckdb.DuckDBPyConnection) -> None:
    for snapshot_path in sorted(FHWA_SNAPSHOT_DIR.glob("*.json")):
        snapshot_date = snapshot_path.stem
        segments = json.loads(snapshot_path.read_text(encoding="utf-8"))
        print(f"loading {snapshot_path.name}: {len(segments)} FHWA corridor segments")

        for seg in segments:
            con.execute(
                """
                INSERT INTO fhwa_corridor_segment (
                    object_id, afc_number, road_type, route_number, primary_name, alt_name_1,
                    alt_name_2, local_name, state, ev_round, ev_status, length_miles,
                    geometry_geojson, source, snapshot_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (object_id) DO UPDATE SET
                    ev_status = excluded.ev_status,
                    length_miles = excluded.length_miles,
                    geometry_geojson = excluded.geometry_geojson,
                    snapshot_date = excluded.snapshot_date
                """,
                [
                    seg["object_id"], seg["afc_number"], seg.get("road_type"),
                    seg.get("route_number"), seg.get("primary_name"), seg.get("alt_name_1"),
                    seg.get("alt_name_2"), seg.get("local_name"), seg.get("state"),
                    seg.get("ev_round"), seg.get("ev_status"), seg.get("length_miles"),
                    json.dumps(seg["geometry_geojson"]), seg.get("source", "fhwa_ntad"),
                    snapshot_date,
                ],
            )

    n_segments = con.execute("SELECT count(*) FROM fhwa_corridor_segment").fetchone()[0]
    print(f"warehouse now has {n_segments} FHWA corridor segments")


def _build_eia(con: duckdb.DuckDBPyConnection) -> None:
    for snapshot_path in sorted(EIA_SNAPSHOT_DIR.glob("*.json")):
        snapshot_date = snapshot_path.stem
        prices = json.loads(snapshot_path.read_text(encoding="utf-8"))
        print(f"loading {snapshot_path.name}: {len(prices)} EIA electricity prices")

        for p in prices:
            con.execute(
                """
                INSERT INTO eia_electricity_price (
                    state, state_description, sector_id, sector_name, period_year,
                    price_cents_per_kwh, source, snapshot_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (state, sector_id, period_year) DO UPDATE SET
                    price_cents_per_kwh = excluded.price_cents_per_kwh,
                    snapshot_date = excluded.snapshot_date
                """,
                [
                    p["state"], p.get("state_description"), p["sector_id"], p.get("sector_name"),
                    p["period_year"], p["price_cents_per_kwh"], p.get("source", "eia"), snapshot_date,
                ],
            )

    n_prices = con.execute("SELECT count(*) FROM eia_electricity_price").fetchone()[0]
    print(f"warehouse now has {n_prices} EIA electricity price rows")


def _build_noaa(con: duckdb.DuckDBPyConnection) -> None:
    for snapshot_path in sorted(NOAA_SNAPSHOT_DIR.glob("*.json")):
        snapshot_date = snapshot_path.stem
        periods = json.loads(snapshot_path.read_text(encoding="utf-8"))
        print(f"loading {snapshot_path.name}: {len(periods)} NOAA forecast periods")

        for p in periods:
            con.execute(
                """
                INSERT INTO noaa_forecast (
                    site_id, period_name, start_time, temperature_f, temperature_unit,
                    short_forecast, source, snapshot_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (site_id, period_name, start_time, snapshot_date) DO NOTHING
                """,
                [
                    p["site_id"], p["period_name"], p["start_time"], p["temperature_f"],
                    p.get("temperature_unit"), p.get("short_forecast"),
                    p.get("source", "noaa_nws"), snapshot_date,
                ],
            )

    n_periods = con.execute("SELECT count(*) FROM noaa_forecast").fetchone()[0]
    print(f"warehouse now has {n_periods} NOAA forecast period rows")


def _build_epa(con: duckdb.DuckDBPyConnection) -> None:
    for snapshot_path in sorted(EPA_SNAPSHOT_DIR.glob("*.json")):
        snapshot_date = snapshot_path.stem
        vehicles = json.loads(snapshot_path.read_text(encoding="utf-8"))
        print(f"loading {snapshot_path.name}: {len(vehicles)} EPA EV model-year variants")

        for v in vehicles:
            con.execute(
                """
                INSERT INTO epa_vehicle (
                    make, model, year, range_miles, comb_e_kwh_per_100mi, charge240_hours,
                    battery_kwh_estimate, source, snapshot_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (make, model, year) DO UPDATE SET
                    range_miles = excluded.range_miles,
                    comb_e_kwh_per_100mi = excluded.comb_e_kwh_per_100mi,
                    charge240_hours = excluded.charge240_hours,
                    battery_kwh_estimate = excluded.battery_kwh_estimate,
                    snapshot_date = excluded.snapshot_date
                """,
                [
                    v["make"], v["model"], v["year"], v.get("range_miles"),
                    v.get("comb_e_kwh_per_100mi"), v.get("charge240_hours"),
                    v.get("battery_kwh_estimate"), v.get("source", "epa_fueleconomy_gov"),
                    snapshot_date,
                ],
            )

    n_vehicles = con.execute("SELECT count(*) FROM epa_vehicle").fetchone()[0]
    print(f"warehouse now has {n_vehicles} EPA EV model-year variant rows")


def _build_openei(con: duckdb.DuckDBPyConnection) -> None:
    for snapshot_path in sorted(OPENEI_SNAPSHOT_DIR.glob("*.json")):
        snapshot_date = snapshot_path.stem
        charges = json.loads(snapshot_path.read_text(encoding="utf-8"))
        print(f"loading {snapshot_path.name}: {len(charges)} OpenEI grid demand charges")

        for c in charges:
            con.execute(
                """
                INSERT INTO grid_demand_charge (
                    site_id, utility_name, rate_name, max_demand_charge_usd_per_kw, sector,
                    openei_label, source_url, source, snapshot_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (site_id) DO UPDATE SET
                    utility_name = excluded.utility_name,
                    rate_name = excluded.rate_name,
                    max_demand_charge_usd_per_kw = excluded.max_demand_charge_usd_per_kw,
                    sector = excluded.sector,
                    openei_label = excluded.openei_label,
                    source_url = excluded.source_url,
                    snapshot_date = excluded.snapshot_date
                """,
                [
                    c["site_id"], c["utility_name"], c["rate_name"], c["max_demand_charge_usd_per_kw"],
                    c["sector"], c["openei_label"], c.get("source_url"),
                    c.get("source", "openei_urdb"), snapshot_date,
                ],
            )

    n_charges = con.execute("SELECT count(*) FROM grid_demand_charge").fetchone()[0]
    print(f"warehouse now has {n_charges} OpenEI grid demand charge rows")


def _build_nrel_atb(con: duckdb.DuckDBPyConnection) -> None:
    for snapshot_path in sorted(NREL_ATB_SNAPSHOT_DIR.glob("*.json")):
        snapshot_date = snapshot_path.stem
        costs = json.loads(snapshot_path.read_text(encoding="utf-8"))
        print(f"loading {snapshot_path.name}: {len(costs)} NREL ATB battery storage cost rows")

        for c in costs:
            con.execute(
                """
                INSERT INTO battery_storage_cost (
                    duration_hours, capex_usd_per_kw, fixed_om_usd_per_kw_year, projection_year,
                    atb_dataset_year, scenario, financing_case, source, snapshot_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (duration_hours, projection_year) DO UPDATE SET
                    capex_usd_per_kw = excluded.capex_usd_per_kw,
                    fixed_om_usd_per_kw_year = excluded.fixed_om_usd_per_kw_year,
                    atb_dataset_year = excluded.atb_dataset_year,
                    scenario = excluded.scenario,
                    financing_case = excluded.financing_case,
                    snapshot_date = excluded.snapshot_date
                """,
                [
                    c["duration_hours"], c["capex_usd_per_kw"], c["fixed_om_usd_per_kw_year"],
                    c["projection_year"], c["atb_dataset_year"], c.get("scenario", "Moderate"),
                    c.get("financing_case", "Market"), c.get("source", "nrel_atb"), snapshot_date,
                ],
            )

    n_costs = con.execute("SELECT count(*) FROM battery_storage_cost").fetchone()[0]
    print(f"warehouse now has {n_costs} NREL ATB battery storage cost rows")


def build(warehouse_path: Path = WAREHOUSE_PATH) -> None:
    con = duckdb.connect(str(warehouse_path))
    try:
        con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        _build_tesla(con)
        _build_nrel(con)
        _build_fhwa(con)
        _build_eia(con)
        _build_noaa(con)
        _build_epa(con)
        _build_openei(con)
        _build_nrel_atb(con)
    finally:
        con.close()


if __name__ == "__main__":
    build()
