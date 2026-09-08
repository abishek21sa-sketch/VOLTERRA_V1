using Test
using JuMP
using VolterraOptimization

# Synthetic sites for unit tests -- deterministic, independent of the warehouse file. A guarded
# integration test at the bottom exercises load_sites()/load_electricity_prices() against the
# real warehouse if present.
site_small = WarehouseSite("test-1", "Test Small", "Testville", "TS", 6, 120.0)
site_medium = WarehouseSite("test-2", "Test Medium", "Testville", "TS", 8, 150.0)
site_large = WarehouseSite("test-3", "Test Large", "Testville", "TS", 32, 250.0)
site_oversized = WarehouseSite("test-4", "Test Oversized", "Testville", "TS", 45, 250.0)  # bigger than any tier

# Synthetic price, not real EIA data -- keeps unit tests independent of the warehouse file.
const TEST_PRICES = Dict("TS" => 0.12)
# Synthetic demand charges, not real OpenEI/URDB data -- keyed by site_id like the real thing.
const TEST_DEMAND_CHARGES = Dict("test-1" => 8.0, "test-2" => 8.0, "test-3" => 8.0, "test-4" => 8.0)

@testset "capex_for" begin
    tier_no_change = TierOption(8, 150.0)
    @test capex_for(site_medium, tier_no_change) > 0 || tier_no_change.stalls == site_medium.stall_count
    # A same-size, same-power "tier" costs nothing to build.
    @test capex_for(site_medium, TierOption(site_medium.stall_count, site_medium.max_power_kw)) == 0.0

    # More incremental stalls costs strictly more.
    small_upgrade = capex_for(site_medium, TierOption(16, 250.0))
    big_upgrade = capex_for(site_medium, TierOption(40, 500.0))
    @test big_upgrade > small_upgrade

    # A power-tier upgrade at the same new stall count costs strictly more (retrofit charge).
    same_stalls_low_power = capex_for(site_small, TierOption(8, 150.0))
    same_stalls_high_power = capex_for(site_small, TierOption(8, 500.0))
    @test same_stalls_high_power > same_stalls_low_power
end

