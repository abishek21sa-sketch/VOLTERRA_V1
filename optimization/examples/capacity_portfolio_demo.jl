"""
Phase 4 demo: capacity + power-tier selection MILP over the real network, at a moderate
demand-growth scenario (1.3x), swept across a range of capital budgets to show the real
efficient-frontier tradeoff between capital spend and annualized cost (waiting + electricity +
grid demand charge).

Run from the optimization/ directory:
    julia --project=. examples/capacity_portfolio_demo.jl

Site data (stall_count, max_power_kw, state), per-state electricity prices, and per-site demand
charges are real, read live from the warehouse (Phase 4 + Phase 6 + Phase 7). Demand-charge
coverage is real but partial — OpenEI/URDB's `DEMO_KEY` allows only a handful of real per-site
rates per hour (see ../src/warehouse_io.jl's `load_demand_charges` and
../../ingestion/supplemental/openei.py) — sites without one use the fallback described in
capacity_selection.jl's `candidate_options`. Everything else — demand growth, capital costs, value
of time, energy consumed per session, peak demand coincidence — is modeled; see
capacity_selection.jl's module docstring and ../../docs/data-sources.md.
"""

using VolterraOptimization

const GROWTH_MULTIPLIER = 1.3  # modeled scenario: "30% more demand than today's assumed baseline"
const MAX_MEAN_WAIT_MINUTES = 15.0
const BUDGET_SWEEP_USD = [0.0, 50_000.0, 150_000.0, 300_000.0, 340_000.0, 1_000_000.0]

function main()
    sites = load_sites()
    prices = load_electricity_prices()  # loaded once, passed explicitly -- avoids reopening the
                                         # warehouse on every budget-sweep iteration below
    demand_charges = load_demand_charges()
    println("="^100)
    println("VOLTERRA Phase 4+6+7 — capacity/power-tier selection MILP (JuMP + HiGHS)")
    println("$(length(sites)) real sites, $(length(prices)) real state electricity prices, " *
             "$(length(demand_charges)) real per-site demand charges (partial — DEMO_KEY limited).")
    println("Demand scenario: $(GROWTH_MULTIPLIER)x baseline (modeled).")
    println("="^100)

    println("\nBudget sweep — efficient frontier (capital spend vs. annualized waiting + electricity + demand-charge cost):")
    println(rpad("budget", 14), rpad("status", 12), rpad("upgrades", 10), rpad("capex", 14),
             rpad("wait cost", 16), rpad("electricity cost", 20), "demand charge")
    for budget in BUDGET_SWEEP_USD
        result = optimize_capacity_portfolio(sites; budget_usd=budget, growth_multiplier=GROWTH_MULTIPLIER,
                                              max_mean_wait_minutes=MAX_MEAN_WAIT_MINUTES,
                                              electricity_prices=prices, demand_charges=demand_charges)
        n_upgraded = count(o -> o.is_upgrade, result.selected)
        println(
            rpad("\$" * string(Int(budget)), 14),
            rpad(string(result.termination_status), 12),
            rpad(string(n_upgraded), 10),
            rpad("\$" * string(round(Int, result.total_capex_usd)), 14),
            rpad("\$" * string(round(Int, result.total_annualized_waiting_cost_usd)), 16),
            rpad("\$" * string(round(Int, result.total_annualized_electricity_cost_usd)), 20),
            "\$" * string(round(Int, result.total_annualized_demand_charge_usd)),
        )
    end

    println("\n", "-"^100)
    println("Detail at the natural optimum (\$1,000,000 budget — see sweep above, additional budget")
    println("past this point buys nothing: remaining sites aren't cost-effective to upgrade):")
    result = optimize_capacity_portfolio(sites; budget_usd=1_000_000.0, growth_multiplier=GROWTH_MULTIPLIER,
                                          max_mean_wait_minutes=MAX_MEAN_WAIT_MINUTES,
                                          electricity_prices=prices, demand_charges=demand_charges)
    for o in result.selected
        tag = o.is_upgrade ? "UPGRADE" : "no change"
        price = get(prices, o.site.state, missing)
        println(
            "  ", rpad(o.site.name, 45),
            " -> ", o.tier.stalls, " stalls @ ", o.tier.power_kw, "kW  [", rpad(tag, 9), "]",
            "  util=", round(o.queueing.utilization, digits=2),
            "  mean wait=", round(o.queueing.mean_wait_hours * 60, digits=2), " min",
            "  elec=\$", round(Int, o.annualized_electricity_cost_usd), " (\$", price, "/kWh)",
            "  demand=\$", round(Int, o.annualized_demand_charge_usd),
            o.is_upgrade ? "  capex=\$$(round(Int, o.capex_usd))" : "",
        )
    end
    if !isempty(result.below_service_target_sites)
        println("\n  Below the $(MAX_MEAN_WAIT_MINUTES)-min service target: ", result.below_service_target_sites)
    end
    if !isempty(result.infeasible_sites)
        println("\n  No stable tier exists at this demand (needs a larger tier menu): ", result.infeasible_sites)
    end

    println("\n", "="^100)
end

main()
