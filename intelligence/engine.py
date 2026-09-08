from __future__ import annotations
from tenx.engine import run_decision
from campaign.engine import run_campaign
from empirical.backbone import run_empirical_reference

def lifecycle_report():
    d=run_decision(11); v=d['model_validation']; mape=float(v.get('value',0) or 0); preds=d.get('predictions',{}); vals=[float(x) for x in preds.values()] if isinstance(preds,dict) else []
    cv=(max(vals)-min(vals))/(sum(vals)/len(vals)+1e-9) if vals else 0.0
    state='REFIT_GRAPH' if mape>.18 else ('DEMAND_WATCH' if mape>.11 else 'PLANNING_READY')
    public_data=d.get('public_data_backbone',{})
    return {'public_data_state':public_data.get('dataset_state'),'public_evidence_gate':d.get('public_evidence_gate'),'model_family':d['ml_family'],'target':d['prediction_target'],'validation':v,'corridor_dispersion':round(cv,5),'planning_readiness':state,'retrain_trigger':'retrain graph-temporal forecaster if rolling MAPE >11% or network topology/site availability materially changes','monitoring':['rolling corridor MAPE','spatial residual clusters','station demand drift','grid-headroom drift','forecast-to-capex decision delta'],'registry_state':'FORECAST_SHADOW' if state!='PLANNING_READY' else 'FORECAST_ACTIVE','source_mode':run_empirical_reference().get('data_mode')}

def run_agent():
    d=run_decision(11); life=lifecycle_report(); c=run_campaign(); steps=['load corridor/station/grid graph','forecast corridor demand','identify coverage and queue-risk gaps']
    state='CAPITAL_REVIEW'
    public_gate=d.get('public_evidence_gate')
    if public_gate=='REFERENCE_MODE_HOLD_FOR_REAL_DATA_CLAIM':
        steps.append('flag external public-data acquisition gap; prohibit real-data performance claim')
        state='REFERENCE_MODE_HOLD'
    if life['planning_readiness']=='REFIT_GRAPH': steps += ['hold capital recommendation','refresh topology/demand window','refit graph forecast']; state='FORECAST_HOLD'
    else: steps += ['construct GRIDWEAVE topology','run N-1 outage challenge','escalate shortlisted topology to exact multi-period MILP','assemble capital phasing memo']
    return {'agent':'Network Investment Planner','objective':'stage resilient charging capacity where forecast demand and grid headroom justify capital','prediction':d.get('predictions'),'decision':d['decision'],'decision_state':state,'chosen_tool_sequence':steps,'why_this_sequence':'forecast accuracy gates whether the system can safely move from geospatial diagnosis into capital optimization','challenge':d['counterfactual'],'ml_lifecycle':life,'resilience_state':c.get('state'),'operator_actions':['inspect high-growth corridors','review N-1 coverage premium','compare nominal vs resilient capex plan','approve/HOLD investment tranche'],'human_authority':d['human_authority'],'autonomous_execution':False}
