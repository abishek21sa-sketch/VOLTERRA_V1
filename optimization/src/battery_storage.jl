"""
Battery storage *sizing* MILP: for each real site, decide whether to install a peak-shaving
battery, and at which real published duration, to minimize net annualized cost (battery capex +
O&M, minus the real demand-charge savings it produces) — subject to a capital budget.

**Sizing only — dispatch is NOT built here.** The Phase 7 roadmap item is "battery storage
sizing/dispatch"; this file is the sizing half. Real hour-by-hour dispatch scheduling (when to
charge/discharge across a day) would need either real interval load data (Tesla publishes none —
same reason `ml/` has no real demand-forecasting model, see its README) or a new modeled synthetic
hourly load-shape assumption significant enough to deserve its own careful, honestly-labeled
scope — not attempted here rather than bolted on as an afterthought. What this file answers
instead is the economics question dispatch would need answered first anyway: is a battery worth
installing at all, and how big, assuming it can fully shave the site's modeled peak demand for its
own rated duration (a standard simplifying assumption in real commercial BESS sizing studies).

**Real**: each real duration's NREL/NLR ATB cost (`load_battery_storage_costs`, Phase 7 — a real,
government-published national technology-class baseline, not a per-vendor quote, not modeled the
way `capacity_selection.jl`'s `COST_PER_STALL_USD` is); each real site's `stall_count`/
`max_power_kw`; each real site's OpenEI/URDB demand-charge rate where available
(`load_demand_charges`, Phase 7, partial coverage — see that function's docstring).
**Modeled**: the peak-shave assumption itself (a battery is sized to exactly
`nameplate_peak_kw(tier)`, i.e. sized to fully shave the same modeled peak demand
`capacity_selection.jl`'s demand-charge cost term uses — the two models share this one definition,
not two independently-invented ones, see `nameplate_peak_kw`); `BATTERY_LIFETIME_YEARS` (a
standard grid-storage planning assumption, not fit to a specific real product or vendor
datasheet).

Per this project's discipline, results report capex, annualized O&M, and annualized demand-charge
savings as three separate totals — the objective necessarily nets them against each other to
decide what's worth building, but nothing here presents "net benefit" as a real observed dollar
figure Tesla or any utility has published.
"""

using JuMP
using HiGHS

const BATTERY_LIFETIME_YEARS = 15.0  # MODELED: standard Li-ion grid-storage planning assumption
                                       # (widely used in DOE/NREL storage techno-economic studies),
                                       # not fit to a specific vendor or product datasheet. The ATB
                                       # cost data pulled by nrel_atb.py doesn't include a lifetime
                                       # figure itself.

annualized_battery_capex_usd(battery_kw::Float64, capex_usd_per_kw::Float64) =
    battery_kw * capex_usd_per_kw / BATTERY_LIFETIME_YEARS

annualized_battery_om_usd(battery_kw::Float64, fixed_om_usd_per_kw_year::Float64) =
    battery_kw * fixed_om_usd_per_kw_year

"""
    annualized_demand_charge_savings_usd(shaved_kw, demand_charge_usd_per_kw) -> Float64

Same real \$/kW rate and annualization (`MONTHS_PER_YEAR`, from capacity_selection.jl) the
demand-charge cost term itself uses — a battery that shaves `shaved_kw` off the site's billed
peak saves exactly what that many kW would otherwise have cost.
"""
annualized_demand_charge_savings_usd(shaved_kw::Float64, demand_charge_usd_per_kw::Float64) =
    shaved_kw * demand_charge_usd_per_kw * MONTHS_PER_YEAR

struct BatteryCandidate
    site::WarehouseSite
    duration_hours::Int
    battery_kw::Float64
    capex_usd::Float64                # one-time
    annualized_capex_usd::Float64     # capex_usd / BATTERY_LIFETIME_YEARS
    annualized_om_usd::Float64
    annualized_demand_charge_savings_usd::Float64
end

