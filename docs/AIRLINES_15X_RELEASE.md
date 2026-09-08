# VOLTERRA V1 — Airlines-1.5× Depth Candidate

Release: `VOLTERRA_V1_FORTUNE50_AIRLINES15X_RC4`

## What changed

This release adds a provenance-aware empirical backbone, a live empirical API, project-native historical/entity diagnostics, a named empirical case study, external-source refresh/promotion workflow, live empirical charts, and a 26+ workspace contract in which each workspace has a distinct method/evidence/action definition.

## Current evidence mode

- Source: **Alternative Fuel Stations API**
- Source URL: https://developer.nlr.gov/docs/transportation/alt-fuel-stations-v1/
- Mode: **offline_reference**
- Promotion state: **REFERENCE_ONLY**
- Local analyzable evidence: **153 rows / 9 fields**
- Workspaces: **26**

## Domain diagnostic

**station queue-risk and climate sensitivity**

Reference metrics:
```json
{
  "records": 153,
  "sites": 17,
  "temperature_wait_correlation": -0.018
}
```

Decision signal: Use high queue-risk sites/corridors as GRIDWEAVE repair candidates, then verify N-1 coverage and exact expansion MILP feasibility.

## Analytical chain

source provenance → schema/data-quality checks → entity/factor drilldown → cohort/history comparison → diagnostic ranking → predictive model → original algorithm → OR/simulation escalation → counterfactual challenge → human decision

## Windows gates

Core/offline acceptance:
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_airlines15x_acceptance.ps1
```

External-data promotion (internet required):
```powershell
.\scripts\windows_external_data_promotion.ps1
```

## Claim boundary

External-source results are claimed only when data_mode is refreshed_external or published_external_snapshot; offline_reference remains reference evidence.

The label “Airlines-1.5×” is an internal portfolio-depth target relative to the latest observable Airlines evidence, not an external company certification and not a claim that reference/synthetic data is real production evidence.
