from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from validation.network_expansion_reference import Site,Corridor
from validation.investment_assurance import build_investment_certificate,verify_investment_certificate

sites=[Site('URBAN_A',180000,42000,150,10,(900,1500,1500)),Site('CORRIDOR_B',240000,38000,150,12,(600,1200,1800)),Site('GRID_C',130000,50000,250,8,(500,1000,1500))]
corridors=[Corridor('I-CORE',(800,1200,1700),True),Corridor('SUBURB',(400,700,1000),False),Corridor('FREIGHT',(250,550,850),True)]
coverage={('I-CORE','URBAN_A'):True,('I-CORE','CORRIDOR_B'):True,('SUBURB','URBAN_A'):True,('SUBURB','GRID_C'):True,('FREIGHT','CORRIDOR_B'):True,('FREIGHT','GRID_C'):True}
cert=build_investment_certificate(sites,corridors,coverage,(450000,400000,400000),sessions_per_stall=800)
verification=verify_investment_certificate(cert)
payload={'status':'PASS' if verification['valid'] and cert['decision_state']=='INFRASTRUCTURE_INVESTMENT_REVIEW' else 'HOLD','certificate':cert,'verification':verification}
(ROOT/'validation'/'enterprise_operability.json').write_text(json.dumps(payload,indent=2,sort_keys=True,default=str))
print(json.dumps({'status':payload['status'],'decision_state':cert['decision_state'],'resilience_premium_reference_usd':cert['resilience_premium_reference_usd'],'certificate_valid':verification['valid']},indent=2))
if payload['status']!='PASS': raise SystemExit('VOLTERRA_ENTERPRISE_OPERABILITY=HOLD')
print('VOLTERRA_ENTERPRISE_OPERABILITY=PASS')