"""
    battery_candidates(site, tier, demand_charge_usd_per_kw, cost_menu) -> Vector{BatteryCandidate}

One candidate per real ATB duration in `cost_menu`, each sized to fully shave `tier`'s modeled
nameplate peak (`nameplate_peak_kw`, shared with capacity_selection.jl). "No battery" isn't a
candidate here — it's the implicit outcome when a site's optimal MILP selection count is zero
(see `optimize_battery_portfolio`'s `<= 1`, not `== 1`, per-site constraint), not represented as a
zero-cost row, so the MILP is free to reject every real option when none pay for themselves.
"""
function battery_candidates(site::WarehouseSite, tier::TierOption, demand_charge_usd_per_kw::Float64,
                             cost_menu::Vector{BatteryStorageCost})
    peak_kw = nameplate_peak_kw(tier)
    return [
        BatteryCandidate(
            site, c.duration_hours, peak_kw,
            peak_kw * c.capex_usd_per_kw,
            annualized_battery_capex_usd(peak_kw, c.capex_usd_per_kw),
            annualized_battery_om_usd(peak_kw, c.fixed_om_usd_per_kw_year),
            annualized_demand_charge_savings_usd(peak_kw, demand_charge_usd_per_kw),
        )
        for c in cost_menu
    ]
end

struct BatteryPortfolioResult
    selected::Vector{BatteryCandidate}
    total_capex_usd::Float64
    total_annualized_om_usd::Float64
    total_annualized_demand_charge_savings_usd::Float64
    net_annual_benefit_usd::Float64  # savings - (annualized capex + O&M); positive = worth building
    termination_status::MOI.TerminationStatusCode
end

"""
    optimize_battery_portfolio(sites; budget_usd, tiers=Dict(), demand_charges=load_demand_charges(),
                                cost_menu=load_battery_storage_costs())

Solves the MILP: for each site, select at most one real battery duration (or none), minimizing
net annualized cost (annualized capex + O&M − annualized demand-charge savings), subject to a
one-time capital budget across all installed batteries.

`tiers` lets a caller price battery sizing against a *post-upgrade* tier from
`optimize_capacity_portfolio`'s result instead of each site's real current
stall_count/max_power_kw (the default when a site is absent from `tiers`) — useful for combining
both models, but not required to use this one standalone.

A site with no real demand-charge rate on file (`demand_charges` missing that `site_id`) is
excluded from consideration entirely, not silently priced at a fallback the way
`capacity_selection.jl` does for its own missing-rate case — sizing a battery against a fabricated
demand-charge rate would risk an economically wrong build/no-build decision, whereas
`capacity_selection.jl`'s fallback only ever affects a cost *total*, never a discrete decision.
"""
function optimize_battery_portfolio(sites::Vector{WarehouseSite}; budget_usd::Float64,
                                     tiers::Dict{String,TierOption}=Dict{String,TierOption}(),
                                     demand_charges::Dict{String,Float64}=load_demand_charges(),
                                     cost_menu::Vector{BatteryStorageCost}=load_battery_storage_costs())
    priced_sites = [s for s in sites if haskey(demand_charges, s.site_id)]
    skipped = [s.name for s in sites if !haskey(demand_charges, s.site_id)]
    if !isempty(skipped)
        @info "excluding sites with no real OpenEI demand-charge rate from battery sizing (DEMO_KEY partial coverage)" skipped
    end

    items = BatteryCandidate[]
    for site in priced_sites
        tier = get(tiers, site.site_id, TierOption(site.stall_count, site.max_power_kw))
        append!(items, battery_candidates(site, tier, demand_charges[site.site_id], cost_menu))
    end
    n = length(items)

    model = Model(HiGHS.Optimizer)
    set_silent(model)
    @variable(model, y[1:n], Bin)

    for site in priced_sites
        idxs = findall(item -> item.site.site_id == site.site_id, items)
        isempty(idxs) && continue
        @constraint(model, sum(y[i] for i in idxs) <= 1)  # at most one duration -- zero is a valid, real outcome
    end

    @constraint(model, sum(y[i] * items[i].capex_usd for i in 1:n) <= budget_usd)
    @objective(model, Min, sum(
        y[i] * (items[i].annualized_capex_usd + items[i].annualized_om_usd -
                items[i].annualized_demand_charge_savings_usd)
        for i in 1:n
    ))

    optimize!(model)
    status = termination_status(model)

    selected = BatteryCandidate[]
    if status == MOI.OPTIMAL || status == MOI.TIME_LIMIT
        selected = [items[i] for i in 1:n if value(y[i]) > 0.5]
    end

    total_capex = sum(c.capex_usd for c in selected; init=0.0)
    total_annualized_capex = sum(c.annualized_capex_usd for c in selected; init=0.0)
    total_om = sum(c.annualized_om_usd for c in selected; init=0.0)
    total_savings = sum(c.annualized_demand_charge_savings_usd for c in selected; init=0.0)

    return BatteryPortfolioResult(
        selected, total_capex, total_om, total_savings,
        total_savings - total_annualized_capex - total_om,
        status,
    )
end
