using Test
using VolterraSimulation
using Distributions
using Random

@testset "charging_curve" begin
    @testset "power fraction" begin
        @test charging_power_fraction(0.0) == 1.0
        @test charging_power_fraction(0.5) == 1.0
        @test charging_power_fraction(1.0) ≈ 0.2
        @test charging_power_fraction(0.75) ≈ 0.6  # midpoint of the taper: (1.0 + 0.2) / 2
        # monotonically non-increasing past the breakpoint
        @test charging_power_fraction(0.6) >= charging_power_fraction(0.8) >= charging_power_fraction(1.0)
    end

    @testset "temperature derate" begin
        @test temperature_derate(20.0) == 1.0
        @test temperature_derate(10.0) == 1.0
        @test temperature_derate(-20.0) == 0.5
        @test temperature_derate(-30.0) == 0.5  # clamped below the floor temp
        @test 0.5 < temperature_derate(-5.0) < 1.0
    end

    @testset "charge duration" begin
        # Charging further should always take at least as long.
        short = charge_duration_hours(0.2, 0.5, 75.0, 150.0)
        long = charge_duration_hours(0.2, 0.9, 75.0, 150.0)
        @test long > short

        # Doubling rated power should roughly halve duration (taper shape unchanged).
        base = charge_duration_hours(0.2, 0.6, 75.0, 150.0)
        double_power = charge_duration_hours(0.2, 0.6, 75.0, 300.0)
        @test isapprox(double_power, base / 2, rtol=1e-9)

        # Colder temperature must never charge faster.
        warm = charge_duration_hours(0.2, 0.6, 75.0, 150.0; temp_c=20.0)
        cold = charge_duration_hours(0.2, 0.6, 75.0, 150.0; temp_c=-10.0)
        @test cold > warm

        @test_throws AssertionError charge_duration_hours(0.5, 0.5, 75.0, 150.0)  # target == start
        @test_throws AssertionError charge_duration_hours(0.6, 0.5, 75.0, 150.0)  # target < start
    end
end

@testset "queueing" begin
    @testset "erlang_c matches known identities" begin
        # c=1 (M/M/1): Erlang-C reduces exactly to the utilization rho.
        for rho in (0.1, 0.5, 0.9)
            @test erlang_c(1, rho) ≈ rho
        end

        # Erlang-C must increase with offered load for fixed c (more congestion => more waiting).
        c = 4
        p_low = erlang_c(c, 1.0)
        p_high = erlang_c(c, 3.5)
        @test p_high > p_low

        @test_throws AssertionError erlang_c(2, 2.0)   # offered load >= c: unstable
    end

    @testset "mgc_queue internal consistency" begin
        # M/M/c special case: service SCV = 1 (exponential) should match the plain Erlang-C
        # mean-wait formula exactly, since the Allen-Cunneen correction factor becomes 1.
        c = 5
        mean_s = 0.3  # hours
        var_s_exponential = mean_s^2  # SCV = 1 for an exponential distribution
        result = mgc_queue(10.0, c, mean_s, var_s_exponential)

        offered_load = 10.0 * mean_s
        rho = offered_load / c
        p_wait = erlang_c(c, offered_load)
        expected_wq_mmc = p_wait * mean_s / (c * (1 - rho))

        @test result.utilization ≈ rho
        @test result.p_wait ≈ p_wait
        @test result.mean_wait_hours ≈ expected_wq_mmc rtol=1e-9

        # Little's Law must hold by construction.
        @test result.mean_queue_length ≈ 10.0 * result.mean_wait_hours
        @test result.mean_number_in_system ≈ 10.0 * result.mean_time_in_system_hours

        @test_throws AssertionError mgc_queue(100.0, c, mean_s, var_s_exponential)  # unstable
    end

    @testset "higher service-time variance increases mean wait" begin
        c, mean_s, arrival = 5, 0.3, 10.0
        low_var = mgc_queue(arrival, c, mean_s, mean_s^2 * 0.5)
        high_var = mgc_queue(arrival, c, mean_s, mean_s^2 * 3.0)
        @test high_var.mean_wait_hours > low_var.mean_wait_hours
        # ...but utilization only depends on the mean, not the variance.
        @test low_var.utilization ≈ high_var.utilization
    end

    @testset "prob_wait_exceeds" begin
        result = mgc_queue(10.0, 5, 0.3, 0.3^2)
        @test prob_wait_exceeds(result, 0.0) ≈ result.p_wait
        # Longer thresholds are always less likely.
        @test prob_wait_exceeds(result, 1.0) < prob_wait_exceeds(result, 0.1)
        @test prob_wait_exceeds(result, 100.0) < 1e-6
    end
