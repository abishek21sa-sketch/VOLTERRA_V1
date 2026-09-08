from validation.network_expansion_reference import Site, Corridor, solve_reference


def fixture():
    sites=[
        Site('A', 120_000, 35_000, 150, 8, (600,1200)),
        Site('B', 160_000, 30_000, 150, 8, (450,1200)),
    ]
    corridors=[
        Corridor('C1',(900,1500),True),
        Corridor('C2',(300,700),False),
    ]
    coverage={('C1','A'):True,('C1','B'):False,('C2','A'):True,('C2','B'):True}
    return sites,corridors,coverage


def test_critical_corridor_forces_eligible_site_open_by_horizon():
    s,c,cov=fixture(); r=solve_reference(s,c,cov,(300_000,300_000))
    assert r['status']=='OPTIMAL'
    assert 'A' in r['opened_period']
    assert r['assignments'][('C1',2)]=='A'


def test_budget_and_grid_limits_bound_buildout():
    s,c,cov=fixture(); r=solve_reference(s,c,cov,(180_000,180_000))
    assert r['status']=='OPTIMAL'
    assert r['stalls_by_period']['A'][0] <= 4  # 600kW / 150kW


def test_infeasible_if_critical_corridor_has_no_eligible_site():
    s,c,cov=fixture(); cov={k:v for k,v in cov.items() if k[0]!='C1'}
    r=solve_reference(s,c,cov,(1_000_000,1_000_000))
    assert r['status']=='INFEASIBLE'


def test_n_minus_one_gate_requires_redundant_critical_coverage_and_capacity():
    sites=[
        Site('A',120_000,30_000,150,8,(900,1500)),
        Site('B',130_000,30_000,150,8,(900,1500)),
        Site('C',100_000,35_000,150,8,(900,1500)),
    ]
    corridors=[Corridor('C1',(400,900),True),Corridor('C2',(300,700),True)]
    coverage={('C1','A'):True,('C1','B'):True,('C2','B'):True,('C2','C'):True}
    r=solve_reference(sites,corridors,coverage,(500_000,500_000),sessions_per_stall=700,n_minus_one_critical=True)
    assert r['status']=='OPTIMAL'
    assert r['n_minus_one_critical'] is True
    # Every single-site outage has an explicit surviving assignment for both critical corridors.
    for outage in ('A','B','C'):
        assert ('C1',outage) in r['contingency_assignments']
        assert ('C2',outage) in r['contingency_assignments']
        assert r['contingency_assignments'][('C1',outage)] != outage
        assert r['contingency_assignments'][('C2',outage)] != outage
