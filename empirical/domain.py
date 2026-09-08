from pathlib import Path
import csv,statistics,collections,math
ROOT=Path(__file__).resolve().parents[1]
def _corr(a,b):
 if len(a)<3:return 0.0
 ma,mb=statistics.fmean(a),statistics.fmean(b); num=sum((x-ma)*(y-mb) for x,y in zip(a,b)); da=sum((x-ma)**2 for x in a); db=sum((y-mb)**2 for y in b); return num/math.sqrt(max(1e-12,da*db))
def domain_diagnostics():
 rows=list(csv.DictReader((ROOT/'ml/data/real_sites_holdout.csv').open())); sites=collections.defaultdict(list)
 for r in rows:sites[r['name']].append(r)
 summary=[]
 for name,rr in sites.items():summary.append({'site':name,'n':len(rr),'mean_wait_min':round(statistics.fmean(float(x['mean_wait_min']) for x in rr),3),'p_wait_over_15min':round(statistics.fmean(float(x['p_wait_over_15min']) for x in rr),4),'mean_utilization':round(statistics.fmean(float(x['utilization']) for x in rr),3)})
 summary.sort(key=lambda x:(x['p_wait_over_15min'],x['mean_wait_min']),reverse=True); temp=[float(r['temp_c']) for r in rows]; wait=[float(r['mean_wait_min']) for r in rows]
 return {'analysis':'station queue-risk and climate sensitivity','metrics':{'records':len(rows),'sites':len(sites),'temperature_wait_correlation':round(_corr(temp,wait),4)},'highest_queue_risk_sites':summary[:8],'decision_signal':'Use high queue-risk sites/corridors as GRIDWEAVE repair candidates, then verify N-1 coverage and exact expansion MILP feasibility.','evidence_boundary':'Current holdout file is local validation evidence; AFDC/NLR station refresh is required for external Illinois network claims.'}
