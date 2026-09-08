# Data sources

Every dataset VOLTERRA uses, what it's used for, and — critically — whether values derived from
it are **real observations** or **modeled/estimated**. This file is the source of truth that
`warehouse/schema.sql`'s per-field `source` columns must stay consistent with.

## Primary: Tesla (public)

| Data | Where | Granularity | Status |
|---|---|---|---|
| Supercharger site locations, address, coordinates, stall count, rated power, access/compatibility | Tesla "Find Us" pages | Per-site, real | Ingested — 17 US sites (Phase 1) |
| Network-level uptime (99.95% avg., 2024 Impact Report) | Tesla Impact Report | Network aggregate, real | Benchmark only — never disaggregated to a fake per-site uptime |
| Supercharger count, energy delivered, stalls added (Q4 2024 update: 65,000+ chargers, 5.2+ TWh in 2024, 10,000+ stalls added) | Tesla quarterly updates | Network aggregate, real | Calibration target for growth/adoption scenarios |
| Production/delivery figures (e.g. 1,654,667 produced / 1,636,129 delivered, 2025) | Tesla Investor Relations | Company aggregate, real | Input to EV-adoption growth scenarios, not station demand |

**Never derived from Tesla data**: station-level demand, session counts, queue lengths, or
utilization. Tesla does not publish these. Any number describing them is `modeled` or
`estimated` and must be labeled as such everywhere it's displayed.

## Supplemental (public, real)

