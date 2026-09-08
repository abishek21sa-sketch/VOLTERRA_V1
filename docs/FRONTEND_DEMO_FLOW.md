# Frontend Demo Flow — VOLTERRA V1

This product keeps its own visual language: **map-first infrastructure planning studio**. The shared contract is behavioral evidence, not a shared layout or theme.

## Native entrypoint

`frontend/src/app/network-map/network-map.html`

## Project-specific demo sequence

1. inspect real network sites
2. overlay modeled demand risk
3. select resilient topology
4. stress N-1 and demand scenarios
5. investment review approves or holds

## Evidence requirements

The screen must show the project-native inputs, objective, constraints, baseline/counterfactual, evidence class, signature decision, and human approval/hold state. The product must not imply autonomous actuation.

## API evidence surface

The read-only signature evidence endpoint is `/api/governance/signature`. Its response is linked to `artifacts/fortune50_capability_benchmark.json` and exposes the current decision, baseline, sensitivity/counterfactual evidence, and human-gated status.
