using Test
using VolterraGraph
using VolterraRouting

# Synthetic vehicles/sites -- deterministic, independent of the warehouse file. A guarded
# integration testset at the bottom exercises the real network if the warehouse is present.
const SMALL_EV = RealEVSpec("Test", "Short-Range", 2024, 150.0, 50.0, 0.30)   # 50 kWh, 0.30 kWh/mi -> 166.7mi true range
const LONG_EV = RealEVSpec("Test", "Long-Range", 2024, 350.0, 100.0, 0.28)     # 100 kWh, 0.28 kWh/mi -> 357mi true range

# A-B-C chain: A-B and B-C are short real hops (~88mi each), A-C direct is longer (~177mi) but
# still under the 200mi threshold used below -- so a THIRD, single-hop route also exists here.
# That's fine for the single-route soc_feasible_shortest_path tests (Dijkstra should legitimately
# prefer the fastest real option), but the all_simple_paths/traffic-assignment tests need a
# topology with an unambiguous, verified real edge set, so they use `diamond_sites` instead (below).
chain_sites = [
    GeoSite("A", "A", missing, missing, 8, 150.0, 36.0, -115.0),
    GeoSite("B", "B", missing, missing, 8, 150.0, 37.0, -114.0),
    GeoSite("C", "C", missing, missing, 8, 150.0, 38.0, -113.0),
    GeoSite("E", "E", missing, missing, 8, 150.0, 50.0, 50.0),  # far away, unreachable at any real threshold used below
]
site_of = Dict(s.site_id => s for s in chain_sites)

# A real diamond: A-B, B-C, A-D, D-C each ~163.5mi (under 200mi threshold); A-C ~220mi and B-D
# ~241.5mi (both over 200mi threshold, verified by haversine_miles below, not assumed) -- so
# exactly two real routes exist, A-B-C and A-D-C, with no direct or cross-connecting edge.
diamond_sites = [
    GeoSite("A", "A", missing, missing, 8, 150.0, 36.0, -115.0),
    GeoSite("B", "B", missing, missing, 8, 150.0, 37.75, -113.0),
    GeoSite("C", "C", missing, missing, 8, 150.0, 36.0, -111.0),
    GeoSite("D", "D", missing, missing, 4, 150.0, 34.25, -113.0),
    GeoSite("E", "E", missing, missing, 8, 150.0, 50.0, 50.0),  # far away, unreachable at any real threshold used below
]
diamond_site_of = Dict(s.site_id => s for s in diamond_sites)
@test haversine_miles(36.0, -115.0, 37.75, -113.0) < 200.0   # A-B
@test haversine_miles(37.75, -113.0, 36.0, -111.0) < 200.0   # B-C
@test haversine_miles(36.0, -115.0, 34.25, -113.0) < 200.0   # A-D
@test haversine_miles(34.25, -113.0, 36.0, -111.0) < 200.0   # D-C
@test haversine_miles(36.0, -115.0, 36.0, -111.0) > 200.0    # A-C direct -- excluded
@test haversine_miles(37.75, -113.0, 34.25, -113.0) > 200.0  # B-D -- excluded

@testset "driving_distance_mi" begin
    d = driving_distance_mi(site_of["A"], site_of["B"])
    real_gc = haversine_miles(36.0, -115.0, 37.0, -114.0)
    @test d ≈ real_gc * CIRCUITY_FACTOR
    @test d > real_gc  # circuity factor always inflates, never shrinks, real distance
end

@testset "soc_bucket / bucket_soc" begin
    @test soc_bucket(0.0) == 0
    @test soc_bucket(0.049) == 0
    @test soc_bucket(0.05) == 1
    @test bucket_soc(soc_bucket(0.37)) <= 0.37  # flooring is always conservative
end

