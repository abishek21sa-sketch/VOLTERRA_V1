"""Governed infrastructure-investment assurance for VOLTERRA planning outputs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from .network_expansion_reference import Site, Corridor, solve_reference


def _canonical(x: Any) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _serializable(result: dict[str, Any]) -> dict[str, Any]:
    r=dict(result)
    r['assignments']={f'{k[0]}@P{k[1]}':v for k,v in r.get('assignments',{}).items()}
    r['contingency_assignments']={f'{k[0]}@OUTAGE:{k[1]}':v for k,v in r.get('contingency_assignments',{}).items()}
    return r


def build_investment_certificate(
    sites: list[Site], corridors: list[Corridor], coverage: dict[tuple[str,str], bool], budgets: tuple[float,...],
    *, sessions_per_stall: float = 900.0, unserved_penalty: float = 80.0, discount_rate: float = 0.08,
) -> dict[str, Any]:
    nominal=solve_reference(sites,corridors,coverage,budgets,sessions_per_stall=sessions_per_stall,unserved_penalty=unserved_penalty,discount_rate=discount_rate)
    resilient=solve_reference(sites,corridors,coverage,budgets,sessions_per_stall=sessions_per_stall,unserved_penalty=unserved_penalty,discount_rate=discount_rate,n_minus_one_critical=True)
    expected_contingencies=sum(1 for c in corridors if c.critical)*len(sites)
    observed_contingencies=len(resilient.get('contingency_assignments',{}))
    premium=None; premium_ratio=None
    if nominal.get('status')=='OPTIMAL' and resilient.get('status')=='OPTIMAL':
        premium=float(resilient['objective']-nominal['objective'])
        premium_ratio=float(premium/max(abs(float(nominal['objective'])),1.0))
    checks={
        'nominal_plan_optimal':nominal.get('status')=='OPTIMAL',
        'n_minus_one_plan_optimal':resilient.get('status')=='OPTIMAL',
        'critical_outage_coverage_complete':observed_contingencies==expected_contingencies,
        'investment_execution_blocked':True,
    }
    state='INFRASTRUCTURE_INVESTMENT_REVIEW' if all(checks.values()) else 'HOLD'
    core={
        'certificate_type':'VOLTERRA_INFRASTRUCTURE_ASSURANCE_V1',
        'decision_state':state,
        'planning_horizon_periods':len(budgets),
        'candidate_site_count':len(sites),
        'corridor_count':len(corridors),
        'critical_corridor_count':sum(1 for c in corridors if c.critical),
        'nominal_plan':_serializable(nominal),
        'n_minus_one_plan':_serializable(resilient),
        'resilience_premium_reference_usd':premium,
        'resilience_premium_ratio':premium_ratio,
        'resilience_economic_flag':'HIGH_REDUNDANCY_PREMIUM' if premium_ratio is not None and premium_ratio>0.25 else 'WITHIN_REFERENCE_REVIEW_BAND',
        'checks':checks,
        'approval_authority':'INFRASTRUCTURE_PLANNING_COMMITTEE',
        'required_external_reviews':['UTILITY_INTERCONNECTION','SITE_PERMITTING','REAL_DEMAND_CALIBRATION','CAPEX_VALIDATION'],
        'investment_execution_allowed':False,
        'native_julia_gate':'PENDING_WINDOWS_JULIA_ACCEPTANCE',
        'claim_boundary':'Synthetic/reference planning evidence only; not utility approval, permitting approval, or authorization to spend capital.',
    }
    return {**core,'certificate_sha256':hashlib.sha256(_canonical(core)).hexdigest(),'generated_at_utc':datetime.now(timezone.utc).isoformat()}


def verify_investment_certificate(certificate: dict[str, Any]) -> dict[str, Any]:
    stored=certificate.get('certificate_sha256')
    core={k:v for k,v in certificate.items() if k not in {'certificate_sha256','generated_at_utc'}}
    expected=hashlib.sha256(_canonical(core)).hexdigest()
    return {'valid':bool(stored and stored==expected),'stored_sha256':stored,'expected_sha256':expected,'decision_state':certificate.get('decision_state')}
