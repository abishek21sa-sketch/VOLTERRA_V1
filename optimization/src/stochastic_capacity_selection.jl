"""
Two-stage stochastic capacity + power-tier selection with a chance constraint on service level,
under REAL per-site weather scenario uncertainty (Phase 8).

**Two-stage structure, not dressed-up sensitivity analysis.** Stage 1 (here-and-now) chooses one
tier per site, shared across every scenario — a real capacity decision has to be made before next
week's weather is known, exactly like `capacity_selection.jl`'s tier choice. Stage 2 (recourse) is
a per-scenario violation indicator: given the fixed Stage-1 tier, does *this* scenario's real
temperature push mean wait above the service target? The Stage-1 decision is shared across
scenarios, Stage-2 outcomes differ by scenario, and a chance constraint couples them — the
defining structure of a two-stage stochastic program with recourse.

**Real scenario data, not an invented distribution.** Uncertainty here is real per-site weather
variability — every real NOAA forecast period on file for a site (`load_temperature_scenarios`,
Phase 6 data, 14 real periods per site as of this writing) is treated as one equally-likely
scenario, a standard sample-average-approximation technique applied to real forecast data rather
than a synthetic probability model. **Demand-growth and adoption-rate uncertainty — also named in
the Phase 8 roadmap item — are NOT modeled here.** VOLTERRA has no real historical growth-rate
time series to ground a demand-uncertainty distribution the way real NOAA data grounds this one
(Tesla publishes point-in-time delivery figures, not the multi-year series a real growth-rate
*distribution* would need); not attempted rather than faked. Weather is the uncertain dimension
this project can back with real data today.

**A tier is a candidate only if stable at every real scenario**, not just a baseline point
estimate — a tier that's only queueing-stable in some real observed weather conditions isn't a
genuinely usable candidate. This sidesteps ever needing to price an undefined/infinite-wait
outcome: every candidate that survives is well-defined at every real scenario, and the chance
constraint operates on real, finite wait times exceeding the service target, not on instability.

**Why only waiting cost varies by scenario.** `mean_energy_kwh` (and therefore electricity cost)
depends only on SOC delta and battery size, not on how fast a session completes — temperature-
invariant. `nameplate_peak_kw` (and therefore demand charge) is a nameplate-capacity quantity,
also temperature-invariant. Only `mean_wait_hours` (via the charging-curve temperature-derate)
differs by scenario — a real, verifiable consequence of this project's existing model structure
(see `capacity_selection.jl`'s `service_time_moments`), not a simplification invented for this
file.

Real: everything `capacity_selection.jl` treats as real, plus real per-site NOAA-derived weather
scenarios. Modeled: everything `capacity_selection.jl` treats as modeled, plus the chance-
constraint threshold `EPSILON_DEFAULT` (a policy choice, not itself uncertain) and equal-
probability weighting across a site's real forecast periods.
"""

using JuMP
using HiGHS

const EPSILON_DEFAULT = 0.20  # MODELED policy: at most this fraction of real weather scenarios
                                # may exceed the service-level target, not derived from real data.

struct StochasticCandidateOption
    site::WarehouseSite
    tier::TierOption
    is_upgrade::Bool
    capex_usd::Float64
    arrival_rate_per_hour::Float64
    expected_annualized_waiting_cost_usd::Float64
    annualized_electricity_cost_usd::Float64  # scenario-invariant, see module docstring
    annualized_demand_charge_usd::Float64     # scenario-invariant, see module docstring
    scenario_mean_wait_hours::Vector{Float64} # per real scenario, same order as that site's scenario vector
end

