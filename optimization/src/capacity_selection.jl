"""
Capacity + power-tier selection MILP: for each real site, choose a stall count and charger
power tier from a discrete menu (or keep it unchanged) to minimize capital cost plus the
annualized cost of customer waiting time plus the annualized cost of the electricity itself
plus the annualized grid demand charge, subject to a capital budget and a minimum service level —
using the M/G/c queueing core from ../../simulation as the actual waiting-cost function, not a
proxy.

This is the "Charger Capacity Optimization" piece from the project brief (jointly selecting
stall count and power architecture), applied to VOLTERRA's *existing* real network rather than
hypothetical new locations. True greenfield facility-location (choosing brand-new geographic
sites) is still not built — real FHWA corridor data now exists (Phase 6, `fhwa_corridor_segment`)
to place candidates defensibly instead of picking arbitrary coordinates, but nothing here
consumes it yet; that's still ahead (see docs/roadmap.md Phase 6).

**Real**: site_id, name, current stall_count, current max_power_kw, site state (from the
warehouse); per-state commercial electricity price (`load_electricity_prices`, real EIA data);
per-site max commercial demand charge (`load_demand_charges`, real OpenEI/URDB data, Phase 7 —
partial coverage, see that function's docstring).
**Modeled** (see ../../docs/data-sources.md — none of this is Tesla-published):
  - the discrete tier menu and per-stall/power-upgrade capital costs (industry-order-of-magnitude
    estimates, not Tesla capex figures),
  - the baseline demand scenario (today's utilization assumed, then scaled by a growth
    multiplier — Tesla does not publish per-site utilization),
  - the dollar value of customer waiting time,
  - energy consumed per charging session (from ../../simulation's charging-curve model — the
    *price* per kWh is real, the *quantity* of kWh consumed is modeled),
  - peak coincident demand (`PEAK_DEMAND_COINCIDENCE_FACTOR`) — the real demand-charge *rate*
    (\$/kW) is real, but a utility bills on a site's actual simultaneous peak power draw over a
    billing period, which this project has no real per-site measurement of (Tesla doesn't publish
    it); modeled as a fraction of nameplate capacity (`stalls * power_kw`), not the full nameplate
    figure, since the charging-curve taper means not every stall is simultaneously at rated power.

Per this project's discipline (see root CLAUDE.md), results report capex, waiting-cost,
electricity cost, and demand-charge cost separately per site — the objective function necessarily
blends them into one number to optimize, but nothing here presents that blended number as if it
were a real "priority score".
"""

using JuMP
using HiGHS
using VolterraSimulation
using Distributions
using Random

struct TierOption
    stalls::Int
    power_kw::Float64
end

const TIER_MENU = TierOption[
    TierOption(8, 150.0),
    TierOption(16, 250.0),
    TierOption(24, 250.0),
    TierOption(32, 250.0),
    TierOption(40, 500.0),
]

# MODELED cost assumptions -- industry-order-of-magnitude figures, not Tesla-published capex.
const COST_PER_STALL_USD = Dict(150.0 => 40_000.0, 250.0 => 65_000.0, 500.0 => 120_000.0)
const POWER_UPGRADE_RETROFIT_PER_EXISTING_STALL_USD = 15_000.0

const BASELINE_UTILIZATION = 0.65        # modeled planning assumption, not observed
const VALUE_OF_TIME_USD_PER_HOUR = 20.0  # modeled
const OPERATING_HOURS_PER_YEAR = 8760.0  # sites run 24/7, matches real Tesla access-hours data

# MODELED: fraction of a tier's nameplate capacity (stalls * power_kw) assumed to be drawn
# simultaneously during the utility's real peak-demand billing window. Not 1.0 -- the
# charging-curve taper (charging_curve.jl) means a vehicle late in its session draws well under
# rated power, so not every occupied stall is at its nameplate rating at the same instant even
# at full occupancy. Not derived from a real measured coincidence factor (Tesla publishes none);
# 0.7 is a standard planning-level assumption in EV-charging techno-economic literature, not a
# site-specific fit.
const PEAK_DEMAND_COINCIDENCE_FACTOR = 0.7
const MONTHS_PER_YEAR = 12

const BATTERY_KWH_DIST = Uniform(60.0, 100.0)                     # modeled
const INITIAL_SOC_DIST = Truncated(Normal(0.30, 0.12), 0.02, 0.55) # modeled
const TARGET_SOC = 0.80                                            # modeled

const DEFAULT_TEMP_C = 20.0  # the flat baseline every prior run in this codebase used before Phase 6

