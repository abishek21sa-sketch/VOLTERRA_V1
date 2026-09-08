from collections import Counter
import statistics
from tenx.engine import _run_decision_core, ALL
from empirical.backbone import run_empirical_reference
SEEDS=[4,8,16,28,37,49,63]
def run_campaign():
    runs=[_run_decision_core(s) for s in SEEDS]; site_sets=[tuple(r['decision']['open_sites']) for r in runs]; first=[x[0] for x in site_sets]; modal,n=Counter(first).most_common(1)[0]; capex={x['id']:x['capex'] for x in ALL}; covers={x['id']:set(x['covers']) for x in ALL}; critical={1,2}
    margins=[]; costs=[]
    for ss in site_sets:
        counts=[sum(j in covers[s] for s in ss) for j in critical]; margins.append(min(counts)-1); costs.append(sum(capex[s] for s in ss))
    mapes=[r['model_validation']['value'] for r in runs]; margin=min(margins); emp=run_empirical_reference(); state='CAPITAL_PLAN_REVIEW' if margin>=1 and statistics.mean(mapes)<.35 else 'HOLD_CAPITAL_PLAN'
    return {'campaign':'Resilient Network Capital Campaign','campaign_identity':'N-1 coverage margin + CAPEX/service frontier','scenario_count':len(runs),'scenario_seeds':SEEDS,'state':state,'coverage_margin':margin,'modal_first_site':modal,'first_site_share':round(n/len(runs),4),'mean_forecast_mape':round(statistics.mean(mapes),5),'mean_selected_capex':round(statistics.mean(costs),2),'scenario_matrix':[{'seed':s,'open_sites':r['decision']['open_sites'],'forecast_mape':r['model_validation']['value'],'modeled_capex':costs[i],'n_minus_one_margin':margins[i]} for i,(s,r) in enumerate(zip(SEEDS,runs))],'ai_synthesis':{'why':f'Minimum N-1 coverage margin={margin}; modal first site={modal}; mean forecast MAPE={statistics.mean(mapes):.3f}.','challenge':'A cheap or high-demand topology is not board-ready if a critical corridor loses redundancy after one site outage.','recommended_operator_action':'Review redundancy/CAPEX tradeoff and escalate the stable topology to the exact expansion MILP.','abstention_conditions':['N-1 margin below one','severe grid derating','forecast drift','no redundant critical-corridor candidate']},'action_queue':['inspect high queue-risk corridors','review GRIDWEAVE topology','verify N-1 contingency matrix','solve exact multi-period expansion','approve or defer capital phase'],'human_authority':'INFRASTRUCTURE_INVESTMENT_REVIEW','autonomous_execution':False,'empirical_provenance':{'mode':emp.get('data_mode'),'promotion':emp.get('empirical_promotion')}}