"""
    stochastic_candidate_options(site, scenarios, growth_multiplier, electricity_prices, demand_charges) -> Vector{StochasticCandidateOption}

Same tier menu, capex, electricity- and demand-charge logic as `candidate_options`, but waiting
cost is now an EXPECTATION over `scenarios` (real per-site weather scenarios), and a tier is only
offered if queueing-stable at *every* real scenario — see module docstring for why.
"""
function stochastic_candidate_options(site::WarehouseSite, scenarios::Vector{TemperatureScenario},
                                       growth_multiplier::Float64,
                                       electricity_prices::Dict{String,Float64},
                                       demand_charges::Dict{String,Float64})
    mean_s_current, _, _ = service_time_moments(site.max_power_kw)  # 20C baseline, same as capacity_selection.jl
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

    options = StochasticCandidateOption[]
    for tier in candidate_tiers
        moments = [(scenario, service_time_moments(tier.power_kw; temp_c=scenario.temp_c)) for scenario in scenarios]
        all(arrival_rate * mean_s < tier.stalls for (_, (mean_s, _, _)) in moments) || continue

        scenario_queues = [(scenario, mgc_queue(arrival_rate, tier.stalls, mean_s, var_s))
                            for (scenario, (mean_s, var_s, _)) in moments]
        expected_wait_cost = sum(
            scenario.probability * annualized_waiting_cost_usd(arrival_rate, q.mean_wait_hours)
            for (scenario, q) in scenario_queues
        )
        scenario_mean_wait_hours = [q.mean_wait_hours for (_, q) in scenario_queues]

        _, _, mean_energy_kwh = service_time_moments(tier.power_kw)  # 20C baseline -- temp-invariant
        is_upgrade = tier.stalls != site.stall_count || tier.power_kw != site.max_power_kw
        capex = is_upgrade ? capex_for(site, tier) : 0.0

        push!(options, StochasticCandidateOption(
            site, tier, is_upgrade, capex, arrival_rate,
            expected_wait_cost,
            annualized_electricity_cost_usd(arrival_rate, mean_energy_kwh, price_usd_per_kwh),
            annualized_demand_charge_usd(tier, demand_charge_usd_per_kw),
            scenario_mean_wait_hours,
        ))
    end
    return options
end

struct StochasticPortfolioResult
    selected::Vector{StochasticCandidateOption}
    infeasible_sites::Vector{String}  # no tier stable across every real scenario
    total_capex_usd::Float64
    total_expected_annualized_waiting_cost_usd::Float64
    total_annualized_electricity_cost_usd::Float64
    total_annualized_demand_charge_usd::Float64
    violation_rate_by_site::Dict{String,Float64}  # realized real-scenario P(wait > target) per selected site
    termination_status::MOI.TerminationStatusCode
end

