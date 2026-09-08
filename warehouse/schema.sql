-- VOLTERRA warehouse schema (target — not yet applied by a build script; see README.md).
--
-- Conventions:
--   * `source` columns hold one of: a real-data-source tag ('tesla_find_us', 'tesla_impact_report',
--     'nrel_afdc', 'fhwa', 'census', 'eia', 'noaa', 'epa') or 'modeled' / 'estimated'. See
--     ../docs/data-sources.md for the authoritative list.
--   * OEM-neutral: `network_operator` is a foreign key everywhere a lesser design would hardcode
--     "Tesla". Tesla is simply the first (and initially only) populated operator.

INSTALL spatial;
LOAD spatial;

CREATE TABLE IF NOT EXISTS network_operator (
    operator_id     VARCHAR PRIMARY KEY,   -- e.g. 'tesla'
    display_name    VARCHAR NOT NULL,      -- e.g. 'Tesla Supercharger'
    homepage_url    VARCHAR
);

-- One row per physical charging site, as currently known (latest state).
-- Historical states live in charging_site_snapshot; this table is a materialized "as of latest".
CREATE TABLE IF NOT EXISTS charging_site (
    site_id             VARCHAR PRIMARY KEY,
    operator_id         VARCHAR NOT NULL REFERENCES network_operator(operator_id),
    name                VARCHAR NOT NULL,
    street_address      VARCHAR,
    city                VARCHAR,
    state               VARCHAR,
    geom                GEOMETRY,              -- point geometry (spatial extension)
    -- Plain latitude/longitude alongside `geom`: several clients (DuckDB.jl in optimization/,
    -- graph/) can't safely LOAD the spatial extension in this environment (crashes — see
    -- optimization/README.md), so reads that only need coordinates use these instead of
    -- ST_X(geom)/ST_Y(geom). Same value, redundant on purpose.
    latitude            DOUBLE,
    longitude           DOUBLE,
    geom_source         VARCHAR NOT NULL,      -- e.g. 'geocoded_from_tesla_address'
    stall_count         INTEGER NOT NULL,
    stall_count_source  VARCHAR NOT NULL DEFAULT 'tesla_find_us',
    max_power_kw        DOUBLE NOT NULL,
    max_power_kw_source VARCHAR NOT NULL DEFAULT 'tesla_find_us',
    access_restricted   BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen_snapshot DATE NOT NULL,
    last_seen_snapshot  DATE NOT NULL
);

-- Immutable history: one row per (site, snapshot_date) as observed in that dated snapshot.
-- This is what makes "network expansion over time" queryable — never updated in place.
CREATE TABLE IF NOT EXISTS charging_site_snapshot (
    site_id         VARCHAR NOT NULL REFERENCES charging_site(site_id),
    snapshot_date   DATE NOT NULL,
    stall_count     INTEGER NOT NULL,
    max_power_kw    DOUBLE NOT NULL,
    access_restricted BOOLEAN NOT NULL DEFAULT FALSE,
    source          VARCHAR NOT NULL DEFAULT 'tesla_find_us',
    PRIMARY KEY (site_id, snapshot_date)
);

-- Network-level aggregate benchmarks Tesla publishes (uptime, energy delivered, growth). These
-- are calibration targets, never disaggregated to fabricate a per-site number.
CREATE TABLE IF NOT EXISTS network_benchmark (
    operator_id     VARCHAR NOT NULL REFERENCES network_operator(operator_id),
    metric_name     VARCHAR NOT NULL,      -- e.g. 'avg_site_uptime_pct', 'stalls_added_2024'
    period_start    DATE,
    period_end      DATE,
    value           DOUBLE NOT NULL,
    unit            VARCHAR,
    source          VARCHAR NOT NULL,      -- e.g. 'tesla_2024_impact_report'
    PRIMARY KEY (operator_id, metric_name, period_end)
);

