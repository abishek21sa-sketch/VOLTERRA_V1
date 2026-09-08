# Research Validation Protocol — VOLTERRA V1

This protocol implements the attached Research Validation & Benchmarking standard for **GRIDWEAVE-v1**.

## Null hypothesis (H0)

H0: GRIDWEAVE produces no different topology than the cheapest-site-first baseline.

The null is not rejected by narrative alone. It is evaluated on a fixed reference scenario and declared seed set `[17, 29, 43]`.

## Baseline, metrics, and ablation

- **Baseline:** cheapest-site-first topology without N-1 criticality enforcement.
- **Primary metrics:** objective value, feasibility, decision change versus baseline, constraint violations, runtime, and reproducibility.
- **Ablation:** remove the critical-node N-1 constraint.
- **Sensitivity grid:** vary demand by a multiplier and report coverage, criticality, and topology changes.
- **Expected reporting:** include solver status, selected decision, objective components, scenario/seed, baseline comparison, and any no-feasible result.

## Evidence separation

Observed data are used for context and predictive inputs; simulated data are used for controlled scenario testing; optimized outputs are decisions produced by the signature; shadow-mode results are recommendations without actuation; realized outcomes require a separately identified operational intervention. These classes must not be merged into a single production-performance claim.

## Failure and sensitivity checks

The acceptance suite covers invalid shapes or bounds, an ordinary feasible scenario, the declared ablation, and a deterministic sensitivity call. Any infeasible scenario must return a controlled no-feasible result or a documented validation error rather than silently relaxing a constraint.

## Scalability and external validation boundary

Record candidate/scenario count, runtime, solver status, and optimality gap when a solver is used. Public or synthetic evidence supports analytical validation only. Domain/field validation, prospective shadow mode, and realized intervention outcomes remain separate required stages.
