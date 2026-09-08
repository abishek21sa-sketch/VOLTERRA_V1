from copy import deepcopy
from validation.network_expansion_reference import Site,Corridor
from validation.investment_assurance import build_investment_certificate,verify_investment_certificate


def resilient_fixture():
    sites=[Site('A',120000,30000,150,8,(900,1500)),Site('B',130000,30000,150,8,(900,1500)),Site('C',100000,35000,150,8,(900,1500))]
    corridors=[Corridor('C1',(400,900),True),Corridor('C2',(300,700),True)]
    coverage={('C1','A'):True,('C1','B'):True,('C2','B'):True,('C2','C'):True}
    return sites,corridors,coverage


def test_investment_certificate_requires_committee_review_and_n_minus_one_evidence():
    s,c,cov=resilient_fixture()
    cert=build_investment_certificate(s,c,cov,(500000,500000),sessions_per_stall=700)
    assert cert['decision_state']=='INFRASTRUCTURE_INVESTMENT_REVIEW'
    assert cert['investment_execution_allowed'] is False
    assert cert['checks']['critical_outage_coverage_complete'] is True
    assert verify_investment_certificate(cert)['valid'] is True
    bad=deepcopy(cert); bad['resilience_premium_reference_usd']=0
    assert verify_investment_certificate(bad)['valid'] is False


def test_investment_certificate_holds_if_critical_corridor_has_no_redundancy():
    sites=[Site('A',100000,30000,150,8,(900,1500)),Site('B',100000,30000,150,8,(900,1500))]
    corridors=[Corridor('C1',(300,600),True)]
    coverage={('C1','A'):True}
    cert=build_investment_certificate(sites,corridors,coverage,(500000,500000),sessions_per_stall=700)
    assert cert['decision_state']=='HOLD'
