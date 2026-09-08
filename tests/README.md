# tests

This top-level directory previously held six pytest files exercising a generic template
system (`tenx/`, `tenx_ui/`, `campaign/`, `empirical/`, `intelligence/`) unrelated to VOLTERRA's
actual EV charging / grid domain — removed as template contamination. It never contained real
coverage for this project's own modules.

Real test coverage lives next to the code it tests:

- Julia: `optimization/test/`, `simulation/test/`, `graph/test/`, `routing/test/` (run via each
  module's own `Pkg.test()` — see each module's README).
- Python ML: `ml/tests/`.
- Go backend: `backend/internal/copilot/copilot_test.go`, `backend/internal/copilot/tools_test.go`,
  `backend/internal/graphdb/graphdb_test.go` (run via `go test ./...` from `backend/`).
