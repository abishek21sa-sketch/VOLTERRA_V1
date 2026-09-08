# data/

Nothing in this directory is committed to git (see root `.gitignore`) — it's all regenerable by
running the collectors in `ingestion/`. The structure exists now so ingestion code has a stable
place to write to.

- `raw/` — unmodified responses/downloads from each source, cached by source and fetch date.
  Never processed in place; `ingestion/` reads from here to build snapshots.
- `snapshots/` — versioned, immutable, dated extracts (e.g. `snapshots/tesla/2026-08-26.json`).
  A snapshot is never overwritten or deleted — this is what lets `warehouse/` reconstruct network
  expansion over time. One subdirectory per source.

See [`../docs/data-sources.md`](../docs/data-sources.md) for what each source is used for and
whether it's real vs. modeled, and the root [`CLAUDE.md`](../CLAUDE.md) for the versioning
discipline collectors must follow.