| Source | Used for | Notes |
|---|---|---|
| NREL / Alternative Fuels Data Center | Non-Tesla charging stations, connector standards, competitor comparison | **Ingested** — 200 real DC-fast stations, 12 states (Phase 6). NREL was renamed "National Laboratory of the Rockies (NLR)"; API host moved from `developer.nrel.gov` (retired 2026-05-29) to `developer.nlr.gov` — see `ingestion/supplemental/nrel.py` |
| FHWA | Alternative Fuel Corridor designations (real corridor topology) | **Ingested** — all 994 real EV-designated segments nationwide, with real geometry (Phase 6). Official USDOT/BTS ArcGIS Feature Service, part of the National Transportation Atlas Database — see `ingestion/supplemental/fhwa.py`. Interstate *traffic volume* (AADT/HPMS) is a separate, not-yet-ingested FHWA dataset |
| US Census | Population, commuting patterns, urban density, household characteristics | **Blocked** — the ACS API has no `DEMO_KEY`-equivalent; every keyless request 302s to `missing_key.html` (confirmed). A real key requires submitting the user's email to an external signup form, which this project won't do autonomously — see `ingestion/supplemental/README.md`. (The reverse-geocoding half — coordinate → real Census tract GEOID via `geocoding.geo.census.gov` — is itself keyless and was confirmed working; only the ACS data API is gated.) |
| EIA | Electricity prices | **Ingested** — real 2024 commercial retail price, all 12 states (Phase 6). Used directly as a real cost term in `optimization/src/capacity_selection.jl` (previously had no electricity cost at all) — see `ingestion/supplemental/eia.py` |
| OpenEI / NREL Utility Rate Database (URDB) | Real per-site utility demand-charge tariffs | **Ingested** — 6 of 17 real sites (Phase 7, `DEMO_KEY`'s 10-req/hour ceiling), real per-site $/kW rates from real utilities (SCE, PacifiCorp, Progress Energy Florida, Appalachian Power, Duquesne Light) via real lat/lon lookup — see `ingestion/supplemental/openei.py`. Used directly as a new real cost term in `optimization/src/capacity_selection.jl` alongside EIA's energy price, reported separately (never blended) |
| NOAA | Temperature, severe weather, seasonal conditions | **Ingested** — real current NWS forecast, 15 of 17 real Tesla sites (2 hit persistent upstream 503s), 210 forecast periods (Phase 6). Live forecast, not a historical climate archive. Used directly: feeds real temperature into `simulation/src/charging_curve.jl`'s `temperature_derate`/`charge_duration_hours` (previously only ever ran at a hardcoded 20°C) — see `warehouse/verify/temperature_scenarios.jl` and `ingestion/supplemental/noaa.py` |
| EPA | Vehicle efficiency/range | **Ingested** — 1,192 real 2023+ EV model-year variants, 35 makes (Phase 6), fueleconomy.gov's bulk CSV (EPA/DOE joint). `battery_kwh_estimate` is DERIVED from two real published fields (EPA has no direct battery-kWh field) — see `ingestion/supplemental/epa.py`. Used directly: `warehouse/verify/vehicle_mix_scenarios.jl` replaces `simulation/`'s arbitrary `Uniform(60,100)` battery-size assumption with real bootstrap sampling from this data — the real fleet's mean (111.3 kWh) is 39% larger than the arbitrary assumption's mean (80 kWh), a real, non-trivial finding |
| NREL/NLR Annual Technology Baseline (ATB) | Real, government-published battery storage technology cost (national baseline, not per-site) | **Ingested** — all 5 real published durations (1/2/4/6/8hr), current-year (2026) projection (Phase 7), from NREL/NLR's real bulk CSV on the public OEDI S3 data lake — see `ingestion/supplemental/nrel_atb.py`. Used directly as the cost basis for a new real battery storage *sizing* MILP, `optimization/src/battery_storage.jl` — a real, honest finding: battery storage only pays off at 1 of the 6 currently-priced real sites under real 2026 costs |

## Modeled / estimated (explicitly labeled, never shown as observed)

- Station-level demand and session-level arrivals (queueing/simulation inputs).
- Vehicle charging curves by model (absent a public per-model curve source, documented
  approximations are used and labeled).
- Any forecast (`ml/` outputs) — always tagged with the model that produced it and a confidence
  interval where applicable, never rendered as a fact.

## Attribution / terms

Fill in exact source URLs and last-checked dates here as each collector in `ingestion/` is
implemented — a collector must not ship without its source's terms-of-use documented here.

- **Tesla**: no published API terms (site not designed for programmatic access); collector
  respects `robots.txt` (`Crawl-delay: 10`), rate-limits, and identifies itself. Last checked
  2026-08-27.
- **NREL AFDC** (developer portal now branded NLR — National Laboratory of the Rockies): public
  API, free, requires an API key (https://developer.nlr.gov/signup/); rate limits documented at
  https://developer.nlr.gov/docs/rate-limits (`DEMO_KEY` for light testing only — and even
  tighter on this specific endpoint, see `ingestion/supplemental/nrel.py`). Data field reference:
  https://afdc.energy.gov/data_download/alt_fuel_stations_format. Domain-transition notice:
  https://developer.nlr.gov/docs/nlr-domain-transition/. Last checked 2026-08-27.
- **FHWA Alternative Fuel Corridors**: public ArcGIS Feature Service, no key required. Explicit
  public-domain license per the item's own metadata: "a work of the United States government...
  not protected by any U.S. copyrights... available for unrestricted public use." Program page:
  https://www.fhwa.dot.gov/environment/alternative_fuel_corridors/. Data dictionary:
  https://doi.org/10.21949/1529007. Service:
  https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_Alternative_Fuel_Corridors/FeatureServer.
  Last checked 2026-08-27.
- **EIA** (Energy Information Administration): public API, free, requires an API key
  (https://www.eia.gov/opendata/register.php); same `api-umbrella` gateway and `DEMO_KEY`
  constraints as NLR's API despite a different domain — see `ingestion/supplemental/eia.py`.
  Explicitly a work of the U.S. government (public domain). API browser:
  https://www.eia.gov/opendata/browser/electricity/retail-sales. Last checked 2026-08-27.
- **NOAA / NWS** (National Weather Service): public API (`api.weather.gov`), genuinely keyless —
  no account or token required, unlike NOAA's separate Climate Data Online API. Docs:
  https://www.weather.gov/documentation/services-web-api. Requires an identifying `User-Agent`
  header per its own usage guidance; no documented hard rate limit, collector paces requests as a
  courtesy regardless — see `ingestion/supplemental/noaa.py`. A work of the U.S. government
  (public domain). Last checked 2026-08-27.
- **EPA/DOE fueleconomy.gov**: public bulk data (`https://www.fueleconomy.gov/feg/epadata/vehicles.csv`),
  genuinely keyless, no account. Terms: https://www.fueleconomy.gov/feg/ws/index.shtml (public,
  free to use; the site itself is a joint EPA/DOE government resource). Data dictionary for CSV
  columns: https://www.fueleconomy.gov/feg/ws/index.shtml#vehicle. Last checked 2026-08-27.
- **Census ACS** (attempted, blocked): public API (`api.census.gov/data`), requires a real key —
  no `DEMO_KEY`-equivalent, confirmed via a 302 redirect to `missing_key.html` on a keyless
  request. Key signup: https://api.census.gov/data/key_signup.html (requires submitting an email
  address; not something this project does autonomously). Docs:
  https://www.census.gov/data/developers/guidance/api-user-guide.html. Last checked 2026-08-27.
- **OpenEI / NREL Utility Rate Database (URDB)**: public API (`api.openei.org/utility_rates`),
  free, `DEMO_KEY` capped at 10 requests/hour (same `api-umbrella` gateway signature as NREL/EIA).
  Real key signup: https://openei.org/services/api/signup/. Docs:
  https://openei.org/services/doc/rest/util_rates/. A work of NREL/DOE (public domain data). Last
  checked 2026-08-27.
- **NREL/NLR Annual Technology Baseline (ATB)**: public bulk data
  (`https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/2024/v3.0.0/ATBe.csv`), genuinely
  keyless, no account. Dashboard: https://atb.nlr.gov (note the NLR domain — `atb.nrel.gov` no
  longer resolves). Documentation: https://atb.nlr.gov/electricity/2024/index. A work of NREL/DOE
  (public domain data). Last checked 2026-08-27.
