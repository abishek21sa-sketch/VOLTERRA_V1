using Test
using VolterraGraph
using Graphs

@testset "haversine_miles" begin
    @test haversine_miles(40.0, -74.0, 40.0, -74.0) ≈ 0.0 atol=1e-9
    # NYC to LA is a well-known real distance (~2,450 mi great-circle).
    nyc_la = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
    @test 2400.0 < nyc_la < 2500.0
    # Symmetric.
    @test haversine_miles(10.0, 20.0, 30.0, 40.0) ≈ haversine_miles(30.0, 40.0, 10.0, 20.0)
end

# A synthetic 5-node chain with a known ground truth: A - B - C - D, plus an isolated E.
# Distances are placed so a threshold of 15 connects consecutive nodes only (no skip edges).
chain_sites = [
    GeoSite("A", "A", missing, missing, 8, 150.0, 0.0, 0.0),
    GeoSite("B", "B", missing, missing, 8, 150.0, 0.0, 10.0),
    GeoSite("C", "C", missing, missing, 8, 150.0, 0.0, 20.0),
    GeoSite("D", "D", missing, missing, 8, 150.0, 0.0, 30.0),
    GeoSite("E", "E", missing, missing, 8, 150.0, 10.0, 90.0),  # far from everything
]
# Longitude degrees at the equator (latitude 0) convert to real miles via haversine -- roughly
# 69 mi/degree, so 10 degrees ~ 690 mi. Use a threshold that isolates consecutive links only.

@testset "build_resilience_graph" begin
    rg = build_resilience_graph(chain_sites, 800.0)  # connects A-B, B-C, C-D (each ~690mi) but not A-C etc (~1380mi)
    @test ne(rg.g) == 3  # A-B, B-C, C-D only
    @test length(rg.edges) == 3
    @test rg.range_threshold_mi == 800.0

    # Every site is isolated at a threshold below any real distance.
    rg_isolated = build_resilience_graph(chain_sites, 1.0)
    @test ne(rg_isolated.g) == 0
end

@testset "criticality_index on a known chain: A-B-C-D, E isolated" begin
    rg = build_resilience_graph(chain_sites, 800.0)
    crit = criticality_index(rg)

    # B is the sole connector between A and {C, D} -- removing it disconnects exactly 2 pairs:
    # (A,C) and (A,D). It does NOT affect (C,D), which stays connected without B.
    @test crit["B"] == 2
    # By symmetry, C disconnects (B,D) and (A,D) -- also 2 pairs.
    @test crit["C"] == 2
    # A and D are endpoints -- removing an endpoint disconnects nothing for anyone else.
    @test crit["A"] == 0
    @test crit["D"] == 0
    # E has no edges at all -- trivially zero impact.
    @test crit["E"] == 0
end

@testset "criticality_index on a fully-connected triangle: no single point of failure" begin
    triangle = [
        GeoSite("X", "X", missing, missing, 8, 150.0, 0.0, 0.0),
        GeoSite("Y", "Y", missing, missing, 8, 150.0, 0.0, 1.0),
        GeoSite("Z", "Z", missing, missing, 8, 150.0, 1.0, 0.0),
    ]
    rg = build_resilience_graph(triangle, 200.0)  # all pairwise distances are small; fully connects
    @test ne(rg.g) == 3
    crit = criticality_index(rg)
    @test all(v -> v == 0, values(crit))  # removing any one vertex still leaves the other two adjacent
end

@testset "compute_resilience_metrics reports separate fields, none blended" begin
    rg = build_resilience_graph(chain_sites, 800.0)
    metrics = compute_resilience_metrics(rg)
    @test length(metrics) == length(chain_sites)

    b_metric = only(filter(m -> m.site.site_id == "B", metrics))
    @test b_metric.degree == 2         # A and C
    @test b_metric.criticality == 2
    @test b_metric.betweenness > 0.0   # B sits on shortest paths between A and {C,D}

    e_metric = only(filter(m -> m.site.site_id == "E", metrics))
    @test e_metric.degree == 0
    @test e_metric.criticality == 0
    @test e_metric.betweenness == 0.0
end

# Guarded integration test against the real warehouse.
@testset "load_sites + real network (integration)" begin
    if isfile(DEFAULT_WAREHOUSE_PATH)
        sites = load_sites()
        @test length(sites) > 0
        @test all(s -> -90 <= s.latitude <= 90 && -180 <= s.longitude <= 180, sites)

        rg = build_resilience_graph(sites, 150.0)
        metrics = compute_resilience_metrics(rg)
        @test length(metrics) == length(sites)
        @test all(m -> m.criticality >= 0, metrics)
        # Criticality can never exceed the number of pairs among all other sites.
        n = length(sites)
        @test all(m -> m.criticality <= (n - 1) * (n - 2) ÷ 2, metrics)
    else
        @test_skip "warehouse/volterra.duckdb not present -- run warehouse/build_warehouse.py first"
    end
end
