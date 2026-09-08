"""
Phase 8 demo: two-stage stochastic capacity + power-tier selection over the real network, under
real per-site NOAA weather scenario uncertainty (14 real forecast periods per site) instead of a
single 20°C point estimate. Three parts: (1) a capital budget sweep at the default chance-
constraint threshold, mirroring the Phase 4 demo's budget sweep, at the same 15-min service target
used throughout this project; (2) real per-site scenario detail at the natural budget optimum; (3)
a chance-constraint (epsilon) sweep at a target derived from that real detail, not guessed — see
below for why.

Run from the optimization/ directory:
    julia --project=. examples/stochastic_capacity_selection_demo.jl

Real: site data, per-state electricity prices, per-site demand charges (same partial DEMO_KEY
coverage as the Phase 4/7 demos), and per-site NOAA weather scenarios (Phase 6 — 15 of 17 real
sites have one; the other 2 are excluded from this model entirely, not fallback-priced, see
stochastic_capacity_selection.jl's module docstring). Modeled: demand growth multiplier, capital
costs, value of time, energy per session, peak demand coincidence (all inherited from
capacity_selection.jl), plus the chance-constraint threshold `EPSILON_DEFAULT` itself (a policy
choice). Demand-growth/adoption-rate uncertainty is NOT modeled this phase — see that file's
module docstring for why.

**Why the epsilon sweep's target isn't the project's usual 15-min service cap**: at 1.3x growth,
this real network's real weather variability tops out around 5 minutes of mean wait (see the
printed per-site scenario range below) — nowhere near 15 minutes. Sweeping epsilon against a
15-min target would just be flat (every real scenario always complies, chance constraint never
binds) — a real finding in itself (this network's real weather swings aren't severe enough to
threaten a generous service target at this demand), but not one that demonstrates the chance
constraint's actual mechanics. So the epsilon sweep below instead targets the REAL median
(site, scenario) wait observed at the budget optimum: a target derived from what the model
actually produced on real data, not a constant picked to force an interesting-looking result.
"""

using VolterraOptimization

const GROWTH_MULTIPLIER = 1.3  # same modeled scenario as the Phase 4 demo, for a fair comparison
const MAX_MEAN_WAIT_MINUTES = 15.0  # this project's standard service target (see capacity_selection.jl)
const BUDGET_SWEEP_USD = [0.0, 50_000.0, 150_000.0, 300_000.0, 340_000.0, 1_000_000.0]
const EPSILON_SWEEP = [1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.01]
const EPSILON_SWEEP_BUDGET_USD = 1_000_000.0