const _SERVICE_TIME_MOMENTS_CACHE = Dict{Tuple{Float64,Float64},Tuple{Float64,Float64,Float64}}()

"""
    service_time_moments(power_kw; temp_c=DEFAULT_TEMP_C, n=2000) -> (mean_hours, var_hours, mean_energy_kwh)

Samples the (real, non-exponential) service-time distribution implied by the charging-curve
model at a given charger power and temperature, plus the mean energy delivered per session
(`battery_kwh * (TARGET_SOC - soc0)` — exact per sampled session, not a power-times-time
approximation, since tapering makes those different). Same method
`simulation/examples/site_queueing_demo.jl` uses for the timing part, duplicated here rather than
shared because this module's SOC/battery assumptions are a planning scenario choice, not
necessarily identical to that demo's.

`temp_c` defaults to the flat 20°C baseline `capacity_selection.jl`'s deterministic model has
always used — `stochastic_capacity_selection.jl` (Phase 8) is what actually varies it, one real
NOAA-derived scenario at a time (see `load_temperature_scenarios`); `mean_energy_kwh` doesn't
depend on `temp_c` at all (energy needed is a function of SOC delta and battery size, not how
fast it's delivered), only `mean_hours`/`var_hours` do — worth knowing before assuming every
returned value varies by scenario.

Deterministic — seeded from `(power_kw, temp_c)`, and memoized — so this is a pure function of its
inputs in practice: the same (tier, temperature) always yields the same moments, whether called
once or across every site/scenario sharing that combination, and results are reproducible across
separate runs (this matters for the MILP: `optimize_capacity_portfolio` must give the same answer
for the same inputs, not a different one each time from fresh Monte Carlo noise).
"""
function service_time_moments(power_kw::Float64; temp_c::Float64=DEFAULT_TEMP_C, n::Int=2000)
    key = (power_kw, temp_c)
    haskey(_SERVICE_TIME_MOMENTS_CACHE, key) && return _SERVICE_TIME_MOMENTS_CACHE[key]

    # Seed from power_kw alone at the default temperature -- preserves the exact Monte Carlo draws
    # (and therefore the exact dollar figures already published in this project's READMEs/roadmap)
    # that every call site used before temp_c became parametrized. Only genuinely new (power_kw,
    # temp_c) combinations -- Phase 8's stochastic scenarios -- get their own seed derived from
    # the pair; nothing previously documented shifts.
    rng = MersenneTwister(temp_c == DEFAULT_TEMP_C ? hash(power_kw) : hash(key))
    durations = Vector{Float64}(undef, n)
    energies = Vector{Float64}(undef, n)
    for i in 1:n
        soc0 = clamp(rand(rng, INITIAL_SOC_DIST), 0.02, TARGET_SOC - 0.01)
        battery_kwh = rand(rng, BATTERY_KWH_DIST)
        durations[i] = charge_duration_hours(soc0, TARGET_SOC, battery_kwh, power_kw; temp_c=temp_c)
        energies[i] = battery_kwh * (TARGET_SOC - soc0)
    end
    mean_s = sum(durations) / n
    var_s = sum((s - mean_s)^2 for s in durations) / (n - 1)
    mean_energy = sum(energies) / n

    result = (mean_s, var_s, mean_energy)
    _SERVICE_TIME_MOMENTS_CACHE[key] = result
    return result
end

function capex_for(site::WarehouseSite, tier::TierOption)
    incremental_stalls = max(tier.stalls - site.stall_count, 0)
    new_stall_cost = incremental_stalls * COST_PER_STALL_USD[tier.power_kw]
    power_upgrade = tier.power_kw > site.max_power_kw ?
        site.stall_count * POWER_UPGRADE_RETROFIT_PER_EXISTING_STALL_USD : 0.0
    return new_stall_cost + power_upgrade
end

annualized_waiting_cost_usd(arrival_rate_per_hour::Float64, mean_wait_hours::Float64) =
    VALUE_OF_TIME_USD_PER_HOUR * arrival_rate_per_hour * OPERATING_HOURS_PER_YEAR * mean_wait_hours

annualized_electricity_cost_usd(arrival_rate_per_hour::Float64, mean_energy_kwh::Float64,
                                 price_usd_per_kwh::Float64) =
    arrival_rate_per_hour * OPERATING_HOURS_PER_YEAR * mean_energy_kwh * price_usd_per_kwh