@testset "soc_feasible_shortest_path" begin
    rg = build_resilience_graph(chain_sites, 200.0)

    @testset "a short direct hop needs no charging" begin
        result = soc_feasible_shortest_path(rg, LONG_EV, "A", "B")
        @test result.feasible
        @test result.path == ["A", "B"]
        @test isempty(result.charge_stops)
        @test result.total_time_hours ≈ result.total_distance_mi / AVG_HIGHWAY_SPEED_MPH atol=1e-9
    end

    @testset "a long trip forces a real intermediate charging stop" begin
        # A-C's real driving distance (great-circle x circuity) must exceed SMALL_EV's real range,
        # so the direct A-C edge (which exists in this graph at 200mi threshold, since the real
        # great-circle distance is under 200mi) still isn't usable by this vehicle -- verified
        # here rather than assumed, so the test doesn't silently pass for the wrong reason.
        ac_driving_mi = driving_distance_mi(site_of["A"], site_of["C"])
        @test ac_driving_mi * SMALL_EV.consumption_kwh_per_mile > SMALL_EV.battery_kwh  # energy needed exceeds real battery capacity

        result = soc_feasible_shortest_path(rg, SMALL_EV, "A", "C")
        @test result.feasible
        @test result.path == ["A", "B", "C"]  # direct A-C exists topologically but exceeds this vehicle's real range
        @test length(result.charge_stops) == 1
        stop = only(result.charge_stops)
        @test stop.site_id == "B"
        @test stop.soc_depart > stop.soc_arrive
        @test stop.charge_hours > 0.0
        @test result.total_time_hours > result.total_distance_mi / AVG_HIGHWAY_SPEED_MPH  # charging adds real time
    end

    @testset "a stop needing many SOC buckets of charge merges into one real ChargeStop" begin
        # A vehicle that arrives at B very low and needs to climb several SOC buckets before it can
        # safely continue to C -- regression test for a real bug found in Phase 9 development: the
        # search used to offer "any target bucket in one jump" as an alternative to chaining single-
        # bucket steps, and a numerical-quadrature difference between the two (charge_duration_hours
        # integrates with a fixed step count regardless of interval width) made Dijkstra sometimes
        # prefer chaining several small real charge actions over one big one, which then showed up
        # as several separate ChargeStops at the same real site instead of one real charging session.
        low_energy_ev = RealEVSpec("Test", "Barely Makes It", 2024, 200.0, 60.0, 0.32)
        result = soc_feasible_shortest_path(rg, low_energy_ev, "A", "C")
        @test result.feasible
        @test result.path == ["A", "B", "C"]
        @test length(result.charge_stops) == 1  # one real charging session at B, not several
        stop = only(result.charge_stops)
        @test stop.soc_depart - stop.soc_arrive > 2 * SOC_BUCKET_STEP  # a real multi-bucket climb, not a trivial top-up
    end

    @testset "no real route exists" begin
        result = soc_feasible_shortest_path(rg, LONG_EV, "A", "E")
        @test !result.feasible
        @test isempty(result.path)
    end

    @testset "range too short for any real hop, even with charging" begin
        tiny_ev = RealEVSpec("Test", "Tiny", 2024, 20.0, 5.0, 0.30)  # 5 kWh battery, way under any hop's real energy need
        result = soc_feasible_shortest_path(rg, tiny_ev, "A", "C")
        @test !result.feasible
    end
end

@testset "all_simple_paths" begin
    rg = build_resilience_graph(diamond_sites, 200.0)
    paths = all_simple_paths(rg, "A", "C"; max_hops=5)
    @test Set(paths) == Set([["A", "B", "C"], ["A", "D", "C"]])  # exactly the two real routes

    @test all_simple_paths(rg, "A", "E"; max_hops=5) == []  # E is unreachable
end

