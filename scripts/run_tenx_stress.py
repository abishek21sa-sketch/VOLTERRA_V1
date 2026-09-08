from __future__ import annotations
import json, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from tenx.engine import run_decision
seeds=[3,7,11,19,29]; rows=[]; disagree=0
for s in seeds:
    d=run_decision(s); ok=bool(d.get('decision')) and d.get('autonomous_execution') is False and len(d.get('tool_trace',[]))>=5
    diff=d.get('counterfactual',{}).get('disagrees') is True; disagree+=int(diff); rows.append({'seed':s,'pass':ok,'changes_naive_decision':diff,'decision':d.get('decision')})
assert all(r['pass'] for r in rows)
assert disagree>=4, f'original algorithm changed naive decision in only {disagree}/5 stress cases'
out={'status':'PASS','seeds':seeds,'decision_path_passes':sum(r['pass'] for r in rows),'naive_decision_changes':disagree,'rows':rows}
p=ROOT/'artifacts'/'tenx_stress.json'; p.write_text(json.dumps(out,indent=2),encoding='utf-8'); print('TENX_STRESS=PASS'); print(json.dumps(out,indent=2))