function main()
    sites = load_sites()
    prices = load_electricity_prices()
    demand_charges = load_demand_charges()
    scenarios = load_temperature_scenarios()

    n_scenarios = isempty(scenarios) ? 0 : first(length(v) for v in values(scenarios))
    println("="^100)
    println("VOLTERRA Phase 8 — two-stage stochastic capacity selection with chance constraint (JuMP + HiGHS)")
    println("$(length(sites)) real sites, $(length(scenarios)) with real NOAA weather scenarios " *
             "($(n_scenarios) real forecast periods each), $(length(demand_charges)) with a real " *
             "OpenEI demand-charge rate.")
    println("Demand scenario: $(GROWTH_MULTIPLIER)x baseline (modeled). Chance-constraint default: " *
             "epsilon=$(EPSILON_DEFAULT) (modeled policy).")
    println("="^100)

    println("\nBudget sweep at default epsilon=$(EPSILON_DEFAULT), $(MAX_MEAN_WAIT_MINUTES)-min target " *
             "(real efficient frontier, EXPECTED cost over real weather):")
    println(rpad("budget", 14), rpad("status", 12), rpad("upgrades", 10), rpad("capex", 14),
             rpad("E[wait cost]", 16), rpad("electricity", 14), rpad("demand chg", 14), "max violation rate")
    local budget_result
    for budget in BUDGET_SWEEP_USD
        budget_result = optimize_stochastic_capacity_portfolio(sites; budget_usd=budget, growth_multiplier=GROWTH_MULTIPLIER,
                                                                 max_mean_wait_minutes=MAX_MEAN_WAIT_MINUTES,
                                                                 electricity_prices=prices, demand_charges=demand_charges,
                                                                 scenarios_by_site=scenarios)
        n_upgraded = count(o -> o.is_upgrade, budget_result.selected)
        max_violation = isempty(budget_result.violation_rate_by_site) ? 0.0 : maximum(values(budget_result.violation_rate_by_site))
        println(
            rpad("\$" * string(Int(budget)), 14),
            rpad(string(budget_result.termination_status), 12),
            rpad(string(n_upgraded), 10),
            rpad("\$" * string(round(Int, budget_result.total_capex_usd)), 14),
            rpad("\$" * string(round(Int, budget_result.total_expected_annualized_waiting_cost_usd)), 16),
            rpad("\$" * string(round(Int, budget_result.total_annualized_electricity_cost_usd)), 14),
            rpad("\$" * string(round(Int, budget_result.total_annualized_demand_charge_usd)), 14),
            string(round(max_violation, digits=3)),
        )
    end

    println("\n", "-"^100)
    println("Detail at \$1,000,000 budget, default epsilon=$(EPSILON_DEFAULT) (natural optimum, see sweep above):")
    for o in budget_result.selected
        tag = o.is_upgrade ? "UPGRADE" : "no change"
        vrate = get(budget_result.violation_rate_by_site, o.site.site_id, 0.0)
        println(
            "  ", rpad(o.site.name, 45),
            " -> ", o.tier.stalls, " stalls @ ", o.tier.power_kw, "kW  [", rpad(tag, 9), "]",
            "  E[wait cost]=\$", round(Int, o.expected_annualized_waiting_cost_usd),
            "  violation rate=", round(vrate, digits=3),
            o.is_upgrade ? "  capex=\$$(round(Int, o.capex_usd))" : "",
        )
    end
    if !isempty(budget_result.infeasible_sites)
        println("\n  No tier stable across every real weather scenario at this demand: ", budget_result.infeasible_sites)
    end

    println("\nReal per-site scenario wait range (min real scenario wait -> max real scenario wait, minutes):")
    all_waits_min = Float64[]
    for o in budget_result.selected
        waits = o.scenario_mean_wait_hours .* 60
        append!(all_waits_min, waits)
        lo, hi = extrema(waits)
        println("  ", rpad(o.site.name, 45), round(lo, digits=3), " -> ", round(hi, digits=3))
    end
    sort!(all_waits_min)
    real_median_wait_min = all_waits_min[(length(all_waits_min) + 1) ÷ 2]
    println("\nReal median (site, scenario) wait across the natural-optimum network: " *
             "$(round(real_median_wait_min, digits=3)) min -- vs. this project's usual " *
             "$(MAX_MEAN_WAIT_MINUTES)-min target, real weather variability at $(GROWTH_MULTIPLIER)x growth " *
             "never gets close, which is why the epsilon sweep below targets this real median instead.")

    println("\n", "-"^100)
    println("Chance-constraint sweep at a fixed \$$(Int(EPSILON_SWEEP_BUDGET_USD)) budget, target = the real " *
             "median wait above ($(round(real_median_wait_min, digits=3)) min) -- not a guessed constant, so " *
             "roughly half of real (site, scenario) pairs sit on each side of it by construction:")
    println(rpad("epsilon", 12), rpad("status", 12), rpad("upgrades", 10), rpad("total cost", 16),
             rpad("infeasible", 12), "max violation rate")
    for eps in EPSILON_SWEEP
        result = optimize_stochastic_capacity_portfolio(sites; budget_usd=EPSILON_SWEEP_BUDGET_USD,
                                                          growth_multiplier=GROWTH_MULTIPLIER,
                                                          max_mean_wait_minutes=real_median_wait_min,
                                                          epsilon=eps, electricity_prices=prices,
                                                          demand_charges=demand_charges, scenarios_by_site=scenarios)
        n_upgraded = count(o -> o.is_upgrade, result.selected)
        total_cost = result.total_capex_usd + result.total_expected_annualized_waiting_cost_usd +
                     result.total_annualized_electricity_cost_usd + result.total_annualized_demand_charge_usd
        max_violation = isempty(result.violation_rate_by_site) ? 0.0 : maximum(values(result.violation_rate_by_site))
        println(
            rpad(string(eps), 12),
            rpad(string(result.termination_status), 12),
            rpad(string(n_upgraded), 10),
            rpad("\$" * string(round(Int, total_cost)), 16),
            rpad(string(length(result.infeasible_sites)), 12),
            string(round(max_violation, digits=3)),
        )
    end

    println("\n", "="^100)
end

main()
