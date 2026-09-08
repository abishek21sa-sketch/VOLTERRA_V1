# Architecture notes

High-level data/control flow once past scaffold stage. Formulations are copied from the original
project concept for reference while building each module — treat them as the target, not as
already-implemented.

## Flow

```
ingestion/ (Python)          — Tesla + supplemental collectors, dated snapshots
        │
        ▼
data/snapshots/               — immutable, versioned raw extracts
        │
        ▼
warehouse/ (DuckDB+spatial)   — replay snapshots → volterra.duckdb (read-only to everything else)
        │
        ├──────────────┬───────────────┬──────────────────┐
        ▼              ▼               ▼                  ▼
optimization/(Julia) simulation/(Julia) graph/(Memgraph)  ml/(Python)
   facility loc.      M/G/c + DES       centrality,        forecasting,
   capacity/power      site model       criticality idx    RL agent
   grid/battery
        │              │               │                  │
        └──────────────┴───────┬───────┴──────────────────┘
                                ▼
                    backend/ (Go/Fiber API + copilot)
                                │
                                ▼
                    frontend/ (Angular + MapLibre GL)
```

Redpanda (event bus) and Memgraph are the only two services with no file-based equivalent — see
`docker-compose.yml`. Everything else can run as plain files/processes during early development.

## Key formulations (target, from the original design)

**Facility location** (Phase 4): binary `x_j` = build at site `j`; minimize
`CapitalCost + ExpectedTravelPenalty + ExpectedWaitingCost + GridCost + ResilienceRisk` subject to
budget, coverage, highway-accessibility, grid-capacity, service-level, and redundancy constraints.
Capacitated: capacity `c_j` (stall count) and power tier `p_j` are selected jointly with location,
not as a separate pass.

**Queueing** (Phase 3): each site is an M/G/c system — `c` = stall count, service time
non-exponential and heterogeneous (function of initial/desired SOC, vehicle charging curve,
charger power, temperature). Report utilization, expected queue length, `P(wait)`, expected wait,
service level, `P(wait > threshold)` as separate numbers — never blended into one score (project-
wide discipline inherited from the sibling AirlinesApp project, applied here to queueing outputs).

**Routing** (Phase 9): EVs have `SOC_{t+1} = SOC_t - E_ij + E_charge_j`, constrained by
`SOC_t ≥ SOC_min` — a resource-constrained shortest path, not plain shortest-path. System-optimal
vs. user-equilibrium routing is a real comparison to make once congestion-aware routing exists,
since naive nearest-station routing creates the congestion it's trying to avoid.

**Resilience** (Phase 5): graph `G=(V,E)` of sites + corridors; removing a site is evaluated by
journeys made infeasible, added travel distance, congestion transferred elsewhere, and
connectivity loss — the Charging Criticality Index. A low-throughput rural interstate site can
rank above a high-throughput urban site if it's the only connectivity between two regions.

**Grid/battery** (Phase 7): site load `P_j(t) = Σ_k p_jk(t)`; battery decision variables `B_j`
(capacity) and `P_charge_jt`, `P_discharge_jt`, optimized for peak shaving, demand-charge
reduction, and resilience buffering against transformer/tariff constraints.

**Stochastic optimization** (Phase 8): demand `D_jt(ω)` per site/time/scenario; two-stage
stochastic programming / chance constraints / distributionally robust variants over demand,
weather, adoption, traffic, and outage uncertainty.