"""
    nameplate_peak_kw(tier) -> Float64

Modeled peak coincident demand for a tier: `tier.stalls * tier.power_kw *
PEAK_DEMAND_COINCIDENCE_FACTOR`. The single shared definition of "how many kW this site's real
demand-charge billing and any battery-storage peak-shaving decision should assume it draws at
once" — `annualized_demand_charge_usd` below and `battery_storage.jl`'s sizing model both call
this rather than each re-deriving the same modeled assumption independently.
"""
nameplate_peak_kw(tier::TierOption) = tier.stalls * tier.power_kw * PEAK_DEMAND_COINCIDENCE_FACTOR

"""
    annualized_demand_charge_usd(tier, demand_charge_usd_per_kw) -> Float64

Real \$/kW rate × modeled peak coincident demand (`nameplate_peak_kw(tier)`), billed monthly and
annualized. Independent of arrival rate — unlike energy cost (which scales with how much is
delivered) and waiting cost (how long people wait), a demand charge is driven by the site's peak
simultaneous draw capacity, not its throughput — a nearly-idle 40-stall site and a fully-booked
40-stall site face the same nameplate-driven demand-charge exposure under this modeled coincidence
assumption.
"""
annualized_demand_charge_usd(tier::TierOption, demand_charge_usd_per_kw::Float64) =
    nameplate_peak_kw(tier) * demand_charge_usd_per_kw * MONTHS_PER_YEAR

struct CandidateOption
    site::WarehouseSite
    tier::TierOption
    is_upgrade::Bool
    capex_usd::Float64
    arrival_rate_per_hour::Float64
    queueing::QueueingResult
    annualized_waiting_cost_usd::Float64
    annualized_electricity_cost_usd::Float64
    annualized_demand_charge_usd::Float64
end

"""
    candidate_options(site, growth_multiplier, electricity_prices, demand_charges) -> Vector{CandidateOption}

Builds every tier option for `site` that is at least as large as the current stall count and
stable (utilization < 1) at the modeled demand. Demand (arrival rate) is a property of the site,
held fixed across tier choices — adding stalls/power doesn't change how many drivers want to
charge there, only how fast they're served.

`electricity_prices` (from `load_electricity_prices`, real EIA data) is keyed by state; a site
whose state is missing falls back to the mean of whatever prices *are* present, so a genuinely
absent state is at least priced plausibly rather than silently defaulting to zero — but this
should not happen for VOLTERRA's current 12-state network, and if it does, it's worth noticing,
not treating as normal (see the fallback branch's warning).

`demand_charges` (from `load_demand_charges`, real OpenEI/URDB data, Phase 7) is keyed by
`site_id`, not state — same missing-value fallback shape as electricity prices (mean of known
real rates, with a warning), but expect this branch to fire far more often: OpenEI's `DEMO_KEY`
ceiling means only a subset of VOLTERRA's 17 real sites have a real per-site rate on file at any
given time (see that function's docstring) — this is a normal, expected, and honestly-reported
partial-coverage state, not a bug to silence.

Deliberately does **not** filter by a service-level cap here — see
`optimize_capacity_portfolio`'s docstring for why that's a reported metric, not a hard
per-candidate exclusion.
"""
function candidate_options(site::WarehouseSite, growth_multiplier::Float64,
                            electricity_prices::Dict{String,Float64},
                            demand_charges::Dict{String,Float64})
    mean_s_current, _, _ = service_time_moments(site.max_power_kw)
    baseline_arrival = BASELINE_UTILIZATION * site.stall_count / mean_s_current
    arrival_rate = baseline_arrival * growth_multiplier

    price_usd_per_kwh = if !ismissing(site.state) && haskey(electricity_prices, site.state)
        electricity_prices[site.state]
    else
        @warn "no real EIA electricity price for state $(site.state) (site $(site.name)) -- using the mean of known states" site.state
        sum(values(electricity_prices)) / length(electricity_prices)
    end

    demand_charge_usd_per_kw = if haskey(demand_charges, site.site_id)
        demand_charges[site.site_id]
    else
        @warn "no real OpenEI demand charge for site $(site.name) (DEMO_KEY partial coverage) -- using the mean of known real rates" site.site_id
        sum(values(demand_charges)) / length(demand_charges)
    end

    candidate_tiers = TierOption[TierOption(site.stall_count, site.max_power_kw)]
    for t in TIER_MENU
        t.stalls > site.stall_count && push!(candidate_tiers, t)
    end

    options = CandidateOption[]
    for tier in candidate_tiers
        mean_s, var_s, mean_energy_kwh = service_time_moments(tier.power_kw)
        offered_load = arrival_rate * mean_s
        offered_load >= tier.stalls && continue  # unstable queue at this demand -- not physically viable

        q = mgc_queue(arrival_rate, tier.stalls, mean_s, var_s)
        is_upgrade = tier.stalls != site.stall_count || tier.power_kw != site.max_power_kw
        capex = is_upgrade ? capex_for(site, tier) : 0.0
        push!(options, CandidateOption(
            site, tier, is_upgrade, capex, arrival_rate, q,
            annualized_waiting_cost_usd(arrival_rate, q.mean_wait_hours),
            annualized_electricity_cost_usd(arrival_rate, mean_energy_kwh, price_usd_per_kwh),
            annualized_demand_charge_usd(tier, demand_charge_usd_per_kw),
        ))
    end
    return options