"""
    optimize_stochastic_capacity_portfolio(sites; budget_usd, growth_multiplier=1.0,
        max_mean_wait_minutes=15.0, epsilon=EPSILON_DEFAULT, electricity_prices=load_electricity_prices(),
        demand_charges=load_demand_charges(), scenarios_by_site=load_temperature_scenarios())

Solves the two-stage MILP: minimize total capex + EXPECTED annualized waiting cost (across real
per-site weather scenarios) + annualized electricity cost + annualized demand charge, choosing
exactly one tier per site, subject to (a) the capital budget and (b) a real per-site chance
constraint — the probability-weighted fraction of real scenarios where mean wait exceeds
`max_mean_wait_minutes` must not exceed `epsilon`.

Implemented via a standard exact big-M reformulation: a binary `z[i,s]` per (candidate, scenario)
pair that *could* violate the target (`gap = scenario_wait - target > 0`) is forced to 1 whenever
that candidate is selected and its real scenario wait exceeds target
(`gap <= gap * (z + (1 - y))`, tight because `M = gap` exactly — no arbitrary large constant to
tune); pairs that never violate get no `z` variable at all. This is the actual stochastic-
programming machinery, not a relabeled deterministic constraint.

Sites with no real NOAA weather scenarios on file (`scenarios_by_site` missing that `site_id`) are
excluded entirely, not fallback-priced — same reasoning as `battery_storage.jl`'s missing-
demand-charge exclusion: a discrete stability/feasibility decision shouldn't rest on a fabricated
scenario set.
"""
function optimize_stochastic_capacity_portfolio(sites::Vector{WarehouseSite}; budget_usd::Float64,
        growth_multiplier::Float64=1.0, max_mean_wait_minutes::Float64=15.0,
        epsilon::Float64=EPSILON_DEFAULT,
        electricity_prices::Dict{String,Float64}=load_electricity_prices(),
        demand_charges::Dict{String,Float64}=load_demand_charges(),
        scenarios_by_site::Dict{String,Vector{TemperatureScenario}}=load_temperature_scenarios())
    priced_sites = [s for s in sites if haskey(scenarios_by_site, s.site_id)]
    skipped = [s.name for s in sites if !haskey(scenarios_by_site, s.site_id)]
    if !isempty(skipped)
        @info "excluding sites with no real NOAA weather scenarios from stochastic sizing" skipped
    end

    per_site_options = Dict(
        site.site_id => stochastic_candidate_options(site, scenarios_by_site[site.site_id],
                                                       growth_multiplier, electricity_prices, demand_charges)
        for site in priced_sites
    )
    infeasible_sites = [site.name for site in priced_sites if isempty(per_site_options[site.site_id])]

    items = StochasticCandidateOption[]
    for site in priced_sites
        append!(items, per_site_options[site.site_id])
    end
    n = length(items)
    max_wait_hours = max_mean_wait_minutes / 60.0

    model = Model(HiGHS.Optimizer)
    set_silent(model)
    @variable(model, y[1:n], Bin)

    for site in priced_sites
        isempty(per_site_options[site.site_id]) && continue
        idxs = findall(item -> item.site.site_id == site.site_id, items)
        @constraint(model, sum(y[i] for i in idxs) == 1)
    end

    @constraint(model, sum(y[i] * items[i].capex_usd for i in 1:n) <= budget_usd)

    z = Dict{Tuple{Int,Int},VariableRef}()
    for i in 1:n, s in 1:length(items[i].scenario_mean_wait_hours)
        gap = items[i].scenario_mean_wait_hours[s] - max_wait_hours
        gap <= 0 && continue  # this candidate/scenario combo never violates the target
        z[(i, s)] = @variable(model, binary = true, base_name = "z_$(i)_$(s)")
        @constraint(model, gap <= gap * (z[(i, s)] + (1 - y[i])))
    end

    for site in priced_sites
        idxs = findall(item -> item.site.site_id == site.site_id, items)
        isempty(idxs) && continue
        scenarios = scenarios_by_site[site.site_id]
        @constraint(model, sum(
            scenarios[s].probability * z[(i, s)]
            for i in idxs for s in 1:length(items[i].scenario_mean_wait_hours) if haskey(z, (i, s))
        ) <= epsilon)
    end

    @objective(model, Min, sum(
        y[i] * (items[i].capex_usd + items[i].expected_annualized_waiting_cost_usd +
                items[i].annualized_electricity_cost_usd + items[i].annualized_demand_charge_usd)
        for i in 1:n
    ))

    optimize!(model)
    status = termination_status(model)

    selected = StochasticCandidateOption[]
    if status == MOI.OPTIMAL || status == MOI.TIME_LIMIT
        selected = [items[i] for i in 1:n if value(y[i]) > 0.5]
    end

    violation_rate_by_site = Dict{String,Float64}()
    for o in selected
        scenarios = scenarios_by_site[o.site.site_id]
        violation_rate_by_site[o.site.site_id] = sum(
            scenarios[s].probability for s in 1:length(o.scenario_mean_wait_hours)
            if o.scenario_mean_wait_hours[s] > max_wait_hours;
            init=0.0
        )
    end

    return StochasticPortfolioResult(
        selected, infeasible_sites,
        sum(o.capex_usd for o in selected; init=0.0),
        sum(o.expected_annualized_waiting_cost_usd for o in selected; init=0.0),
        sum(o.annualized_electricity_cost_usd for o in selected; init=0.0),
        sum(o.annualized_demand_charge_usd for o in selected; init=0.0),
        violation_rate_by_site,
        status,
    )
end
