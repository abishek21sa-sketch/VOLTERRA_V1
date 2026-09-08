# GRIDWEAVE-v1 — Signature Algorithm Contract

This document is the project-native mathematical center required by the portfolio governance pack. The implementation alias is **network topology and N-1 repair**.

## Operational decision

The module makes one operational decision: **build a grid topology that covers demand and maintains a declared critical-node repair margin**.

## Mathematical center

- **Decision variables:** binary site-selection/topology decisions and critical-node redundancy state.
- **Objective:** minimize topology cost subject to demand coverage and critical-node constraints.
- **Constraints and release gates:** all demand nodes covered; critical nodes retain N-1 alternate coverage.
- **Determinism:** the reference contract is deterministic for a fixed candidate set, scenario, and seed.
- **Solver status:** the current reference is an executable enumerative/closed-form contract; production solver integration remains downstream of this gate.

## Baseline and counterfactual

The named baseline is **cheapest-site-first topology without N-1 criticality enforcement**. The counterfactual is evaluated on the same inputs and scenario so that a claimed improvement cannot be caused by a changed data slice.

## Ablation

The declared ablation is to **remove the critical-node N-1 constraint**. It is executable through the module's `ablation(...)` function and is covered by the signature tests.

## Sensitivity

The sensitivity sweep is: **vary demand by a multiplier and report coverage, criticality, and topology changes**. Sensitivity output is evidence about robustness, not a claim of causal production impact.

## Evidence classes and authority

Evidence is kept separate as observed, simulated, optimized, shadow-mode, and realized. **observed/public network context plus simulated demand scenarios; realized grid intervention outcomes are not claimed** A human authority remains required before any operational action; autonomous execution is disabled.

## Implementation and acceptance

- Implementation: `tenx/signature_algorithm.py`
- Windows acceptance test: `tests/test_signature_algorithm.py`
- Required acceptance result: `4 tests, OK`, with invalid inputs and no-feasible cases controlled explicitly.

## Release boundary

This signature is release-ready only when this contract, the research-validation protocol, the machine-readable governance artifact, the existing Airlines 1.5x gates, and the final integrity/hash checks all pass together.
