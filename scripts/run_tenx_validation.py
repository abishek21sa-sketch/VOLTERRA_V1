from __future__ import annotations
import json, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from tenx.engine import run_decision
REQUIRED=['project','ml_family','prediction_target','model_validation','original_algorithm','decision','counterfactual','uncertainty','or_escalation','tool_trace','limitations','abstention_conditions','user_aid','human_authority','autonomous_execution']
start=time.perf_counter(); d=run_decision(); missing=[x for x in REQUIRED if x not in d]
assert not missing,missing
assert d['autonomous_execution'] is False
assert len(d['tool_trace'])>=5
assert len(d['abstention_conditions'])>=2
assert len(d['user_aid'])>=3
assert d['counterfactual'].get('naive_policy')
assert d['counterfactual'].get('disagrees') is True, 'original algorithm must change the bundled naive decision'
assert d['original_algorithm']
assert d['ml_family']
ui=(ROOT/'tenx_ui'/'index.html').read_text(encoding='utf-8'); wc=json.loads((ROOT/'tenx_ui'/'workspace_contract.json').read_text(encoding='utf-8'))
assert len(wc.get('workspaces',[]))>=20 and 'dataset.workspace' in ui
out={'status':'PASS','runtime_ms':round((time.perf_counter()-start)*1000,3),'project':d['project'],'ml_family':d['ml_family'],'original_algorithm':d['original_algorithm'],'workspace_count':len(wc.get('workspaces',[])),'human_authority':d['human_authority'],'autonomous_execution':d['autonomous_execution'],'decision':d['decision'],'counterfactual':d['counterfactual']}
path=ROOT/'artifacts'/'tenx_validation.json'; path.parent.mkdir(exist_ok=True); path.write_text(json.dumps(out,indent=2),encoding='utf-8'); print('TENX_CERTIFICATION=PASS'); print(json.dumps(out,indent=2))
