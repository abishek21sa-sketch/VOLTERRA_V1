"""
Phase 7 demo: battery storage sizing MILP over the real network — which real sites, at which
real NREL/NLR ATB duration, are worth a peak-shaving battery under real OpenEI/URDB demand-charge
rates, swept across a capital budget to show the real tradeoff.

Run from the optimization/ directory:
    julia --project=. examples/battery_storage_demo.jl

Real: site_id, stall_count, max_power_kw (Tesla); demand-charge rate (OpenEI/URDB, Phase 7 —
partial real coverage, sites without one are excluded from consideration entirely, not
fallback-priced, see battery_storage.jl's module docstring); battery storage cost per real
duration (NREL/NLR ATB, Phase 7). Modeled: the peak-shave sizing assumption
(`nameplate_peak_kw`, shared with capacity_selection.jl) and `BATTERY_LIFETIME_YEARS`.
"""

using VolterraOptimization

const BUDGET_SWEEP_USD = [0.0, 200_000.0, 500_000.0, 1_000_000.0, 5_000_000.0]

function main()
    sites = load_sites()
    demand_charges = load_demand_charges()
    battery_menu = load_battery_storage_costs()

    println("="^100)
    println("VOLTERRA Phase 7 — battery storage sizing MILP (JuMP + HiGHS)")
    println("$(length(sites)) real sites, $(length(demand_charges)) with a real OpenEI demand-charge rate " *
             "(only these are considered — see battery_storage.jl), $(length(battery_menu)) real ATB durations.")
    println("="^100)

    println("\nReal ATB commercial battery storage cost by duration:")
    for c in battery_menu
        println("  $(c.duration_hours)hr:  capex=\$$(round(Int, c.capex_usd_per_kw))/kW   " *
                 "fixed O&M=\$$(round(c.fixed_om_usd_per_kw_year, digits=1))/kW-yr")
    end

    println("\nBudget sweep — how many real sites get a battery, and net annual benefit:")
    println(rpad("budget", 14), rpad("status", 12), rpad("batteries", 11), rpad("capex", 14),
             rpad("annual O&M", 14), rpad("annual savings", 16), "net annual benefit")
    local last_result
    for budget in BUDGET_SWEEP_USD
        result = optimize_battery_portfolio(sites; budget_usd=budget,
                                             demand_charges=demand_charges, cost_menu=battery_menu)
        last_result = result
        println(
            rpad("\$" * string(Int(budget)), 14),
            rpad(string(result.termination_status), 12),
            rpad(string(length(result.selected)), 11),
            rpad("\$" * string(round(Int, result.total_capex_usd)), 14),
            rpad("\$" * string(round(Int, result.total_annualized_om_usd)), 14),
            rpad("\$" * string(round(Int, result.total_annualized_demand_charge_savings_usd)), 16),
            "\$" * string(round(Int, result.net_annual_benefit_usd)),
        )
    end

    println("\n", "-"^100)
    println("Detail at the largest budget swept (\$$(Int(BUDGET_SWEEP_USD[end])) — see sweep above):")
    for c in last_result.selected
        println(
            "  ", rpad(c.site.name, 45),
            " -> ", c.duration_hours, "hr battery, ", round(Int, c.battery_kw), " kW",
            "  capex=\$", round(Int, c.capex_usd),
            "  annual O&M=\$", round(Int, c.annualized_om_usd),
            "  annual savings=\$", round(Int, c.annualized_demand_charge_savings_usd),
        )
    end
    if isempty(last_result.selected)
        println("  (no site's real demand-charge rate justified a battery under this real ATB pricing)")
    end

    println("\n", "="^100)
end

main()