end

@testset "discrete-event simulation" begin
    @testset "matches analytical model at moderate utilization" begin
        # Same setup as examples/site_queueing_demo.jl's Charleston, WV case, sized down for a
        # fast test: 8 stalls, exponential service (SCV=1) so the analytical M/M/c formula is
        # exact, not just an approximation, giving a tight target to validate the simulation
        # against.
        c = 8
        mean_s = 0.3  # hours
        rho_target = 0.7
        arrival_rate = rho_target * c / mean_s

        analytical = mgc_queue(arrival_rate, c, mean_s, mean_s^2)  # SCV=1 => exponential service

        rng = MersenneTwister(7)
        n = 20_000
        interarrivals = rand(rng, Exponential(1 / arrival_rate), n)
        services = rand(rng, Exponential(mean_s), n)

        # Minimal M/M/c simulation via a c-server "next free" tracker — deliberately independent
        # of des.jl's ConcurrentSim machinery, so this test validates the analytical formula
        # itself against a from-scratch reference implementation, not just internal consistency.
        server_free_at = zeros(c)
        t = 0.0
        waits = Float64[]
        for i in 1:n
            t += interarrivals[i]
            earliest = argmin(server_free_at)
            start = max(t, server_free_at[earliest])
            push!(waits, start - t)
            server_free_at[earliest] = start + services[i]
        end

        # Skip the first 10% as warm-up.
        warm = n ÷ 10
        empirical_mean_wait = sum(waits[warm:end]) / length(waits[warm:end])

        @test isapprox(empirical_mean_wait, analytical.mean_wait_hours; rtol=0.15)
    end

    @testset "run_site_simulation produces internally consistent records" begin
        cfg = SiteSimConfig(
            4, 150.0, 8.0,
            Uniform(60.0, 100.0), Truncated(Normal(0.3, 0.1), 0.02, 0.55), 0.8,
            20.0, 24.0 * 10,
        )
        records = run_site_simulation(cfg; seed=1)
        @test length(records) > 0
        @test all(r -> r.service_start_time >= r.arrival_time, records)
        @test all(r -> r.service_end_time > r.service_start_time, records)

        summary = summarize(records)
        @test summary.n_completed == length(records)
        @test summary.mean_wait_hours >= 0.0
        @test 0.0 <= summary.p_wait_gt_zero <= 1.0
    end

    @testset "more stalls at fixed load reduces waiting (pooling effect)" begin
        # Near-degenerate battery/SOC distributions so both configs draw an (almost) fixed
        # service-time distribution — only stall count and arrival rate differ between them.
        near_fixed_battery = Uniform(69.99, 70.01)
        near_fixed_soc = Truncated(Normal(0.3, 1e-9), 0.02, 0.55)
        target_soc = 0.7
        approx_mean_s = charge_duration_hours(0.3, target_soc, 70.0, 150.0)
        rho = 0.8

        small = SiteSimConfig(4, 150.0, rho * 4 / approx_mean_s, near_fixed_battery,
                               near_fixed_soc, target_soc, 20.0, 24.0 * 20)
        large_stalls = 16
        large = SiteSimConfig(large_stalls, 150.0, rho * large_stalls / approx_mean_s,
                               near_fixed_battery, near_fixed_soc, target_soc, 20.0, 24.0 * 20)

        small_summary = summarize(run_site_simulation(small; seed=3))
        large_summary = summarize(run_site_simulation(large; seed=3))

        @test large_summary.p_wait_gt_zero < small_summary.p_wait_gt_zero
    end