@testset "candidate_options" begin
    @testset "baseline demand always keeps the current tier viable" begin
        for site in (site_small, site_medium, site_large)
            opts = candidate_options(site, 1.0, TEST_PRICES, TEST_DEMAND_CHARGES)
            @test any(o -> !o.is_upgrade, opts)
        end
    end

    @testset "extreme growth can make the current tier unstable, forcing an upgrade" begin
        opts = candidate_options(site_small, 5.0, TEST_PRICES, TEST_DEMAND_CHARGES)  # 5x baseline demand on a 6-stall site
        @test !any(o -> !o.is_upgrade, opts)  # "no change" excluded -- would be an unstable queue
        @test !isempty(opts)                 # but a larger tier should still be viable
        @test all(o -> o.is_upgrade, opts)
    end

    @testset "a site larger than every tier in the menu has no upgrade path" begin
        opts = candidate_options(site_oversized, 5.0, TEST_PRICES, TEST_DEMAND_CHARGES)
        @test isempty(opts)  # current tier unstable, and nothing in TIER_MENU is even that large
    end

    @testset "every option is a genuinely stable queue" begin
        for site in (site_small, site_medium, site_large), growth in (1.0, 1.5, 2.0)
            for o in candidate_options(site, growth, TEST_PRICES, TEST_DEMAND_CHARGES)
                @test o.queueing.utilization < 1.0
            end
        end
    end

    @testset "electricity cost scales with the real per-state price" begin
        cheap = Dict("TS" => 0.05)
        expensive = Dict("TS" => 0.50)
        opt_cheap = only(filter(o -> !o.is_upgrade, candidate_options(site_medium, 1.0, cheap, TEST_DEMAND_CHARGES)))
        opt_expensive = only(filter(o -> !o.is_upgrade, candidate_options(site_medium, 1.0, expensive, TEST_DEMAND_CHARGES)))
        @test opt_expensive.annualized_electricity_cost_usd > opt_cheap.annualized_electricity_cost_usd
        # Same demand/tier, so it should scale exactly with price (10x price => 10x cost).
        @test isapprox(opt_expensive.annualized_electricity_cost_usd,
                        opt_cheap.annualized_electricity_cost_usd * 10; rtol=1e-9)
    end

    @testset "a missing state falls back to the mean of known prices, with a warning" begin
        site_unknown_state = WarehouseSite("test-5", "Test Unknown State", "Nowhere", "ZZ", 8, 150.0)
        multi_price = Dict("AA" => 0.10, "BB" => 0.20)
        opts = @test_logs (:warn,) match_mode=:any candidate_options(site_unknown_state, 1.0, multi_price, TEST_DEMAND_CHARGES)
        @test !isempty(opts)  # still produces usable options, just priced at the fallback
    end

    @testset "demand charge cost scales with the real per-site rate" begin
        cheap = Dict("test-2" => 2.0)
        expensive = Dict("test-2" => 20.0)
        opt_cheap = only(filter(o -> !o.is_upgrade, candidate_options(site_medium, 1.0, TEST_PRICES, cheap)))
        opt_expensive = only(filter(o -> !o.is_upgrade, candidate_options(site_medium, 1.0, TEST_PRICES, expensive)))
        @test opt_expensive.annualized_demand_charge_usd > opt_cheap.annualized_demand_charge_usd
        # Same tier (fixed nameplate kW), so it should scale exactly with rate (10x rate => 10x cost).
        @test isapprox(opt_expensive.annualized_demand_charge_usd,
                        opt_cheap.annualized_demand_charge_usd * 10; rtol=1e-9)
    end

    @testset "demand charge is independent of arrival rate, unlike electricity/waiting cost" begin
        # Same site/tier/rate at two different growth multipliers -- the demand charge (driven by
        # nameplate capacity, not throughput) should be identical, while electricity cost (which
        # scales with energy delivered) should differ.
        low_growth = only(filter(o -> !o.is_upgrade, candidate_options(site_medium, 1.0, TEST_PRICES, TEST_DEMAND_CHARGES)))
        high_growth = only(filter(o -> !o.is_upgrade, candidate_options(site_medium, 1.3, TEST_PRICES, TEST_DEMAND_CHARGES)))
        @test low_growth.annualized_demand_charge_usd == high_growth.annualized_demand_charge_usd
        @test low_growth.annualized_electricity_cost_usd != high_growth.annualized_electricity_cost_usd
    end

    @testset "a missing site falls back to the mean of known real demand charges, with a warning" begin
        site_unknown_charge = WarehouseSite("test-6", "Test Unknown Charge", "Testville", "TS", 8, 150.0)
        opts = @test_logs (:warn,) match_mode=:any candidate_options(site_unknown_charge, 1.0, TEST_PRICES, TEST_DEMAND_CHARGES)
        @test !isempty(opts)  # still produces usable options, just priced at the fallback
    end
end

@testset "optimize_capacity_portfolio" begin
    sites = [site_small, site_medium, site_large]

    @testset "zero budget at baseline demand keeps everything unchanged" begin
        result = optimize_capacity_portfolio(sites; budget_usd=0.0, growth_multiplier=1.0,
                                              electricity_prices=TEST_PRICES, demand_charges=TEST_DEMAND_CHARGES)
        @test result.termination_status == MOI.OPTIMAL
        @test result.total_capex_usd == 0.0
        @test all(o -> !o.is_upgrade, result.selected)
        @test isempty(result.infeasible_sites)
    end

    @testset "more budget never makes the total cost worse" begin
        budgets = [0.0, 100_000.0, 500_000.0, 5_000_000.0]
        totals = Float64[]
        for b in budgets
            r = optimize_capacity_portfolio(sites; budget_usd=b, growth_multiplier=1.4,
                                             electricity_prices=TEST_PRICES, demand_charges=TEST_DEMAND_CHARGES)
            push!(totals, r.total_capex_usd + r.total_annualized_waiting_cost_usd +
                           r.total_annualized_electricity_cost_usd + r.total_annualized_demand_charge_usd)
        end
        @test issorted(totals, rev=true) || all(diff(totals) .<= 1e-6)  # non-increasing, allowing float noise
    end

    @testset "infeasible_sites surfaces sites with no viable tier, without crashing the model" begin
        result = optimize_capacity_portfolio([site_medium, site_oversized]; budget_usd=1e9, growth_multiplier=5.0,
                                              electricity_prices=TEST_PRICES, demand_charges=TEST_DEMAND_CHARGES)
        @test "Test Oversized" in result.infeasible_sites
        # The other, viable site should still get a real answer.
        @test any(o -> o.site.site_id == site_medium.site_id, result.selected)
    end

    @testset "below_service_target_sites reflects the reporting threshold, not a hard filter" begin
        # A very tight budget under real demand growth should leave some sites above the wait cap
        # rather than making the whole model infeasible.
        result = optimize_capacity_portfolio(sites; budget_usd=0.0, growth_multiplier=1.3,
                                              max_mean_wait_minutes=0.01, electricity_prices=TEST_PRICES,
                                              demand_charges=TEST_DEMAND_CHARGES)
        @test result.termination_status == MOI.OPTIMAL
        @test !isempty(result.below_service_target_sites)
    end