-- NREL AFDC-tracked EV charging stations — real, network-neutral infrastructure inventory (every
-- network including Tesla; see ../docs/data-sources.md). Deliberately a separate table from
-- charging_site, not a union: charging_site is Tesla-Supercharger-specific and feeds the
-- queueing/optimization models (stall_count, max_power_kw are load-bearing there); nrel_station
-- is a broader real inventory used for competitor/coverage-gap analysis, nothing in
-- optimization/simulation reads it (yet). One row per station "as currently known" (latest
-- snapshot) — no immutable per-snapshot history table yet, unlike charging_site_snapshot; add one
-- if/when tracking non-Tesla network growth over time actually matters for an analysis.
CREATE TABLE IF NOT EXISTS nrel_station (
    station_id          BIGINT PRIMARY KEY,     -- AFDC's own numeric id
    station_name        VARCHAR NOT NULL,
    ev_network          VARCHAR,                -- e.g. 'Tesla', 'ChargePoint Network', 'Non-Networked'
    latitude             DOUBLE NOT NULL,
    longitude            DOUBLE NOT NULL,
    city                 VARCHAR,
    state                VARCHAR,
    street_address       VARCHAR,
    zip_code             VARCHAR,
    access_code          VARCHAR,               -- 'public' / 'private'
    ev_connector_types   VARCHAR[],
    ev_dc_fast_num       INTEGER,
    ev_level1_evse_num   INTEGER,
    ev_level2_evse_num   INTEGER,
    ev_pricing           VARCHAR,
    facility_type        VARCHAR,
    open_date            DATE,
    status_code          VARCHAR,               -- 'E' = available/open
    owner_type_code      VARCHAR,
    date_last_confirmed  DATE,
    source               VARCHAR NOT NULL DEFAULT 'nrel_afdc',
    snapshot_date        DATE NOT NULL           -- most recent snapshot this row reflects
);

-- FHWA-designated Alternative Fuel Corridor highway segments (23 U.S.C. 151) -- real, official
-- corridor topology, not a geographic proxy. This is what graph/'s Phase 5 resilience analysis
-- approximated with a great-circle-distance graph before this data existed (see
-- graph/README.md) -- e.g. it can now confirm I-15 through NV/UT is a real "Signage Ready" EV
-- corridor, not just four points that happen to be near each other.
--
-- `geometry_geojson` is the raw GeoJSON LineString/MultiLineString text, not a `GEOMETRY` column
-- -- same reasoning as charging_site's plain latitude/longitude: DuckDB.jl (optimization/,
-- graph/) can't load the `spatial` extension in this environment, and nothing here needs real
-- spatial operations (containment, intersection) yet, just point-to-polyline distance checks
-- that are simpler to do in plain Python/Julia against parsed coordinates than in SQL anyway.
CREATE TABLE IF NOT EXISTS fhwa_corridor_segment (
    object_id            BIGINT PRIMARY KEY,  -- ArcGIS's own unique feature id
    afc_number          VARCHAR NOT NULL,     -- FHWA's corridor number (matches highway number)
    road_type           VARCHAR,              -- 'I' Interstate / 'U' US Route / 'S' State route
    route_number        VARCHAR,
    primary_name         VARCHAR,              -- e.g. 'I15'
    alt_name_1           VARCHAR,
    alt_name_2           VARCHAR,
    local_name           VARCHAR,
    state                VARCHAR,
    ev_round             INTEGER,              -- annual designation round (1-8+) this segment was added/confirmed in
    ev_status            VARCHAR,              -- e.g. 'Signage Ready'
    length_miles         DOUBLE,
    geometry_geojson     VARCHAR NOT NULL,     -- raw GeoJSON LineString/MultiLineString, [lon,lat] pairs
    source                VARCHAR NOT NULL DEFAULT 'fhwa_ntad',
    snapshot_date         DATE NOT NULL
);