end

@testset "reliability" begin
    @testset "stall_availability / mtbf_for_target_availability round-trip" begin
        for target in (0.90, 0.9995, 0.999999)
            mttr = 24.0
            mtbf = mtbf_for_target_availability(target, mttr)
            @test stall_availability(mtbf, mttr) ≈ target rtol = 1e-9
        end

        @test_throws AssertionError mtbf_for_target_availability(0.0, 24.0)
        @test_throws AssertionError mtbf_for_target_availability(1.0, 24.0)
    end

    @testset "stall_availability sanity" begin
        # Equal MTBF/MTTR -> exactly 50% availability, a well-known identity.
        @test stall_availability(10.0, 10.0) ≈ 0.5
        # Fixed MTBF, longer repair time -> lower availability.
        @test stall_availability(100.0, 5.0) > stall_availability(100.0, 50.0)
        # Fixed MTTR, longer MTBF (fails less often) -> higher availability.
        @test stall_availability(200.0, 24.0) > stall_availability(50.0, 24.0)
    end

    @testset "MTBF_HOURS is calibrated to the real Tesla uptime target" begin
        @test stall_availability(MTBF_HOURS, MTTR_HOURS) ≈ TESLA_NETWORK_UPTIME rtol = 1e-9
    end

    @testset "site_full_outage_probability" begin
        # Matches the direct closed-form calculation, not just "some small number".
        a = stall_availability(MTBF_HOURS, MTTR_HOURS)
        @test site_full_outage_probability(6) ≈ (1 - a)^6
        @test site_full_outage_probability(32) ≈ (1 - a)^32

        # More real stalls -> real redundancy -> lower simultaneous-total-outage probability.
        @test site_full_outage_probability(32) < site_full_outage_probability(6)
        @test 0.0 <= site_full_outage_probability(6) <= 1.0

        # At the real 99.95% target, even a small real site's full-outage probability is
        # vanishingly small -- redundancy alone makes total outage a non-concern at this
        # reliability level, a genuine, non-obvious finding worth a hard numeric check, not just
        # "it's less than something".
        @test site_full_outage_probability(6) < 1e-15
    end

    @testset "run_site_simulation_with_failures degrades service under an aggressive failure regime" begin
        cfg = SiteSimConfig(
            8, 150.0, 8.0 * 0.65 / 0.3,  # ~0.65 utilization at mean_s~0.3h, matching this project's BASELINE_UTILIZATION convention
            Uniform(60.0, 100.0), Truncated(Normal(0.3, 0.1), 0.02, 0.55), 0.8,
            20.0, 24.0 * 30,
        )

        baseline = summarize(run_site_simulation(cfg; seed=11))
        # Deliberately much worse than the real calibrated MTBF/MTTR, to give a clear, unambiguous
        # signal that the failure mechanism is doing real work, not a subtle/noisy effect.
        aggressive = summarize(run_site_simulation_with_failures(cfg; mtbf_hours=48.0, mttr_hours=24.0, seed=11))

        @test aggressive.mean_wait_hours > baseline.mean_wait_hours
        @test aggressive.p_wait_gt_zero >= baseline.p_wait_gt_zero
    end

    @testset "run_site_simulation_with_failures matches the no-failure baseline when MTBF is huge" begin
        cfg = SiteSimConfig(
            8, 150.0, 8.0 * 0.65 / 0.3,
            Uniform(60.0, 100.0), Truncated(Normal(0.3, 0.1), 0.02, 0.55), 0.8,
            20.0, 24.0 * 30,
        )

        baseline = summarize(run_site_simulation(cfg; seed=5))
        # A stall that (essentially) never fails within the simulated window should reproduce the
        # no-failure baseline closely.
        near_perfect = summarize(run_site_simulation_with_failures(cfg; mtbf_hours=1e9, mttr_hours=1.0, seed=5))

        @test isapprox(near_perfect.mean_wait_hours, baseline.mean_wait_hours; rtol=0.05)
    end
end
