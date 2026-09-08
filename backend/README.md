# backend

Go + Fiber API gateway: serves the warehouse to the frontend, orchestrates Julia/Python workers
over Redpanda, hosts the Network Operations Copilot tool-calling loop (Phase 11).

## Status: working

`GET /api/sites` reads the real warehouse and returns real Tesla Supercharger data — verified
against `warehouse/volterra.duckdb`. No CGO DuckDB driver is used (this dev environment has no C
compiler); `internal/warehouse` shells out to the `duckdb` CLI with `-json` output instead — see
that package's doc comment.

`GET /api/demand` (Phase 6) serves `ml/`'s precomputed queue-risk surrogate-model predictions for
all 17 real sites across a utilization grid — `internal/mlpredictions` reads a plain JSON file
(`ml/data/queue_risk_predictions.json`, regenerate via `ml/examples/predict_real_sites_grid.py`),
**not** a warehouse query and **not** a live call into Python/LightGBM — see that package's doc
comment for the two hard constraints that rule those out (warehouse write-access discipline; no
cross-language runtime bridge exists yet). Verified end-to-end: real request against a running
backend returns real per-site predictions, confirmed joining correctly against all 17 real
`/api/sites` rows by `site_id` in the actual frontend.

`POST /api/copilot` (Phase 11) is the Network Operations Copilot — see "Copilot" below.

```
docker compose up -d memgraph   # real graph database the network_resilience tool queries live
go mod tidy
go run ./cmd/loadgraph          # loads the real warehouse network into Memgraph (re-run after warehouse changes)
go run ./cmd/api
curl http://localhost:8080/health
curl http://localhost:8080/api/sites
curl http://localhost:8080/api/demand
curl -X POST http://localhost:8080/api/copilot -H "Content-Type: application/json" -d '{"message":"how many stalls does the Las Vegas site have?"}'
go test -p 1 ./...   # 15 tests: 7 mocked-server copilot-loop + 4 guarded real-data tool tests (copilot) + 4 graphdb tests (2 self-contained, 2 guarded real-Memgraph)
# -p 1 matters here: internal/copilot and internal/graphdb both have a real test that calls
# LoadGraph (a DETACH DELETE + reload) against the same live Memgraph instance -- Go runs
# different packages' tests in parallel by default, and two concurrent LoadGraph calls hitting
# real Memgraph at once produce a real transaction conflict ("Cannot resolve conflicting
# transactions"), confirmed by reproduction. -p 1 serializes package-level test execution, which
# fixes it; plain `go test ./...` will intermittently fail on this real shared-resource race.
```

`VOLTERRA_QUEUE_RISK_PREDICTIONS_PATH` overrides the default `../ml/data/queue_risk_predictions.json`
(same override pattern as `VOLTERRA_WAREHOUSE_PATH` below).

Requires the `duckdb` CLI on `PATH` (or adjust `internal/warehouse.QuerySites` to an absolute
path). `VOLTERRA_WAREHOUSE_PATH` overrides the default `../warehouse/volterra.duckdb`; `PORT`
overrides the default 8080 — **on this dev machine 8080 is already taken by a sibling project's
backend (`apex_backend`), so local runs here use `PORT=8090`.** Adjust for your own machine.

## Copilot (Phase 11)