-- EIA average commercial retail electricity price per state per year -- real, published rate
-- (Forms EIA-826/861/861M), used as the closest real figure to what a Supercharger site actually
-- pays per kWh (Tesla doesn't publish site-level electricity contracts). Feeds a real operating-
-- cost term in optimization/src/capacity_selection.jl -- see that module for how modeled energy
-- consumption is combined with this real price.
CREATE TABLE IF NOT EXISTS eia_electricity_price (
    state                VARCHAR NOT NULL,
    state_description     VARCHAR,
    sector_id             VARCHAR NOT NULL,     -- 'COM' commercial / 'IND' industrial / 'RES' residential
    sector_name           VARCHAR,
    period_year           INTEGER NOT NULL,
    price_cents_per_kwh   DOUBLE NOT NULL,
    source                VARCHAR NOT NULL DEFAULT 'eia',
    snapshot_date         DATE NOT NULL,
    PRIMARY KEY (state, sector_id, period_year)
);

-- NOAA National Weather Service forecast, per real charging site -- real, live forecast data
-- (not a historical climate archive; see ingestion/supplemental/noaa.py). Feeds real temperature
-- into simulation/'s charging_curve.jl temperature_derate, which previously only ever saw a
-- hardcoded 20.0C. Insert-only: each snapshot's periods are point-in-time observations, not a
-- "latest state" to overwrite -- re-running the collector on a different day adds new rows
-- rather than updating old ones, so a real forecast history accumulates naturally over time.
CREATE TABLE IF NOT EXISTS noaa_forecast (
    site_id             VARCHAR NOT NULL REFERENCES charging_site(site_id),
    period_name          VARCHAR NOT NULL,      -- e.g. 'Tonight', 'Friday'
    start_time           TIMESTAMPTZ NOT NULL,
    temperature_f         DOUBLE NOT NULL,
    temperature_unit      VARCHAR,
    short_forecast        VARCHAR,
    source                VARCHAR NOT NULL DEFAULT 'noaa_nws',
    snapshot_date         DATE NOT NULL,
    PRIMARY KEY (site_id, period_name, start_time, snapshot_date)
);

-- EPA/DOE (fueleconomy.gov) certified EV efficiency and range, per real model-year variant -- real
-- bulk data (see ingestion/supplemental/epa.py), current-generation vehicles only (model year >=
-- the collector's MIN_MODEL_YEAR, not EPA's full history back to 1984). `battery_kwh_estimate` is
-- DERIVED (range_miles * comb_e_kwh_per_100mi / 100) -- EPA does not publish a battery-kWh field
-- directly (confirmed: the raw `battery` column is -1/unavailable on every row) -- so its `source`
-- is tagged 'epa_fueleconomy_gov_derived', distinct from the directly-published fields' plain
-- 'epa_fueleconomy_gov'. Feeds simulation/'s charging_curve.jl BATTERY_KWH_DIST, which previously
-- used an arbitrary Uniform(60.0, 100.0) never grounded in a real vehicle-mix sample -- see
-- warehouse/verify/vehicle_mix_scenarios.jl.
CREATE TABLE IF NOT EXISTS epa_vehicle (
    make                    VARCHAR NOT NULL,
    model                   VARCHAR NOT NULL,
    year                    INTEGER NOT NULL,
    range_miles             DOUBLE,             -- EPA-rated combined range, real
    comb_e_kwh_per_100mi    DOUBLE,             -- EPA-rated combined electricity consumption, real
    charge240_hours         DOUBLE,             -- EPA-published Level 2 (240V) full-charge time, real
    battery_kwh_estimate    DOUBLE,             -- DERIVED, see comment above -- not a raw EPA field
    source                  VARCHAR NOT NULL DEFAULT 'epa_fueleconomy_gov',
    snapshot_date           DATE NOT NULL,
    -- snapshot_date deliberately NOT part of the key, same reasoning as eia_electricity_price:
    -- (make, model, year) is itself a real historical series, so re-running the collector on a
    -- later date should update a model-year variant's figures in place, not create a duplicate
    -- row per snapshot day. Known minor consequence: a handful of real EPA rows (3 of 1192 as of
    -- 2026-08-27, e.g. two Chevrolet Silverado EV 2024 trims) share an identical (make, model,
    -- year) despite being distinct tested configurations -- EPA's `model` string doesn't always
    -- disambiguate trims. These collapse to one row rather than erroring; harmless at this scale.
    PRIMARY KEY (make, model, year)
);

-- OpenEI/NREL Utility Rate Database (URDB) real commercial demand-charge tariff, nearest to each
-- real charging site -- real, published utility rate (see ingestion/supplemental/openei.py), not
-- a state average like eia_electricity_price. `max_demand_charge_usd_per_kw` is the max real
-- $/kW value across the rate's real structure (TOU tiers or flat) -- see that collector's module
-- docstring for why max, not average (DC fast charging's peaky load makes the worst-case real
-- rate the economically relevant one). Feeds a real demand-charge cost term in
-- optimization/src/capacity_selection.jl (Phase 7), alongside the existing real per-kWh energy
-- cost term from eia_electricity_price (Phase 6) -- reported as a separate cost component, never
-- blended, per this project's discipline.
CREATE TABLE IF NOT EXISTS grid_demand_charge (
    site_id                      VARCHAR NOT NULL REFERENCES charging_site(site_id),
    utility_name                 VARCHAR NOT NULL,
    rate_name                    VARCHAR NOT NULL,
    max_demand_charge_usd_per_kw DOUBLE NOT NULL,
    sector                       VARCHAR NOT NULL,
    openei_label                 VARCHAR NOT NULL,   -- OpenEI's own rate id, for traceability back to the source
    source_url                   VARCHAR,
    source                       VARCHAR NOT NULL DEFAULT 'openei_urdb',
    snapshot_date                DATE NOT NULL,
    -- snapshot_date NOT part of the key, same reasoning as eia_electricity_price/epa_vehicle:
    -- one real current rate per site is itself the latest-known-state, re-running updates in place.
    PRIMARY KEY (site_id)
);

-- NREL/NLR Annual Technology Baseline (ATB) real, government-published battery storage technology
-- cost -- not site-specific or Tesla-specific, a national technology-class baseline (see
-- ingestion/supplemental/nrel_atb.py), the same role eia_electricity_price plays for energy cost.
-- One row per real published duration (1/2/4/6/8 hour). `capex_usd_per_kw` is ATB's own complete
-- real CAPEX figure (confirmed CAPEX = OCC + CFC exactly, to the cent, across every duration).
-- Feeds optimization/src/battery_storage.jl (Phase 7) -- unlike COST_PER_STALL_USD (an "industry-
-- order-of-magnitude estimate, not Tesla-published capex"), this capex figure is real and citable.
CREATE TABLE IF NOT EXISTS battery_storage_cost (
    duration_hours           INTEGER NOT NULL,
    capex_usd_per_kw         DOUBLE NOT NULL,
    fixed_om_usd_per_kw_year DOUBLE NOT NULL,
    projection_year          INTEGER NOT NULL,
    atb_dataset_year         INTEGER NOT NULL,
    scenario                 VARCHAR NOT NULL,
    financing_case           VARCHAR NOT NULL,  -- named to avoid the SQL reserved word `case`
    source                   VARCHAR NOT NULL DEFAULT 'nrel_atb',
    snapshot_date            DATE NOT NULL,
    PRIMARY KEY (duration_hours, projection_year)
);

-- Placeholder for the remaining supplemental-source table (Census tracts). Same `source`
-- discipline once Phase 6 (see ../docs/roadmap.md) ingests it -- see CLAUDE.md for why Census is
-- currently blocked (its API requires a real key, unlike NREL/EIA's DEMO_KEY, and getting one
-- requires submitting an email address to an external form).