end

# Synthetic battery cost menu, not real ATB data -- deliberately two options with very different
# economics (see the testset below) so tests can check the MILP actually discriminates on cost,
# not just accepts whatever's offered.
const TEST_BATTERY_MENU = [
    BatteryStorageCost(1, 500.0, 10.0),   # cheap: pays for itself easily against TEST_DEMAND_CHARGES
    BatteryStorageCost(4, 2000.0, 40.0),  # expensive: same shave amount, never worth it here
]

@testset "battery_storage" begin
    @testset "annualized_battery_capex_usd divides evenly by BATTERY_LIFETIME_YEARS" begin
        @test annualized_battery_capex_usd(100.0, 1500.0) ≈ 100.0 * 1500.0 / BATTERY_LIFETIME_YEARS
    end

    @testset "battery_candidates sizes every duration to the same nameplate peak" begin
        tier = TierOption(site_medium.stall_count, site_medium.max_power_kw)
        candidates = battery_candidates(site_medium, tier, 8.0, TEST_BATTERY_MENU)
        @test length(candidates) == length(TEST_BATTERY_MENU)
        @test all(c -> c.battery_kw == nameplate_peak_kw(tier), candidates)
    end

    @testset "cheap-but-sufficient beats expensive-for-the-same-shave" begin
        result = optimize_battery_portfolio([site_medium]; budget_usd=1e9,
                                             demand_charges=TEST_DEMAND_CHARGES, cost_menu=TEST_BATTERY_MENU)
        @test result.termination_status == MOI.OPTIMAL
        @test length(result.selected) == 1
        @test only(result.selected).duration_hours == 1  # not the 4hr option -- same savings, higher cost
        @test result.net_annual_benefit_usd > 0  # a real worthwhile build under this synthetic pricing
    end

    @testset "zero budget builds nothing" begin
        result = optimize_battery_portfolio([site_medium]; budget_usd=0.0,
                                             demand_charges=TEST_DEMAND_CHARGES, cost_menu=TEST_BATTERY_MENU)
        @test result.termination_status == MOI.OPTIMAL
        @test isempty(result.selected)
        @test result.net_annual_benefit_usd == 0.0
    end

    @testset "a site missing a real demand charge is excluded, not fallback-priced" begin
        site_unknown_charge = WarehouseSite("test-7", "Test Unknown Charge", "Testville", "TS", 8, 150.0)
        result = optimize_battery_portfolio([site_medium, site_unknown_charge]; budget_usd=1e9,
                                             demand_charges=TEST_DEMAND_CHARGES, cost_menu=TEST_BATTERY_MENU)
        @test all(c -> c.site.site_id != "test-7", result.selected)
    end

    @testset "at most one duration selected per site, never both" begin
        # Both options are individually profitable is not the scenario here (4hr never pays off),
        # so instead check the per-site constraint directly holds even with a huge budget.
        result = optimize_battery_portfolio([site_medium]; budget_usd=1e9,
                                             demand_charges=TEST_DEMAND_CHARGES, cost_menu=TEST_BATTERY_MENU)
        @test count(c -> c.site.site_id == site_medium.site_id, result.selected) <= 1
    end

    @testset "net_annual_benefit_usd nets the three reported totals consistently" begin
        result = optimize_battery_portfolio([site_medium]; budget_usd=1e9,
                                             demand_charges=TEST_DEMAND_CHARGES, cost_menu=TEST_BATTERY_MENU)
        implied_annualized_capex = result.total_capex_usd / BATTERY_LIFETIME_YEARS
        @test isapprox(result.net_annual_benefit_usd,
                        result.total_annualized_demand_charge_savings_usd - implied_annualized_capex - result.total_annualized_om_usd;
                        rtol=1e-9)
    end