@testset "site_wait_hours / marginal_wait_hours" begin
    site = site_of["A"]

    @testset "baseline (zero added flow) is finite and positive" begin
        w0 = site_wait_hours(site, 0.0)
        @test w0 > 0.0
        @test isfinite(w0)
    end

    @testset "adding flow never decreases real wait time" begin
        w_low = site_wait_hours(site, 0.5)
        w_high = site_wait_hours(site, 2.0)
        @test w_high >= w_low
    end

    @testset "enough added flow makes the real queue unstable" begin
        @test site_wait_hours(site, 1000.0) == Inf
    end

    @testset "marginal congestion cost is non-negative and grows with utilization" begin
        m_low = marginal_wait_hours(site, 0.5)
        m_high = marginal_wait_hours(site, 2.0)
        @test m_low >= 0.0
        @test m_high >= m_low  # convex delay -- marginal cost rises as utilization climbs
    end
end

@testset "path_cost_hours" begin
    rg = build_resilience_graph(chain_sites, 200.0)
    direct = build_path_option(site_of, ["A", "B"])
    via_stop = build_path_option(site_of, ["A", "B", "C"])

    @test isempty(direct.intermediate_sites)
    @test via_stop.intermediate_sites == ["B"]

    empty_flow = Dict{String,Float64}()
    @test path_cost_hours(via_stop, site_of, empty_flow) > path_cost_hours(direct, site_of, empty_flow)
end

@testset "assign_traffic" begin
    path_abc = build_path_option(diamond_site_of, ["A", "B", "C"])
    path_adc = build_path_option(diamond_site_of, ["A", "D", "C"])
    paths = [path_abc, path_adc]

    @testset "flow conserves total demand at a moderate demand level" begin
        demand = 3.0
        ue = assign_traffic(paths, diamond_site_of, demand)
        @test ue.stable
        @test sum(ue.flow_per_hour) ≈ demand atol=1e-6
        @test all(f -> f >= -1e-9, ue.flow_per_hour)
    end

    @testset "system optimal never costs more than user equilibrium (price of anarchy >= 1)" begin
        demand = 3.0
        ue = assign_traffic(paths, diamond_site_of, demand; marginal_cost=false)
        so = assign_traffic(paths, diamond_site_of, demand; marginal_cost=true)
        @test so.stable && ue.stable
        @test so.total_cost_hours <= ue.total_cost_hours + 1e-6
    end

    @testset "extreme demand makes every real path unstable" begin
        result = assign_traffic(paths, diamond_site_of, 10_000.0; n_iterations=20)
        @test !result.stable
    end
end

# Guarded integration test against the real warehouse.
@testset "real network (integration)" begin
    if isfile(VolterraGraph.DEFAULT_WAREHOUSE_PATH)
        sites = load_sites()
        real_site_of = Dict(s.site_id => s for s in sites)
        vehicles = load_epa_vehicles()
        @test length(vehicles) > 0
        @test all(v -> v.range_miles > 0 && v.battery_kwh > 0 && v.consumption_kwh_per_mile > 0, vehicles)

        lv = only(filter(s -> occursin("Las Vegas", s.name), sites))
        np = only(filter(s -> occursin("Nephi", s.name), sites))
        rg = build_resilience_graph(sites, 250.0)

        paths = all_simple_paths(rg, lv.site_id, np.site_id; max_hops=5)
        @test length(paths) >= 1

        vehicle = first(vehicles)
        result = soc_feasible_shortest_path(rg, vehicle, lv.site_id, np.site_id)
        # Real trip distance (~300mi great-circle) exceeds most real single-charge EV ranges, so a
        # feasible real path should generally need at least one real intermediate stop -- but this
        # isn't asserted as a hard requirement since it legitimately depends on which real vehicle
        # got sampled (a long-range real EV might make it direct if a direct edge exists).
        if result.feasible
            @test result.total_time_hours > 0.0
        end

        if length(paths) >= 2
            path_options = [build_path_option(real_site_of, p) for p in paths]
            ue = assign_traffic(path_options, real_site_of, 2.0)
            so = assign_traffic(path_options, real_site_of, 2.0; marginal_cost=true)
            if ue.stable && so.stable
                @test so.total_cost_hours <= ue.total_cost_hours + 1e-6
            end
        end
    else
        @test_skip "warehouse/volterra.duckdb not present -- run warehouse/build_warehouse.py first"
    end
end
