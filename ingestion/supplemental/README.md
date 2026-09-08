# ingestion/supplemental

One collector per external source, same versioned-snapshot discipline as `../tesla/`.

## nrel.py — working

Real, documented public REST API — no Selenium/browser workaround needed, unlike Tesla. See
`nrel.py`'s module docstring for two important, non-obvious findings from development:

1. **NREL renamed itself.** `developer.nrel.gov` doesn't resolve because NREL was renamed the
   **National Laboratory of the Rockies (NLR)**; the old domain was retired 2026-05-29 in favor
   of `developer.nlr.gov` (old API keys still work, only the hostname changed — see
   https://developer.nlr.gov/docs/nlr-domain-transition/). AFDC itself kept its familiar domain;
   found the new API host by reading AFDC's own JS bundle, since the docs at the time still
   pointed to the retired one.
2. **`DEMO_KEY` is tighter here than NLR's documented default.** Site-wide default is 30/hour,
   50/day (https://developer.nlr.gov/docs/rate-limits); this specific endpoint enforces **10/hour**
   empirically (services may override the default). Fine for a small, focused pull; get a free
   real key in seconds at https://developer.nlr.gov/signup/ and set `NREL_API_KEY` for anything
   larger.

```
pip install -e .
python -m supplemental.nrel --states NV,UT,CA --max-records 200
```

First real snapshot: `data/snapshots/nrel/2026-08-27.json` — 200 real DC-fast-charging stations
(a partial sample; 6,831 matched the query across VOLTERRA's 12 Tesla-network states, `DEMO_KEY`'s
rate limit didn't allow pulling the rest during development). Includes every network AFDC tracks
— Tesla (31 stations, tagged `ev_network: "Tesla"`), Non-Networked, ChargePoint, EVCS, and others
— which is exactly the OEM-neutral, non-Tesla-competitor role this source is meant to fill (see
`../../docs/data-sources.md`).

## fhwa.py — working

Real public ArcGIS Feature Service, no API key, no rate limit hit during development — easier
than both Tesla and NREL. FHWA's own Alternative Fuel Corridors page just points to an ArcGIS Hub
page with no obvious API; the real, queryable endpoint was found the same way Tesla's and NREL's
were — not by trusting the docs, but by searching ArcGIS Online's public item catalog
(`arcgis.com/sharing/rest/search?q=Alternative+Fuel+Corridors`) for the authoritative
`USDOT_BTS`-owned Feature Service (part of the National Transportation Atlas Database), then
reading its item metadata for the real `FeatureServer` URL. See `fhwa.py`'s module docstring for
the exact resolution path — the collector hardcodes that specific owned service rather than a
fuzzy search match, since plenty of unauthoritative state/individual corridor layers show up in
the same search.

```
pip install -e .
python -m supplemental.fhwa                    # full national EV corridor network (single request)
python -m supplemental.fhwa --states NV,UT      # or just some states
```

First real snapshot: `data/snapshots/fhwa/2026-08-27.json` — **all 994** FHWA-designated EV
Alternative Fuel Corridor segments nationwide (not a partial sample — the whole EV-designated
network fits in one query, well under the service's 2,000-record cap). Includes real
`LineString`/`MultiLineString` geometry per segment, not just metadata.

Cross-validated against Phase 5's resilience graph in `../../warehouse/verify/corridor_alignment.py`:
3 of the 4 real Tesla sites on that graph's Las Vegas–Nephi corridor sit within ~3 miles of the
real FHWA-designated I-15 EV corridor centerline (Beaver, UT: 0.14 mi) — confirming Phase 5's
geographic-proximity proxy graph was a reasonable stand-in for real corridor topology in that
case, not just a coincidence of nearby points. One site (Nephi) comes out ~21 mi off despite I-15
running through the real town of Nephi — a real gap in this dataset's segment coverage there, not
a bug; see that script's output for the honest caveat.

## eia.py — working

Real public REST API (api.eia.gov, "Electricity Retail Sales"). Same `DEMO_KEY` story as NREL —
10 requests/hour on this endpoint — and the response headers carry the identical `api-umbrella`
gateway signature NLR's API does, despite being a completely different domain, suggesting shared
underlying government API-gateway infrastructure. Unlike NREL, one request covers every state of
interest (`facets[stateid][]=` repeated per state), so the full 12-state pull costs exactly one
request, not one per state.

```
pip install -e .
python -m supplemental.eia --year 2024
```

First real snapshot: `data/snapshots/eia/2026-08-27.json` — real 2024 average commercial retail
electricity price for all 12 of VOLTERRA's Tesla-network states, from 9.39¢/kWh (Utah) to
25.54¢/kWh (California). Loaded into `eia_electricity_price` and **used immediately**, not just
stored: `../../optimization/src/capacity_selection.jl` previously had no electricity cost term at
all; now it's a real third component of the MILP's objective (real price × modeled energy
consumption), reported separately from capex and waiting cost. See
`../../optimization/README.md`.

Also surfaced a real, reproducible bug in `optimization/`'s and `graph/`'s DuckDB.jl usage on
Windows — `DBInterface.close!` doesn't synchronously release the file lock — while wiring
`load_sites()` and `load_electricity_prices()` together in the same process. Fixed with a shared
`close + GC.gc()` helper in both modules' `warehouse_io.jl`.

## noaa.py — working

Real public NWS API (`api.weather.gov`) — genuinely keyless, no account/token needed at all,
unlike NOAA's other API (Climate Data Online, which does require a free registered token — tried
that first; the NWS forecast API is the keyless alternative and this project never creates
accounts on the user's behalf). Live current-forecast data, not a historical climate archive.
Two calls per site (`/points/{lat},{lon}` → grid cell → `/gridpoints/.../forecast`), no bulk
endpoint.

Two real, non-obvious bugs found during development, both in `noaa.py`'s docstring/comments:
1. **ASCII-only headers.** httpx encodes headers as ASCII by default; an em-dash in the
   `User-Agent` string broke every single request with `UnicodeEncodeError`. NWS's own docs
   *require* an identifying `User-Agent`, so the fix is a plain-ASCII one, not dropping the header.
2. **httpx doesn't follow redirects by default.** `/points/{lat},{lon}` always 301s to NWS's own
   canonicalized 4-decimal-place coordinate — every request failed with `HTTPStatusError` on the
   bare redirect until `follow_redirects=True` was added to the `httpx.get()` call. (`requests`
   follows redirects by default; httpx deliberately doesn't — easy to miss coming from `requests`.)

```
pip install -e .
python -m supplemental.noaa --from-warehouse   # pulls site_id/lat/lon from charging_site
python -m supplemental.noaa --site abc123,34.05,-118.24   # or specific sites
```

First real snapshot: `data/snapshots/noaa/2026-08-27.json` — real current NWS forecast for 15 of
17 real Tesla sites in the warehouse, 210 forecast periods total. Most transient `503 Service
Unavailable` responses from NWS's forecast endpoint (real upstream flakiness, not a collector bug)
recovered within the 3-attempt retry budget, but two sites (Chino Hills, CA and Nephi, UT) hit
three consecutive 503s each and were skipped — confirmed via the warehouse, not just the log: `run()`
logs-and-continues per site rather than failing the whole snapshot, so this is exactly the intended
degraded-but-complete behavior, not a bug. Re-running the collector will likely pick these two up.
Loaded into `noaa_forecast` and **used immediately, not just stored**:
`../../warehouse/verify/temperature_scenarios.jl` feeds real forecast temperatures into
`../../simulation/src/charging_curve.jl`'s `temperature_derate`/`charge_duration_hours`, which
previously only ever ran with a hardcoded `temp_c=20.0` anywhere in this codebase. At the real
coldest period on file (Cle Elum, WA, "Saturday Night", 45°F/7.2°C), real derating lengthens
service time 4.9% and mean queue wait from 1.59 to 2.6 minutes — a real, non-trivial effect. The
real warmest period (Las Vegas, 109°F) shows exactly 0% effect, honestly surfacing that the model
only derates for cold, not heat — see that script's docstring and `../../simulation/README.md`.

## epa.py — working

Real public bulk data: fueleconomy.gov's full vehicle CSV (jointly maintained by EPA and DOE),
`https://www.fueleconomy.gov/feg/epadata/vehicles.csv` — ~22MB, no key, no account, single
request. fueleconomy.gov also exposes a nested per-model REST API (confirmed real and keyless
during development — pulled a real 2024 Tesla Model 3 RWD via
`/ws/rest/vehicle/menu/model?year=2024&make=Tesla` → `.../menu/options?...` →
`/ws/rest/vehicle/47909`), but it's one request per model variant with no bulk-by-fuel-type
endpoint. The CSV is the same underlying database — confirmed by cross-checking vehicle id 47909's
CSV row against the JSON API response for that same id: identical `range`/`combE`/`charge240` —
so the CSV wins for a fleet-wide pull, same "one request gets everything" shape as `fhwa.py`.

Filtered to `fuelType1 == "Electricity"` and model year 2023+ (the current-generation EV fleet
actually being sold and charged today, not EPA's full history back to 1984). **EPA doesn't publish
a battery-kWh field** (`battery` is `-1`/unavailable on every row checked) — `battery_kwh_estimate`
is DERIVED (`range_miles * comb_e_kwh_per_100mi / 100`) and tagged with a distinct
`epa_fueleconomy_gov_derived` source, not conflated with the directly-published fields.

```
pip install -e .
python -m supplemental.epa                       # model year 2023+
python -m supplemental.epa --min-model-year 2020  # wider fleet sample
```

First real snapshot: `data/snapshots/epa/2026-08-27.json` — 1,192 real EV model-year variants
across 35 real makes (3 collapse on load due to duplicate make/model/year keys — see
`schema.sql`'s comment on `epa_vehicle`; 1,189 distinct rows). Real derived battery-kWh range: 35.0
kWh (2023 MINI Cooper SE Hardtop) to 245.4 kWh (2025 Chevrolet Silverado EV 8WT), real mean 111.3
kWh — a much wider real spread, and a notably higher mean, than the arbitrary `Uniform(60,100)`
assumption's fixed range and 80 kWh mean. Loaded into `epa_vehicle` and **used immediately, not
just stored**: `../../warehouse/verify/vehicle_mix_scenarios.jl` bootstrap-samples from the real
distribution instead of that arbitrary shape.

**A significant, honest finding, not a footnote**: substituting the real fleet mix at all three of
Phase 3's real demo sites lengthens service time by exactly 39.4% and pushes utilization from the
"safe" 0.80 the arbitrary assumption was tuned to, past 1.0 — an unstable queue — at every site
tested (Cranberry Township PA, Charleston WV, Santa Clarita CA). This means every previously
reported "0.80 utilization" figure in this project's Phase 3/4 demos was implicitly calibrated to
a battery-size assumption today's real EV market (more large-battery trucks/SUVs) has outgrown —
worth keeping in mind when citing those earlier results. See
`../../simulation/README.md` and that script's docstring.

## openei.py — working (Phase 7)

Real public REST API: NREL/OpenEI's U.S. Utility Rate Database (`api.openei.org/utility_rates`),
the canonical real archive of actual published utility tariffs — real utility name, real rate
name, real approval status, real source URL, and the real rate structure (energy $/kWh AND demand
$/kW). Same `api-umbrella` gateway signature and `DEMO_KEY` (10 requests/hour) story as
NREL/EIA — see `nrel.py`/`eia.py`.

**Why this source, for Phase 7 specifically**: DC fast charging's real-world operating cost is
often dominated by the demand charge (a per-kW-peak monthly fee), not the per-kWh energy price
`eia.py` already ingested in Phase 6 — a genuinely well-known pain point in the real EV-charging
industry, and exactly the real number `optimization/`'s grid-aware work needs. `eia.py`'s
state-level average has no demand-charge component at all.

**Per-site lookup, not per-state** — unlike `eia.py`, URDB rates are genuinely utility- and
tariff-specific, and the API supports a real lat/lon + radius search, so this collector queries
each real Tesla site's own coordinates directly (`sector=Commercial`, 20-mile radius). Confirmed
real example: the real Chino Hills, CA site resolves to a real Southern California Edison
"TOU-GS-3" commercial demand-metered tariff with real on-peak/off-peak/partial-peak rates
($11.24-$17.13/kW/month), sourced from SCE's own published rate sheet (URL included in the raw
response). Takes the MAX real $/kW value across a rate's real TOU/flat structure — DC fast
charging's peaky load makes the worst-case real rate the economically relevant one, not an
average that would understate real peak-demand risk.

```
pip install -e .
python -m supplemental.openei --from-warehouse
```

First real snapshot: `data/snapshots/openei/2026-08-27.json` — 6 of 17 real sites (DEMO_KEY's
10-requests/hour ceiling; the collector correctly logged, retried, and gracefully skipped the
remaining 11 once the quota hit `429 Too Many Requests` mid-run — confirmed behavior, not a bug).
Real rates found: PacifiCorp ($6.85/kW), Progress Energy Florida ($6.02/kW), Appalachian Power
($4.52/kW), Southern California Edison ($17.13/kW), Duquesne Light ($7.25/kW) — a real ~4x spread
across real utilities, itself a finding worth knowing before assuming a flat number. Loaded into
`grid_demand_charge` and **used immediately, not just stored**: a new real cost term in
`optimization/src/capacity_selection.jl`'s MILP — see `optimization/README.md`.

One real, honest data-quality caveat found during development: the 20-mile radius search
occasionally returns a real but not obviously EV-charging-relevant Commercial rate (e.g. a real
"School Service" tariff near the Charleston, WV site) — URDB has no EV-charging-specific sector
tag, so this collector takes the nearest real approved Commercial rate as the best available
proxy, not a perfect match. Documented rather than silently accepted as more precise than it is.

## nrel_atb.py — working (Phase 7)

Real public bulk data: NREL's Annual Technology Baseline (ATB) — the real, government-published,
annually-updated U.S. baseline for electricity generation and storage technology costs, used
throughout real utility/DOE/national-lab technoeconomic modeling. Same NREL-to-NLR rename story as
`nrel.py`, confirmed at the DNS level: `atb.nrel.gov` no longer resolves at all, `atb.nlr.gov`
does. Found the real bulk CSV the same "read what the live site actually calls" way as every other
source here — the ATB web app (a Tableau dashboard) links to a real, keyless, single-request CSV
on the public Open Energy Data Initiative (OEDI) S3 data lake (~98.5MB).

Filtered to `technology == "Commercial Battery Storage"` (not Residential or Utility-Scale),
across all 5 real published durations (1/2/4/6/8 hour), `scenario == "Moderate"` (deliberately not
the optimistic "Advanced" or pessimistic "Conservative" case), `core_metric_case == "Market"`, and
the current year's real cost projection. `CAPEX` is ATB's own complete real figure — confirmed by
cross-checking `CAPEX = OCC + CFC` exactly, to the cent, across every duration.

```
pip install -e .
python -m supplemental.nrel_atb                     # current year
python -m supplemental.nrel_atb --projection-year 2030  # a future ATB projection year
```

First real snapshot: `data/snapshots/nrel_atb/2026-08-27.json` — all 5 real durations for
projection year 2026: 1hr ($1,207/kW), 2hr ($1,428/kW), 4hr ($1,870/kW), 6hr ($2,312/kW), 8hr
($2,754/kW), each with a real fixed O&M figure too. Loaded into `battery_storage_cost` and **used
immediately, not just stored**: `optimization/src/battery_storage.jl`'s new battery storage sizing
MILP, alongside the real OpenEI demand-charge data above — see `optimization/README.md` for a
real, honest finding this produces (battery storage pays off at only 1 of the 6 currently-priced
real sites under real 2026 costs, not "everywhere").

## Not yet built (blocked, not just unbuilt)

`census.py` — blocked, not merely unimplemented. Unlike NREL/EIA, the Census ACS API has no
`DEMO_KEY`-equivalent: every keyless request 302-redirects to `missing_key.html` (confirmed by
testing a real reverse-geocoded tract query). Getting a real key means submitting the user's email
address to an external signup form (https://api.census.gov/data/key_signup.html) — this project
won't submit that on the user's behalf; it's the kind of form-submission-with-personal-data action
that needs the user's own explicit action, not an autonomous one. Revisit if the user provides a
key. (The reverse-geocoding half of the pipeline — `geocoding.geo.census.gov`, coordinate → real
Census tract GEOID — is itself keyless and was confirmed working; it's only the ACS *data* API
that's gated.)