`internal/copilot` implements the Network Operations Copilot: a tool-calling loop against Claude's
Messages API, mirroring AirlinesApp's `api/copilot.py` pattern (this project's own sibling
portfolio project) — tiered models, tools wired to VOLTERRA's own real query functions, and the
forced-final-answer discipline (when the model still wants to call tools after `MaxHops`, force a
final answer with `tool_choice={"type":"none"}` **and** `thinking={"type":"disabled"}` together —
see `anthropic.go`'s `Thinking` doc comment for the specific silent-empty-response failure mode
that combination prevents, confirmed in AirlinesApp's own production copilot).

Four real tools, deliberately not more — `internal/copilot/tools.go`'s `NewTools` doc comment has
the full reasoning, in short:
- `list_sites`, `site_details` — fast, Go-native, read the real warehouse directly (no subprocess).
- `queue_risk_prediction` — a live Python subprocess call to `ml/examples/predict_one.py` (roughly
  10-20 real measured seconds, variable — tolerable for an interactive tool call).
- `network_resilience` — a live Cypher query against a real, always-on Memgraph instance
  (`internal/graphdb`) — see "Graph database" below. This used to read a precomputed JSON artifact
  instead, because the equivalent live JULIA subprocess call measured **~53 real seconds** end to
  end (Julia's own startup/JIT warmup, not the computation) — far too slow for a synchronous tool
  call. Memgraph sidesteps that entirely by staying warm as a real running service: the real
  round-trip for this tool now measures well under 3 seconds. A live capacity-optimization tool
  was considered and dropped for the same Julia-subprocess-latency reason and remains a real
  future upgrade via job orchestration over Redpanda (scaffolded in `../docker-compose.yml`, not
  yet wired to any Go code) — a slower computation than a graph query, so a warm service alone
  isn't enough there; it genuinely needs an async worker.

**What has and hasn't been verified.** `go test ./internal/copilot` passes 7 tests verifying the
tool-calling loop logic against an `httptest` server that plays back canned Anthropic API response
shapes (including that the forced-final-answer request really does set both `tool_choice` and
`thinking` together), plus 4 guarded tests exercising the real tools against the real warehouse,
a real running Memgraph, and the real trained queue-risk model. `curl -X POST .../api/copilot`
against a real running server correctly returns a clear `ANTHROPIC_API_KEY not configured` error
rather than crashing. What is NOT verified: whether a real Claude model actually picks sensible
tools for a real natural-language question — this dev environment has no `ANTHROPIC_API_KEY`
configured, so no live call to the real API has been made. Set the env var and try a real request
to close that gap.

```
export ANTHROPIC_API_KEY=sk-ant-...          # required for /api/copilot to actually answer
export VOLTERRA_CLAUDE_MODEL_PUBLIC=...       # optional, defaults to claude-haiku-4-5
export VOLTERRA_CLAUDE_MODEL_RESEARCHER=...   # optional, defaults to claude-sonnet-4-5
```

## Graph database (Memgraph) — the real target architecture, now built

`internal/graphdb` is a real Cypher-over-Bolt client (`github.com/neo4j/neo4j-go-driver/v5`,
Bolt-protocol-compatible with Memgraph) against a real Memgraph instance
(`docker compose up -d memgraph`). This is what `graph/`'s Julia/Graphs.jl implementation was
always a documented, tested **stand-in** for (Docker's daemon was confirmed hung when Phase 5 was
originally built) — Docker now works in this dev environment, so this is the real swap, not a
second independent reimplementation.

`cmd/loadgraph` reads every real site from the warehouse and loads it into Memgraph as a real
`:ChargingSite` graph — real coordinates/stall counts/power, real geographic-proximity edges (same
250-mile threshold rule `graph/`'s Julia code and `routing/`'s Phase 9 work both use, for
consistency — real FHWA corridor topology exists but nothing in this project consumes it for edge
placement yet, a separate, larger gap). `internal/graphdb.ResilienceMetrics` computes degree
centrality and betweenness centrality **server-side, via Memgraph's bundled MAGE library**
(`betweenness_centrality.get`) — real graph algorithms running in the graph database, not
reimplemented in Go — and computes the Charging Criticality Index **in Go** from a Memgraph-sourced
edge list (MAGE has no single built-in procedure for "connectivity pairs lost if this node is
removed," and `graph/`'s own Julia implementation is itself a custom algorithm on top of a
connected-components primitive, not a single library call either).

**Cross-validated, not just "it ran without error."** `go test ./internal/graphdb` loads the real
warehouse network into a real running Memgraph and checks the result against `graph/`'s
already-tested Julia implementation's real output for the exact same network at the same 250mi
threshold (Las Vegas: degree=4, betweenness=0.05, criticality=6; St. George and Beaver, UT:
degree=3, betweenness=0.0125; Cle Elum, WA: degree=0; Charleston, WV: degree=1) — every value
matched on first correct run. One real Cypher-dialect bug was found and fixed along the way:
Memgraph doesn't implement Neo4j's inline `size((s)--())` pattern-comprehension syntax
(`Not yet implemented: atom expression '(s)--()'`) — fixed with a standard
`OPTIONAL MATCH (s)--(neighbor) ... count(neighbor)` aggregation instead, more portable Cypher.

**Port note**: `docker-compose.yml`'s Memgraph Lab UI port is remapped to host `3001` (container
port stays `3000`) — host `3000` was already bound by a sibling project's container on this dev
machine, confirmed via `docker ps` before changing it.

## Toolchain note for this dev environment

Go was not on `PATH` for this session's shell, but was already installed at
`C:\Program Files\Go\bin\go.exe` (a prior session's note here described a portable
`~/go-sdk/go` extraction instead — this environment differs, both approaches work identically once
the binary is found). Same story for the DuckDB CLI: `winget install DuckDB.cli` reported it
already installed, just not on `PATH` — found at
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\DuckDB.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\duckdb.exe`.
If cloning this repo somewhere both are already on `PATH`, none of this applies — just
`go mod tidy && go run ./cmd/api`.

`internal/` holds `warehouse/` (the DuckDB CLI wrapper), `mlpredictions/` (the `ml/`
queue-risk-JSON reader), `graphdb/` (the real Memgraph/Cypher client), and `copilot/` (the
tool-calling loop and real tools) — one package per concern. `cmd/` holds `api/` (the server) and
`loadgraph/` (the one-shot real-warehouse-to-Memgraph loader).
