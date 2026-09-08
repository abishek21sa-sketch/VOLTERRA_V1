from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));src=ROOT/'src'
if src.exists():sys.path.insert(0,str(src))
from campaign.engine import run_campaign
from tenx.engine import run_decision
from empirical.backbone import run_empirical_reference
from intelligence.engine import lifecycle_report,run_agent
cam=run_campaign();dec=run_decision();emp=run_empirical_reference();life=lifecycle_report();agent=run_agent()
wc=json.loads((ROOT/'tenx_ui/workspace_contract.json').read_text());html=(ROOT/'tenx_ui/index.html').read_text(encoding='utf-8')
n=len(wc.get('workspaces',[]))
methods=[x.get('method','') for x in wc.get('workspaces',[])];actions=[x.get('operator_action','') for x in wc.get('workspaces',[])]
checks={
  'workspace_depth':n>=26 and 'dataset.workspace' in html,
  'workspace_method_uniqueness':len(set(methods))/max(1,n)>=.90,
  'workspace_action_uniqueness':len(set(actions))/max(1,n)>=.90,
  'campaign_scenarios':cam.get('scenario_count',0)>=7,
  'campaign_ai_synthesis':all(k in cam.get('ai_synthesis',{}) for k in ['why','challenge','recommended_operator_action','abstention_conditions']),
  'campaign_actions':len(cam.get('action_queue',[]))>=4,
  'human_authority':bool(cam.get('human_authority')) and cam.get('autonomous_execution') is False,
  'campaign_endpoint':'/api/project/campaign' in (ROOT/'scripts/start_tenx_workstation.py').read_text(),
  'empirical_preserved':bool(emp.get('domain_diagnostics',{}).get('analysis')),
  'predictive_decision_preserved':bool(dec.get('original_algorithm')) and bool(dec.get('model_validation')),
  'ml_lifecycle':bool(life.get('registry_state')) and len(life.get('monitoring',[]))>=4,
  'agent_planning':len(agent.get('chosen_tool_sequence',[]))>=5 and agent.get('autonomous_execution') is False,
}
status='PASS' if all(checks.values()) else 'FAIL';out={'status':status,'project':wc.get('project_title'),'workspaces':n,'campaign':cam.get('campaign'),'checks':checks,'campaign_state':cam.get('state'),'data_mode':emp.get('data_mode'),'ml_registry':life.get('registry_state'),'agent':agent.get('agent')}
(ROOT/'artifacts').mkdir(exist_ok=True);(ROOT/'artifacts/final_depth_validation.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2));print('PROJECT_DEPTH_VALIDATION='+status);raise SystemExit(0 if status=='PASS' else 1)
