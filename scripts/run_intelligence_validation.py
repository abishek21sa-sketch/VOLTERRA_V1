from pathlib import Path
import json,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
if (ROOT/'src').exists(): sys.path.insert(0,str(ROOT/'src'))
from intelligence.engine import lifecycle_report,run_agent
from tenx.engine import run_decision
t=time.perf_counter(); life=lifecycle_report(); agent=run_agent(); decision=run_decision()
server=(ROOT/'scripts/start_tenx_workstation.py').read_text(encoding='utf-8')
checks={
  'unique_model_family':bool(life.get('model_family')),
  'validation_metric':bool(life.get('validation',{}).get('metric')),
  'monitoring_depth':len(life.get('monitoring',[]))>=4,
  'retraining_policy':bool(life.get('retrain_trigger')),
  'registry_state':bool(life.get('registry_state')),
  'agent_branches_on_model_state':len(agent.get('chosen_tool_sequence',[]))>=5,
  'prediction_used':agent.get('prediction') is not None,
  'decision_used':bool(agent.get('decision')),
  'counterfactual_challenge':bool(agent.get('challenge')),
  'operator_aid':len(agent.get('operator_actions',[]))>=3,
  'human_authority':bool(agent.get('human_authority')) and agent.get('autonomous_execution') is False,
  'original_algorithm_preserved':bool(decision.get('original_algorithm')),
  'ml_endpoint':'/api/ml/lifecycle' in server,
  'agent_endpoint':'/api/agent/run' in server,
}
status='PASS' if all(checks.values()) else 'FAIL'; out={'status':status,'project':decision.get('project'),'model_family':life.get('model_family'),'registry_state':life.get('registry_state'),'agent':agent.get('agent'),'decision_state':agent.get('decision_state'),'runtime_ms':round((time.perf_counter()-t)*1000,3),'checks':checks}
(ROOT/'artifacts').mkdir(exist_ok=True);(ROOT/'artifacts/intelligence_validation.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2));print('INTELLIGENCE_VALIDATION='+status);raise SystemExit(0 if status=='PASS' else 1)
