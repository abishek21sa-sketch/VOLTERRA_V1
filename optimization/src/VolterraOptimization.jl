"""
    VolterraOptimization

Facility-location, capacity/power-selection, and network-protection optimization models for
VOLTERRA (Phase 4, extended in Phase 6/7/8).

- `warehouse_io.jl` — real site data, real EIA electricity prices, real OpenEI/URDB demand
  charges, real NREL/NLR ATB battery storage costs, and real per-site NOAA weather scenarios from
  the warehouse, via DuckDB.jl.
- `capacity_selection.jl` — JuMP/HiGHS MILP: per-site stall count + power tier, minimizing capex
  + annualized waiting cost (from ../../simulation's M/G/c queueing core) + annualized
  electricity cost (real EIA per-state prices × modeled energy consumption) + annualized demand
  charge (real per-site OpenEI/URDB \$/kW rate × modeled peak coincident demand, Phase 7), subject
  to budget and a service-level cap.
- `battery_storage.jl` — JuMP/HiGHS MILP: per-site battery storage *sizing* (which real ATB
  duration, if any), minimizing net annualized cost (real ATB capex/O&M minus real demand-charge
  savings), subject to a capital budget (Phase 7). Sizing only — dispatch scheduling is not built,
  see that file's module docstring for why.
- `stochastic_capacity_selection.jl` — two-stage stochastic JuMP/HiGHS MILP with a chance
  constraint (Phase 8): the same capacity/tier decision as `capacity_selection.jl`, but under real
  per-site NOAA weather scenario uncertainty (14 real forecast periods per site) instead of a
  single 20°C point estimate, with a per-site chance constraint capping the probability-weighted
  fraction of real scenarios allowed to miss the service target. Demand-growth/adoption-rate
  uncertainty is NOT modeled — no real historical growth-rate series exists to ground it, see that
  file's module docstring.
"""
module VolterraOptimization

using JuMP
using HiGHS

include("warehouse_io.jl")
include("capacity_selection.jl")
include("battery_storage.jl")
include("stochastic_capacity_selection.jl")
include("network_expansion.jl")

export WarehouseSite, load_sites, load_electricity_prices, load_demand_charges,
       load_battery_storage_costs, BatteryStorageCost, load_temperature_scenarios,
       TemperatureScenario, DEFAULT_WAREHOUSE_PATH
export TierOption, TIER_MENU, CandidateOption, PortfolioResult
export candidate_options, optimize_capacity_portfolio, capex_for, nameplate_peak_kw,
       service_time_moments, DEFAULT_TEMP_C
export annualized_waiting_cost_usd, annualized_electricity_cost_usd, annualized_demand_charge_usd
export BatteryCandidate, BatteryPortfolioResult, battery_candidates, optimize_battery_portfolio
export annualized_battery_capex_usd, annualized_battery_om_usd, annualized_demand_charge_savings_usd,
       BATTERY_LIFETIME_YEARS
export StochasticCandidateOption, StochasticPortfolioResult, stochastic_candidate_options,
       optimize_stochastic_capacity_portfolio, EPSILON_DEFAULT
export ExpansionSite, CorridorDemand, NetworkExpansionResult, optimize_network_expansion

end # module VolterraOptimization
