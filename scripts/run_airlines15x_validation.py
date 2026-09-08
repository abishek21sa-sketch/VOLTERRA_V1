from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
if (ROOT/'src').exists():sys.path.insert(0,str(ROOT/'src'))
from empirical.backbone import run_empirical_reference
from tenx.engine import run_decision
emp=run_empirical_reference();dec=run_decision();html=(ROOT/'tenx_ui/index.html').read_text(encoding='utf-8');wc=json.loads((ROOT/'tenx_ui/workspace_contract.json').read_text())
n=len(wc.get('workspaces',[]))
checks={
  'source_manifest':bool(emp.get('source',{}).get('source_url')),
  'analyzable_evidence':emp['local_profile'].get('rows',0)>=1,
  'domain_diagnostics':bool(emp.get('domain_diagnostics',{}).get('analysis')),
  'case_study_dossier':all(k in emp.get('case_study',{}) for k in ['facts','inferences','recommendation','limitations']),
  'historical_or_shift_analysis':bool(emp.get('cohort_shift')) or emp['local_profile'].get('rows',0)<6,
  'analysis_stack_10':len(emp.get('analysis_stack',[]))>=10,
  'ai_uses_empirical':bool(dec.get('empirical_backbone')),
  'provenance_in_ai':any('provenance' in str(x).lower() for x in dec.get('tool_trace',[])),
  'workspaces_26_plus':n>=26 and 'dataset.workspace' in html,
  'workspace_contract_complete':all(all(k in x for k in ['engineering_question','method','evidence','operator_action']) for x in wc.get('workspaces',[])),
  'workspace_method_uniqueness':len({x['method'] for x in wc.get('workspaces',[])})/max(1,n)>=0.90,
  'workspace_action_specificity':len({x['operator_action'] for x in wc.get('workspaces',[])})/max(1,n)>=0.90,
  'live_empirical_endpoint_source':'/api/airlines15x/empirical' in (ROOT/'scripts/start_tenx_workstation.py').read_text(),
  'empirical_ui_live':'/api/airlines15x/empirical' in html,
  'claim_boundary':('offline_reference'!=emp['data_mode']) or ('REFERENCE_ONLY'==emp['empirical_promotion']),
}
status='PASS' if all(checks.values()) else 'FAIL';out={'status':status,'checks':checks,'workspaces':n,'data_mode':emp['data_mode'],'promotion':emp['empirical_promotion'],'source':emp['source'].get('source_name'),'rows':emp['local_profile'].get('rows'),'case':emp.get('case_study',{}).get('name')}
(ROOT/'artifacts').mkdir(exist_ok=True);(ROOT/'artifacts/airlines15x_validation.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2));print('EMPIRICAL_DEPTH_VALIDATION='+status);raise SystemExit(0 if status=='PASS' else 1)
