# Enterprise Readiness — VOLTERRA V1

## Release status

**VOLTERRA_V1_PORTFOLIO_RC1** is a portfolio release candidate, not a production deployment certification.

## Decision-system identity

Jointly chooses site opening, stall buildout and corridor service under budget/grid/capacity constraints and prices N-1 infrastructure resilience.

**Signature core:** Multi-period greenfield EV charging network-expansion MILP + N-1 critical-corridor resilience

## Verified in this recovery build

- 4/4 independent SciPy/HiGHS formulation-reference tests verified
- Machine-readable validation evidence is included and hashed.
- Production writes/autonomous execution are blocked by release governance.
- Windows remains the primary local acceptance target.

## Evidence inventory

- `validation/portfolio_validation.json` — SHA-256 `14ca95833242db5473759b80f8613ce60576017a22d62fddff7504e88d28351e`

## Gates still required before any production claim

- Native Julia/JuMP optimization test suite on Windows
- Python ML suite after installing declared Polars dependency
- Go backend tests after dependency restoration
- Angular frontend build/runtime acceptance
- Real-site/grid/corridor data validation

## Claim boundary

This repository may be presented as a reproducible engineering/research decision system supported by its included model-based evidence. It must not be presented as real-world production improvement, certification, clinical effectiveness, vehicle certification, grid approval, or plant/fab performance unless that external validation is subsequently completed.
