from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation.network_expansion_reference import Site, Corridor, solve_reference

sites=[Site('URBAN_A',180000,42000,150,10,(900,1500,1500)),Site('CORRIDOR_B',240000,38000,150,12,(600,1200,1800)),Site('GRID_C',130000,50000,250,8,(500,1000,1500))]
corridors=[Corridor('I-CORE',(800,1200,1700),True),Corridor('SUBURB',(400,700,1000),False),Corridor('FREIGHT',(250,550,850),True)]
coverage={('I-CORE','URBAN_A'):True,('I-CORE','CORRIDOR_B'):True,('SUBURB','URBAN_A'):True,('SUBURB','GRID_C'):True,('FREIGHT','CORRIDOR_B'):True,('FREIGHT','GRID_C'):True}
budgets=(450000,400000,400000)
nominal=solve_reference(sites,corridors,coverage,budgets,sessions_per_stall=800)
resilient=solve_reference(sites,corridors,coverage,budgets,sessions_per_stall=800,n_minus_one_critical=True)

def serializable(result):
    r=dict(result)
    if 'assignments' in r:
        r['assignments']={f'{k[0]}@P{k[1]}':v for k,v in r['assignments'].items()}
    if 'contingency_assignments' in r:
        r['contingency_assignments']={f'{k[0]}@OUTAGE:{k[1]}':v for k,v in r['contingency_assignments'].items()}
    return r

critical_outage_pairs=len([c for c in corridors if c.critical])*len(sites)
contingency_count=len(resilient.get('contingency_assignments',{}))
checks={
    'nominal_optimal': nominal.get('status')=='OPTIMAL',
    'n_minus_one_optimal': resilient.get('status')=='OPTIMAL',
    'all_critical_site_outage_assignments_present': contingency_count==critical_outage_pairs,
}
status='PASS' if all(checks.values()) else 'HOLD'
premium=None
if nominal.get('status')=='OPTIMAL' and resilient.get('status')=='OPTIMAL':
    premium=float(resilient['objective']-nominal['objective'])
payload={
    'release':'VOLTERRA_PORTFOLIO_RC1',
    'status':status,
    'nominal_network_expansion':serializable(nominal),
    'n_minus_one_critical_network_expansion':serializable(resilient),
    'resilience_economics':{
        'objective_premium_usd_reference':premium,
        'interpretation':'Incremental reference objective required to preserve final-horizon critical-corridor service capacity after any single candidate-site outage.'
    },
    'checks':checks,
    'native_julia_gate':'PENDING_WINDOWS_JULIA_ACCEPTANCE',
    'production_write_allowed':False,
    'human_investment_approval_required':True,
    'evidence_boundary':'Reference planning inputs are synthetic. N-1 is a model-based planning contingency, not utility/grid certification; real-site and native Julia/JuMP gates remain separately governed.'
}
core=json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()
payload['evidence_sha256']=hashlib.sha256(core).hexdigest()
(ROOT/'validation'/'portfolio_validation.json').write_text(json.dumps(payload,indent=2,default=str,sort_keys=True))
print(json.dumps({'release':payload['release'],'status':status,'checks':checks,'resilience_economics':payload['resilience_economics'],'native_julia_gate':payload['native_julia_gate']},indent=2))
if status!='PASS': raise SystemExit('VOLTERRA_PORTFOLIO_VALIDATION=HOLD')
print('VOLTERRA_PORTFOLIO_VALIDATION=PASS')
