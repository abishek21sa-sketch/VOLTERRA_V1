# ingestion/tesla

Collector for Tesla's public Supercharger network ("Find Us"). This is the primary empirical
source for the whole platform — see [`../../docs/data-sources.md`](../../docs/data-sources.md).

## Status: working, network-constrained

`collector.py` is implemented and verified: it drives a real Chrome session (Selenium) to
`GET /api/findus/get-locations?country=US&view=map` for the candidate list, then each site's
`GET /findus/location/supercharger/<slug>` page for stall count, power, address, and status —
parsed out of the page's server-rendered `__NEXT_DATA__` JSON. No documented public API exists;
this is what Tesla's own site does in the browser.

**Run this from an ordinary residential/desktop network, not a cloud sandbox or CI runner.**
Confirmed during development: Tesla's edge (Akamai Bot Manager) returns "Access Denied" to this
endpoint from datacenter/cloud IP ranges even via a genuine headless Chrome — same block a bare
`curl` gets. It's IP-reputation-based, not a header/fingerprint problem more code can fix. This
mirrors the sibling AirlinesApp project's BTS pipeline constraint exactly (see its CLAUDE.md) —
this module stays local-only for the same reason.

```
pip install -e .
python -m tesla.collector --limit 15   # quick verification run
python -m tesla.collector               # full US network (~1000+ sites @ 10s/site ≈ 3 hours)
```

## Real snapshots so far

- `data/snapshots/tesla/2026-08-26.json` — 15 US sites, geographically scattered (one per state,
  no real corridor structure).
- `data/snapshots/tesla/2026-08-27.json` — the same 15 plus 2 more (St. George UT, Beaver UT),
  added specifically because Phase 5's resilience analysis needed real sites close enough
  together to form real corridor structure — see `../../graph/README.md`.

Neither was produced by a `collector.py` run (Selenium is blocked from the dev sandbox, per
above); both were collected interactively through a working browser session during development,
using the exact same source and fields `collector.py` uses, and validated to parse cleanly
against `SuperchargerSite`. A real `collector.py` run appends the next dated snapshot the same
way, with no schema differences.

## Design constraints (non-negotiable, see root CLAUDE.md)

- **Rate-limited and identifying.** `robots.txt` sets `Crawl-delay: 10` for all user agents;
  `CRAWL_DELAY_SECONDS` in `collector.py` matches it. Do not lower it to go faster.
- **Versioned, immutable output.** Every run writes a new dated file to
  `data/snapshots/tesla/<YYYY-MM-DD>.json`. `collector.py` refuses to overwrite an existing
  snapshot for today — the warehouse reconstructs "network expansion over time" by replaying
  snapshots in order.
- **Cache raw responses.** Master-list and per-page responses are cached to `data/raw/tesla/`
  before parsing, so a parsing bug never means re-fetching from Tesla.
- **Field-level provenance.** Every emitted field is either a direct Tesla observation or
  explicitly tagged otherwise (see `data-sources.md`) — this collector never invents a field
  Tesla doesn't publish. (Tesla's Find Us data is already geocoded — no separate geocoding step
  is needed.)
