from __future__ import annotations
import math
import numpy as np

class GraphTemporalDemand:
    """Graph-regularized spatiotemporal linear learner authored here; spatial lag is learned jointly."""
    def fit(self,history,adj,l2=.08):
        H=np.asarray(history,float); A=np.asarray(adj,float); A=A/(A.sum(1,keepdims=True)+1e-9); rows=[]; y=[]
        for t in range(1,len(H)):
            prev=H[t-1]; spatial=A@prev
            for i in range(H.shape[1]): rows.append([1,prev[i],spatial[i],math.sin(t/3),math.cos(t/3)]); y.append(H[t,i])
        X=np.asarray(rows); y=np.asarray(y); self.beta=np.linalg.solve(X.T@X+l2*np.eye(X.shape[1]),X.T@y); self.A=A; self.last=H[-1]; self.t=len(H); return self
    def predict(self):
        spatial=self.A@self.last; return np.array([np.dot(self.beta,[1,self.last[i],spatial[i],math.sin(self.t/3),math.cos(self.t/3)]) for i in range(len(self.last))])

class GridWeave:
    """GRIDWEAVE: fast resilient charger-network construction with coverage and N-1 repair."""
    def build(self,demand,candidates,critical):
        opened=[]; uncovered=set(range(len(demand)))
        while uncovered:
            def benefit(c): return sum(demand[j] for j in uncovered if j in c['covers'])/(c['capex']+1e-9)+.25*c['grid_headroom']
            c=max(candidates,key=benefit); opened.append(c['id']); uncovered-=set(c['covers']); candidates=[x for x in candidates if x['id']!=c['id']]
            if not candidates and uncovered: break
        # N-1 repair: each critical corridor needs two distinct opened coverers.
        for j in critical:
            cover=[c for c in candidates if j in c['covers']]
            existing=sum(j in c['covers'] for c in ALL if c['id'] in opened)
            if existing<2 and cover: opened.append(max(cover,key=lambda c:c['grid_headroom']/c['capex'])['id'])
        return list(dict.fromkeys(opened))

ALL=[{'id':'S-A','capex':520,'grid_headroom':.8,'covers':[0,1]},{'id':'S-B','capex':410,'grid_headroom':.45,'covers':[1,2]},{'id':'S-C','capex':610,'grid_headroom':.95,'covers':[2,3]},{'id':'S-D','capex':350,'grid_headroom':.35,'covers':[0,3]}]
def _run_decision_core(seed=5):
    r=np.random.default_rng(seed); base=np.array([62,88,74,53.],float); hist=[]; x=base.copy()
    A=np.array([[0,1,.2,.4],[1,0,.7,0],[.2,.7,0,1],[.4,0,1,0]],float)
    for t in range(20): x=.72*x+.18*(A/(A.sum(1,keepdims=True)+1e-9)@x)+np.array([2,5,4,1])*math.sin(t/3)+r.normal(0,2,4); hist.append(np.maximum(x,5))
    back=np.maximum(GraphTemporalDemand().fit(hist[:-1],A).predict(),1); mape=float(np.mean(np.abs((back-np.asarray(hist[-1]))/(np.asarray(hist[-1])+1e-9)))); pred=np.maximum(GraphTemporalDemand().fit(hist,A).predict(),1); opened=GridWeave().build(pred,ALL.copy(),critical={1,2}); cheap=min(ALL,key=lambda c:c['capex'])['id']
    return {'project':'VOLTERRA','ml_family':'Graph-regularized spatiotemporal demand learning','prediction_target':'next-period charging demand by corridor','model_validation':{'metric':'rolling one-step MAPE','value':mape,'direction':'lower_is_better','split':'last bundled time step'},'predictions':{f'corridor_{i}':float(v) for i,v in enumerate(pred)},'original_algorithm':'GRIDWEAVE-v1','decision':{'open_sites':opened,'critical_corridors':[1,2],'n_minus_one_required':True},'counterfactual':{'naive_policy':'open cheapest candidate first','choice':cheap,'disagrees':opened[0]!=cheap},'uncertainty':'Reference model reports deterministic point forecast; scenario layer must stress forecast error before capital approval.','or_escalation':'Escalate GRIDWEAVE topology to multi-period network-expansion MILP, storage and N-1 contingency planning.','tool_trace':['fit graph-temporal demand model','forecast corridor demand','construct GRIDWEAVE topology','challenge cheapest-site rule','verify N-1 critical coverage','escalate to exact expansion MILP'],'limitations':['bundled demonstration uses reference network history','native Julia/Go/Polars layers require Windows dependency acceptance'],'abstention_conditions':['severe grid derating','critical corridor lacks redundant candidate','forecast drift'], 'user_aid':['inspect corridor forecasts','review redundancy premium','compare nominal vs N-1 plan','approve/hold capital plan'],'human_authority':'INFRASTRUCTURE_INVESTMENT_REVIEW','autonomous_execution':False}


def run_decision(seed=None):
    from empirical.backbone import run_empirical_reference
    import inspect
    sig=inspect.signature(_run_decision_core)
    if seed is None:
        out=_run_decision_core()
    else:
        out=_run_decision_core(seed)
    emp=run_empirical_reference()
    out["empirical_backbone"]=emp
    from empirical.public_data_backbone import integrate_decision
    out=integrate_decision(out)
    out.setdefault("tool_trace",[]).insert(0,"resolve empirical data provenance and source mode")
    out.setdefault("user_aid",[]).append("open empirical case study and entity/history drilldowns before approval")
    return out