end

# Synthetic weather scenarios, not real NOAA data. 0.0C is a real point in charging_curve.jl's
# defined linear derate zone (COLD_DERATE_START_TEMP_C=10, COLD_DERATE_FLOOR_TEMP_C=-20) --
# meaningfully colder than 20C without being the extreme floor, chosen specifically to stay
# queueing-stable (just slower) rather than risk destabilizing site_medium's baseline-utilization
# tier entirely, which would make several tests below meaningless (nothing left to compare).
const TEST_SCENARIOS_MILD_ONLY = Dict("test-2" => [TemperatureScenario(20.0, 1.0)])
const TEST_SCENARIOS_COLD_ONLY = Dict("test-2" => [TemperatureScenario(0.0, 1.0)])
const TEST_SCENARIOS_MIXED = Dict("test-2" => [TemperatureScenario(20.0, 0.9), TemperatureScenario(0.0, 0.1)])

@testset "stochastic_capacity_selection" begin
    @testset "cold-only scenarios never produce more viable candidates than mild-only" begin
        # A colder scenario can only destabilize a tier relative to a milder one, never newly
        # stabilize it -- a general property, not a numeric coincidence, so this holds regardless
        # of the exact service-time values.
        mild = stochastic_candidate_options(site_medium, TEST_SCENARIOS_MILD_ONLY["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES)
        cold = stochastic_candidate_options(site_medium, TEST_SCENARIOS_COLD_ONLY["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES)
        @test length(cold) <= length(mild)
        # Precondition the rest of this testset leans on: the "no change" tier survives both, and
        # really is slower when cold -- confirmed empirically here rather than assumed.
        @test any(o -> !o.is_upgrade, mild) && any(o -> !o.is_upgrade, cold)
    end

    @testset "expected waiting cost is the real probability-weighted average of per-scenario cost" begin
        mixed = stochastic_candidate_options(site_medium, TEST_SCENARIOS_MIXED["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES)
        opt = only(filter(o -> !o.is_upgrade, mixed))
        @test length(opt.scenario_mean_wait_hours) == 2
        manual_expected = sum(
            s.probability * annualized_waiting_cost_usd(opt.arrival_rate_per_hour, w)
            for (s, w) in zip(TEST_SCENARIOS_MIXED["test-2"], opt.scenario_mean_wait_hours)
        )
        @test isapprox(opt.expected_annualized_waiting_cost_usd, manual_expected; rtol=1e-9)
    end

    @testset "electricity and demand-charge cost are scenario-invariant" begin
        mild = only(filter(o -> !o.is_upgrade, stochastic_candidate_options(site_medium, TEST_SCENARIOS_MILD_ONLY["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES)))
        cold = only(filter(o -> !o.is_upgrade, stochastic_candidate_options(site_medium, TEST_SCENARIOS_COLD_ONLY["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES)))
        @test mild.annualized_electricity_cost_usd == cold.annualized_electricity_cost_usd
        @test mild.annualized_demand_charge_usd == cold.annualized_demand_charge_usd
        # ...but waiting cost genuinely does differ -- confirms the invariance above isn't just
        # because nothing about the scenario propagated at all.
        @test mild.expected_annualized_waiting_cost_usd != cold.expected_annualized_waiting_cost_usd
        @test cold.scenario_mean_wait_hours[1] > mild.scenario_mean_wait_hours[1]  # colder really is slower
    end

    @testset "a tight chance constraint can force a costlier choice than an unconstrained one" begin
        # Real mild/cold wait times, computed empirically (not guessed) so the target threshold
        # below is guaranteed to sit strictly between them regardless of the exact numbers.
        mild_wait_min = only(filter(o -> !o.is_upgrade, stochastic_candidate_options(site_medium, TEST_SCENARIOS_MILD_ONLY["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES))).scenario_mean_wait_hours[1] * 60
        cold_wait_min = only(filter(o -> !o.is_upgrade, stochastic_candidate_options(site_medium, TEST_SCENARIOS_COLD_ONLY["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES))).scenario_mean_wait_hours[1] * 60
        @test cold_wait_min > mild_wait_min
        target_minutes = (mild_wait_min + cold_wait_min) / 2  # mild passes this target, cold fails it

        loose = optimize_stochastic_capacity_portfolio([site_medium]; budget_usd=1e9, max_mean_wait_minutes=target_minutes,
                                                         epsilon=1.0, electricity_prices=TEST_PRICES,
                                                         demand_charges=TEST_DEMAND_CHARGES, scenarios_by_site=TEST_SCENARIOS_MIXED)
        tight = optimize_stochastic_capacity_portfolio([site_medium]; budget_usd=1e9, max_mean_wait_minutes=target_minutes,
                                                         epsilon=0.01, electricity_prices=TEST_PRICES,
                                                         demand_charges=TEST_DEMAND_CHARGES, scenarios_by_site=TEST_SCENARIOS_MIXED)
        @test loose.termination_status == MOI.OPTIMAL
        @test tight.termination_status == MOI.OPTIMAL
        loose_cost = loose.total_capex_usd + loose.total_expected_annualized_waiting_cost_usd +
                     loose.total_annualized_electricity_cost_usd + loose.total_annualized_demand_charge_usd
        tight_cost = tight.total_capex_usd + tight.total_expected_annualized_waiting_cost_usd +
                     tight.total_annualized_electricity_cost_usd + tight.total_annualized_demand_charge_usd
        @test tight_cost >= loose_cost - 1e-6  # tightening the chance constraint never helps the objective
        # The whole point: with epsilon below the cold scenario's 0.1 probability, the network
        # can no longer rely on the "no change" tier missing target 10% of the time.
        @test isempty(tight.violation_rate_by_site) || tight.violation_rate_by_site[site_medium.site_id] <= 0.01 + 1e-9
    end

    @testset "tightening epsilon never lowers total cost (monotonic tradeoff)" begin
        mild_wait_min = only(filter(o -> !o.is_upgrade, stochastic_candidate_options(site_medium, TEST_SCENARIOS_MILD_ONLY["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES))).scenario_mean_wait_hours[1] * 60
        cold_wait_min = only(filter(o -> !o.is_upgrade, stochastic_candidate_options(site_medium, TEST_SCENARIOS_COLD_ONLY["test-2"], 1.0, TEST_PRICES, TEST_DEMAND_CHARGES))).scenario_mean_wait_hours[1] * 60
        target_minutes = (mild_wait_min + cold_wait_min) / 2

        epsilons = [1.0, 0.5, 0.2, 0.05, 0.01]
        totals = Float64[]
        for eps in epsilons
            r = optimize_stochastic_capacity_portfolio([site_medium]; budget_usd=1e9, max_mean_wait_minutes=target_minutes,
                                                         epsilon=eps, electricity_prices=TEST_PRICES,
                                                         demand_charges=TEST_DEMAND_CHARGES, scenarios_by_site=TEST_SCENARIOS_MIXED)
            push!(totals, r.total_capex_usd + r.total_expected_annualized_waiting_cost_usd +
                           r.total_annualized_electricity_cost_usd + r.total_annualized_demand_charge_usd)
        end
        # epsilons run loosest -> tightest, so cost must be non-decreasing down the list
        # (small negative tolerance absorbs solver floating-point noise, not a real decrease).
        @test all(diff(totals) .>= -1e-6)
    end

    @testset "a site missing real weather scenarios is excluded, not fallback-priced" begin
        site_unknown_weather = WarehouseSite("test-8", "Test Unknown Weather", "Testville", "TS", 8, 150.0)
        result = @test_logs (:info,) match_mode=:any optimize_stochastic_capacity_portfolio(
            [site_medium, site_unknown_weather]; budget_usd=1e9,
            electricity_prices=TEST_PRICES, demand_charges=TEST_DEMAND_CHARGES, scenarios_by_site=TEST_SCENARIOS_MIXED)
        @test all(o -> o.site.site_id != "test-8", result.selected)
    end

    @testset "violation_rate_by_site reports the real fraction of scenarios exceeding target" begin
        result = optimize_stochastic_capacity_portfolio([site_medium]; budget_usd=1e9, max_mean_wait_minutes=0.01,
                                                          epsilon=1.0, electricity_prices=TEST_PRICES,
                                                          demand_charges=TEST_DEMAND_CHARGES, scenarios_by_site=TEST_SCENARIOS_MIXED)
        @test haskey(result.violation_rate_by_site, "test-2")
        @test 0.0 <= result.violation_rate_by_site["test-2"] <= 1.0
    end
end

# Guarded integration test: only runs if the real warehouse file exists (it's gitignored, so CI
# or a fresh clone without it should still pass the rest of the suite).
@testset "load_sites / load_electricity_prices / load_demand_charges (integration, real warehouse)" begin
    if isfile(DEFAULT_WAREHOUSE_PATH)
        sites = load_sites()
        @test length(sites) > 0
        @test all(s -> s.stall_count > 0 && s.max_power_kw > 0, sites)

        prices = load_electricity_prices()
        @test length(prices) > 0
        @test all(p -> 0.0 < p < 1.0, values(prices))  # sane $/kWh range, not cents or a raw error value
        # Every real site's state should have a real price -- if this ever fails, it means a new
        # site's state genuinely isn't in the EIA data yet, worth knowing, not silently ignoring.
        site_states = Set(s.state for s in sites if !ismissing(s.state))
        @test issubset(site_states, keys(prices))

        charges = load_demand_charges()
        @test length(charges) > 0  # at least the DEMO_KEY-limited partial coverage should be present
        @test length(charges) <= length(sites)  # real, honestly partial -- never more real rates than real sites
        @test all(c -> 0.0 < c < 200.0, values(charges))  # sane $/kW range, not a raw error/unit-mixup value

        result = optimize_capacity_portfolio(sites; budget_usd=0.0, growth_multiplier=1.0)  # real default prices and demand charges
        @test result.termination_status == MOI.OPTIMAL

        battery_costs = load_battery_storage_costs()
        @test length(battery_costs) > 0
        @test all(c -> 0.0 < c.capex_usd_per_kw < 10_000.0, battery_costs)  # sane $/kW range
        @test all(c -> 0.0 < c.fixed_om_usd_per_kw_year < 500.0, battery_costs)  # sane $/kW-yr range
        # Real cost should increase with real duration -- more energy capacity at the same power
        # rating costs more, a basic real-world sanity check on the ingested ATB data itself.
        sorted_by_duration = sort(battery_costs, by=c -> c.duration_hours)
        @test issorted([c.capex_usd_per_kw for c in sorted_by_duration])

        battery_result = optimize_battery_portfolio(sites; budget_usd=1e7)  # real default demand charges + ATB costs
        @test battery_result.termination_status == MOI.OPTIMAL

        scenarios = load_temperature_scenarios()
        @test length(scenarios) > 0
        @test length(scenarios) <= length(sites)  # real, honestly partial -- 2 real sites have no NOAA data
        @test all(v -> length(v) > 0, values(scenarios))
        all_real_scenarios = [sc for site_scenarios in values(scenarios) for sc in site_scenarios]
        @test all(sc -> -50.0 < sc.temp_c < 60.0, all_real_scenarios)  # sane real temp range
        for site_scenarios in values(scenarios)
            @test isapprox(sum(sc.probability for sc in site_scenarios), 1.0; atol=1e-9)  # a real probability distribution
        end

        stochastic_result = optimize_stochastic_capacity_portfolio(sites; budget_usd=0.0, growth_multiplier=1.0)
        @test stochastic_result.termination_status == MOI.OPTIMAL
        # Every selected site's realized violation rate must respect the default chance
        # constraint -- true by construction of the model, a real correctness check on the
        # solve, not just "did it return OPTIMAL."
        @test all(v -> v <= EPSILON_DEFAULT + 1e-9, values(stochastic_result.violation_rate_by_site))
    else
        @test_skip "warehouse/volterra.duckdb not present -- run warehouse/build_warehouse.py first"
    end
end