end

struct PortfolioResult
    selected::Vector{CandidateOption}
    infeasible_sites::Vector{String}          # no stable tier exists at this demand at all
    below_service_target_sites::Vector{String} # selected, but mean wait exceeds the cap
    total_capex_usd::Float64
    total_annualized_waiting_cost_usd::Float64
    total_annualized_electricity_cost_usd::Float64
    total_annualized_demand_charge_usd::Float64
    termination_status::MOI.TerminationStatusCode
end

"""
    optimize_capacity_portfolio(sites; budget_usd, growth_multiplier=1.0, max_mean_wait_minutes=15.0,
                                 electricity_prices=load_electricity_prices(),
                                 demand_charges=load_demand_charges())

Solves the MILP: minimize total capex + annualized waiting cost + annualized electricity cost +
annualized demand charge across all sites, choosing exactly one tier per site (including "no
change"), subject to the capital budget. Electricity cost uses real per-state EIA prices
(`electricity_prices`, default loaded fresh from the warehouse) — see
`annualized_electricity_cost_usd`. Demand-charge cost uses real per-site OpenEI/URDB rates
(`demand_charges`, Phase 7, partial real coverage — see `load_demand_charges`) — see
`annualized_demand_charge_usd`.

`max_mean_wait_minutes` is **not** a hard per-candidate filter — an early version of this model
used it that way, but that makes every site's compliance mandatory-or-infeasible with no room for
the optimizer to trade budget against service quality, which defeats the point of a budget
constraint (see the model's module docstring discussion). Instead it's purely a reporting
threshold: any selected option whose mean wait exceeds it is surfaced in
`below_service_target_sites`, separately from the cost totals, so a tight budget shows up as
*which* sites fall short rather than the whole model going infeasible. Sites with no stable tier
at all (queue overloaded even at the largest tier in `TIER_MENU`) are a genuine infeasibility and
are excluded from the model, reported in `infeasible_sites`.
"""
function optimize_capacity_portfolio(sites::Vector{WarehouseSite}; budget_usd::Float64,
                                      growth_multiplier::Float64=1.0,
                                      max_mean_wait_minutes::Float64=15.0,
                                      electricity_prices::Dict{String,Float64}=load_electricity_prices(),
                                      demand_charges::Dict{String,Float64}=load_demand_charges())
    per_site_options = Dict(site.site_id => candidate_options(site, growth_multiplier, electricity_prices, demand_charges)
                             for site in sites)
    infeasible_sites = [site.name for site in sites if isempty(per_site_options[site.site_id])]

    items = CandidateOption[]
    for site in sites
        append!(items, per_site_options[site.site_id])
    end
    n = length(items)

    model = Model(HiGHS.Optimizer)
    set_silent(model)
    @variable(model, y[1:n], Bin)

    for site in sites
        isempty(per_site_options[site.site_id]) && continue
        idxs = findall(item -> item.site.site_id == site.site_id, items)
        @constraint(model, sum(y[i] for i in idxs) == 1)
    end

    @constraint(model, sum(y[i] * items[i].capex_usd for i in 1:n) <= budget_usd)
    @objective(model, Min, sum(
        y[i] * (items[i].capex_usd + items[i].annualized_waiting_cost_usd +
                items[i].annualized_electricity_cost_usd + items[i].annualized_demand_charge_usd)
        for i in 1:n
    ))

    optimize!(model)
    status = termination_status(model)

    selected = CandidateOption[]
    if status == MOI.OPTIMAL || status == MOI.TIME_LIMIT
        selected = [items[i] for i in 1:n if value(y[i]) > 0.5]
    end

    below_target = [o.site.name for o in selected if o.queueing.mean_wait_hours * 60 > max_mean_wait_minutes]

    return PortfolioResult(
        selected, infeasible_sites, below_target,
        sum(o.capex_usd for o in selected; init=0.0),
        sum(o.annualized_waiting_cost_usd for o in selected; init=0.0),
        sum(o.annualized_electricity_cost_usd for o in selected; init=0.0),
        sum(o.annualized_demand_charge_usd for o in selected; init=0.0),
        status,
    )
end
