from __future__ import annotations
from pathlib import Path
import csv, json, hashlib, math, statistics
from datetime import datetime, timezone
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((Path(__file__).with_name("public_data_config.json")).read_text(encoding="utf-8"))

def _sha(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def _safe(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception: return None

def _read_csv(path:Path,limit=None):
    with path.open('r',encoding='utf-8-sig',errors='ignore',newline='') as f:
        r=csv.DictReader(f); rows=[]
        for i,row in enumerate(r):
            if limit is not None and i>=limit: break
            rows.append(row)
        return list(r.fieldnames or []),rows

def _profile_csv(path:Path):
    fields,rows=_read_csv(path)
    miss=0; cells=max(1,len(rows)*max(1,len(fields)))
    for row in rows:
        miss+=sum(1 for k in fields if str(row.get(k,'')).strip().lower() in {'','nan','na','null','none'})
    return {'path':str(path.relative_to(ROOT)),'rows':len(rows),'features':len(fields),'missing_fraction':round(miss/cells,6),'sha256':_sha(path),'bytes':path.stat().st_size}

def _profile_json(path:Path):
    obj=json.loads(path.read_text(encoding='utf-8'))
    rows=obj.get('fuel_stations',obj) if isinstance(obj,dict) else obj
    if not isinstance(rows,list): rows=[rows]
    fields=sorted(set().union(*(x.keys() for x in rows if isinstance(x,dict)))) if rows else []
    return {'path':str(path.relative_to(ROOT)),'rows':len(rows),'features':len(fields),'missing_fraction':None,'sha256':_sha(path),'bytes':path.stat().st_size}

def _primary_paths():
    d=ROOT/'data'/'raw'/CFG['raw_dir']
    return [d/name for name in CFG['primary_files']]

def _source_state():
    paths=_primary_paths(); present=[p for p in paths if p.exists()]
    active=len(present)==len(paths) and len(paths)>0
    if active: return 'PUBLISHED_EXTERNAL_ACTIVE' if CFG['ptype'] in {'trust','apex'} else 'REFRESHED_EXTERNAL_ACTIVE'
    return 'EXTERNAL_ACQUISITION_REQUIRED'

def _rows_numeric(features_path:Path,targets_path:Path|None=None,max_rows=12000,max_cols=80):
    fh,fr=_read_csv(features_path,limit=max_rows)
    th,tr=([],[]) if targets_path is None else _read_csv(targets_path,limit=max_rows)
    # retain columns with at least 60% numeric support, cap by missingness then variance later
    cols=[]
    for h in fh:
        vals=[_safe(r.get(h)) for r in fr]
        good=sum(v is not None for v in vals)
        if good>=max(5,int(.60*len(fr))): cols.append(h)
    cols=cols[:max_cols]
    X=np.empty((len(fr),len(cols)),float)
    for j,h in enumerate(cols):
        vals=np.array([np.nan if _safe(r.get(h)) is None else _safe(r.get(h)) for r in fr],float)
        med=float(np.nanmedian(vals)) if np.isfinite(vals).any() else 0.0
        vals=np.where(np.isfinite(vals),vals,med); X[:,j]=vals
    return cols,X,th,tr

def _auc(y,p):
    y=np.asarray(y,int); p=np.asarray(p,float); pos=np.where(y==1)[0]; neg=np.where(y==0)[0]
    if not len(pos) or not len(neg): return None
    ranks=np.argsort(np.argsort(p))+1
    u=ranks[pos].sum()-len(pos)*(len(pos)+1)/2
    return float(u/(len(pos)*len(neg)))

def _logistic_case(features_path:Path,targets_path:Path,positive_hint=None,max_cols=60):
    cols,X,th,tr=_rows_numeric(features_path,targets_path,max_cols=max_cols)
    if len(X)<20 or X.shape[1]<2: return {'status':'INSUFFICIENT_DATA'}
    # choose binary target: first column with exactly two useful levels; map +1/positive-like to 1.
    y=None; target_name=None
    for h in th:
        vals=[str(r.get(h,'')).strip() for r in tr[:len(X)]]; uniq=sorted(set(vals))
        if 1<len(uniq)<=2:
            target_name=h
            def conv(v):
                s=str(v).strip().lower()
                if positive_hint and positive_hint(s): return 1
                try: return 1 if float(s)>0 else 0
                except: return 1 if s in {'1','true','yes','fail','fault','positive','bad'} else 0
            y=np.array([conv(v) for v in vals],int); break
    if y is None: return {'status':'TARGET_NOT_BINARY','target_fields':th}
    n=min(len(X),len(y)); X=X[:n]; y=y[:n]
    # deterministic stratified split
    pos=np.where(y==1)[0]; neg=np.where(y==0)[0]
    test=np.sort(np.r_[pos[::5],neg[::5]]); mask=np.ones(n,bool); mask[test]=False; train=np.where(mask)[0]
    mu=X[train].mean(0); sd=X[train].std(0)+1e-9; Z=(X-mu)/sd
    # feature separation on training only
    sep=np.abs(Z[train][y[train]==1].mean(0)-Z[train][y[train]==0].mean(0)) if (y[train]==1).any() and (y[train]==0).any() else np.zeros(X.shape[1])
    keep=np.argsort(-sep)[:min(24,X.shape[1])]; Z=Z[:,keep]; kept=[cols[i] for i in keep]
    b=np.zeros(Z.shape[1]+1); A=np.c_[np.ones(n),Z]
    # class-balanced logistic gradient
    w=np.where(y==1, max(1.0,(y==0).sum()/max(1,(y==1).sum())),1.0); wt=w[train]
    for _ in range(450):
        q=np.clip(A[train]@b,-18,18); p=1/(1+np.exp(-q)); g=A[train].T@((p-y[train])*wt)/(wt.sum()+1e-9); g[1:]+=.015*b[1:]; b-=.12*g
    pt=1/(1+np.exp(-np.clip(A[test]@b,-18,18))); yt=y[test]
    brier=float(np.mean((pt-yt)**2)); auc=_auc(yt,pt)
    threshold=float(np.quantile(pt,.75)) if len(pt) else .5
    pred=(pt>=threshold).astype(int)
    tpr=float(((pred==1)&(yt==1)).sum()/max(1,(yt==1).sum())); tnr=float(((pred==0)&(yt==0)).sum()/max(1,(yt==0).sum())); bal=.5*(tpr+tnr)
    return {'status':'MODEL_ACTIVE','target':target_name,'rows':n,'fail_or_positive_rate':float(y.mean()),'selected_features':kept[:12],
            'holdout':{'rows':len(test),'brier':brier,'auc':auc,'balanced_accuracy':bal},'top_decile_risk':float(np.quantile(pt,.9)) if len(pt) else None,
            'model_family':'repository-authored class-balanced logistic diagnostic model (auxiliary; not headline ML family)'}

def _case_semiconductor():
    paths=_primary_paths()
    ctx=ROOT/'data'/'snapshots'/'tsmc_2025_manufacturing_scale.csv'
    out={'name':CFG['case'],'primary_dataset':CFG['dataset'],'context_profile':_profile_csv(ctx) if ctx.exists() else None}
    if not all(p.exists() for p in paths):
        out.update({'status':'ACQUISITION_REQUIRED','decision_impact':'QSHIFT remains in reference-fab mode; no real SECOM-based excursion prior is applied.','hold_reason':'SECOM raw features/targets have not been acquired into data/raw/uci_secom.'}); return out
    model=_logistic_case(paths[0],paths[1],positive_hint=lambda s:s in {'1','1.0','fail','fault','positive'})
    excursion=float(model.get('top_decile_risk') or model.get('fail_or_positive_rate') or 0)
    out.update({'status':'ACTIVE','yield_excursion_model':model,'qshift_excursion_prior':excursion,
                'decision_impact':'SECOM yield-excursion risk is promoted as an evidence prior into QSHIFT review; queue-time hazard remains a separate survival model because SECOM has no queue-time survival labels.'})
    return out

def _quadratic_predict(X,y,G):
    A=np.c_[np.ones(len(X)),X[:,0],X[:,1],X[:,0]**2,X[:,1]**2,X[:,0]*X[:,1]]
    b=np.linalg.lstsq(A,y,rcond=None)[0]; Q=np.c_[np.ones(len(G)),G[:,0],G[:,1],G[:,0]**2,G[:,1]**2,G[:,0]*G[:,1]]
    return Q@b,b

def _rbf_gp(X,y,G,length=.55,noise=.03):
    X=np.asarray(X,float); y=np.asarray(y,float); G=np.asarray(G,float)
    mu=X.mean(0); sd=X.std(0)+1e-9; Z=(X-mu)/sd; Zg=(G-mu)/sd
    def K(A,B):
        d=((A[:,None,:]-B[None,:,:])**2).sum(2); return np.exp(-.5*d/(length*length))
    C=K(Z,Z)+np.eye(len(Z))*noise; L=np.linalg.cholesky(C); alpha=np.linalg.solve(L.T,np.linalg.solve(L,y)); Ks=K(Zg,Z); m=Ks@alpha; v=np.linalg.solve(L,Ks.T); s=np.sqrt(np.maximum(1-(v*v).sum(0),1e-12)); return m,s

def _case_trust():
    p=_primary_paths()[0]; prof=_profile_csv(p) if p.exists() else None
    if not p.exists(): return {'name':CFG['case'],'status':'ACQUISITION_REQUIRED'}
    h,r=_read_csv(p); X=np.array([[float(x['pressure_torr']),float(x['h2_wf6_ratio'])] for x in r]); u=np.array([float(x['uniformity_pct']) for x in r]); s=np.array([float(x['stress']) for x in r])
    # leave-one-out GP error vs quadratic baseline
    gp_err=[]; quad_err=[]
    for i in range(len(X)):
        ix=np.array([j for j in range(len(X)) if j!=i]); gm,_=_rbf_gp(X[ix],u[ix],X[i:i+1]); qm,_=_quadratic_predict(X[ix],u[ix],X[i:i+1]); gp_err.append(float(gm[0]-u[i])); quad_err.append(float(qm[0]-u[i]))
    grid=np.array([[a,b] for a in np.linspace(X[:,0].min(),X[:,0].max(),31) for b in np.linspace(X[:,1].min(),X[:,1].max(),31)])
    um,usd=_rbf_gp(X,u,grid); sm,ssd=_rbf_gp(X,s,grid)
    # SAFE-TRUST: conservative stress <=8.20, objective uniformity low, information bonus; distance penalty from design center
    stress_ucb=sm+1.645*ssd; feasible=stress_ucb<=8.20; center=X.mean(0); span=np.ptp(X,axis=0)+1e-9; radius=np.linalg.norm((grid-center)/span,axis=1)
    score=-um+.55*usd-.08*radius; score=np.where(feasible,score,-1e9+(-stress_ucb))
    j=int(np.argmax(score)); naive=int(np.argmin(um))
    choice={'pressure_torr':float(grid[j,0]),'h2_wf6_ratio':float(grid[j,1]),'pred_uniformity_pct':float(um[j]),'uniformity_sd':float(usd[j]),'pred_stress':float(sm[j]),'stress_ucb95':float(stress_ucb[j]),'conservative_feasible':bool(feasible[j])}
    return {'name':CFG['case'],'status':'ACTIVE','profile':prof,'observed_runs':len(r),'gp_leave_one_out_rmse':float(np.sqrt(np.mean(np.square(gp_err)))),'quadratic_leave_one_out_rmse':float(np.sqrt(np.mean(np.square(quad_err)))),
            'safe_operating_grid_fraction':float(feasible.mean()),'safe_trust_next_experiment':choice,'naive_min_uniformity_candidate':{'pressure_torr':float(grid[naive,0]),'h2_wf6_ratio':float(grid[naive,1]),'stress_ucb95':float(stress_ucb[naive])},
            'algorithm':'SAFE-TRUST-v1','decision_impact':'Published NIST observations directly fit the GP posterior and determine the conservative next-experiment recommendation.'}

def _case_kaizen():
    paths=_primary_paths()
    if not all(p.exists() for p in paths): return {'name':CFG['case'],'status':'ACQUISITION_REQUIRED','decision_impact':'TRACE-LIFT remains in reference mode until UCI hydraulic observations are acquired.'}
    cols,X,th,tr=_rows_numeric(paths[0],paths[1],max_rows=5000,max_cols=60)
    # use first condition target with >1 levels; rank sensor shifts across healthiest/worst observed target level
    target=th[0] if th else None; vals=[str(x.get(target,'')) for x in tr[:len(X)]] if target else []
    levels=sorted(set(vals));
    if len(levels)<2: return {'name':CFG['case'],'status':'TARGET_UNAVAILABLE'}
    a=np.array([v==levels[0] for v in vals]); b=np.array([v==levels[-1] for v in vals]); mu=X.mean(0); sd=X.std(0)+1e-9; effect=np.abs((X[b].mean(0)-X[a].mean(0))/sd) if a.any() and b.any() else np.zeros(X.shape[1]); order=np.argsort(-effect)[:8]
    probes=[]
    for rank,j in enumerate(order):
        sep=float(min(1,effect[j]/3)); rel=float(1/(1+.08*rank)); ambiguity=float(max(.05,1-sep*.7)); burden=float(.12+.05*rank); score=1.3*ambiguity*sep+1.1*rel-.7*burden+.25*sep
        probes.append({'probe':cols[j],'standardized_condition_shift':float(effect[j]),'trace_lift_score':score,'reliability':rel,'burden':burden})
    return {'name':CFG['case'],'status':'ACTIVE','rows':len(X),'target':target,'condition_levels':levels[:12],'trace_lift_probe_queue':sorted(probes,key=lambda x:-x['trace_lift_score']),'algorithm':'TRACE-LIFT-v1','decision_impact':'Observed condition-state separation determines the diagnostic probe priority sent to CAPE experiment allocation.'}

def _case_volterra():
    p=_primary_paths()[0]
    if not p.exists(): return {'name':CFG['case'],'status':'ACQUISITION_REQUIRED','decision_impact':'GRIDWEAVE remains on reference geography until an AFDC station snapshot is acquired. Tesla operations metrics are context only.'}
    obj=json.loads(p.read_text(encoding='utf-8')); rows=obj.get('fuel_stations',[]) if isinstance(obj,dict) else obj
    elec=[x for x in rows if str(x.get('fuel_type_code','ELEC')).upper()=='ELEC']; bycity={}; bynet={}
    for x in elec:
        city=str(x.get('city','UNKNOWN')); bycity[city]=bycity.get(city,0)+int(x.get('ev_dc_fast_num') or x.get('ev_level2_evse_num') or 1)
        net=str(x.get('ev_network') or x.get('ev_network_web') or 'UNKNOWN'); bynet[net]=bynet.get(net,0)+1
    sparse=sorted(bycity.items(),key=lambda z:(z[1],z[0]))[:12]; dense=sorted(bycity.items(),key=lambda z:(-z[1],z[0]))[:12]
    tesla=[x for x in elec if 'tesla' in str(x.get('ev_network','')).lower() or 'supercharger' in str(x.get('station_name','')).lower()]
    return {'name':CFG['case'],'status':'ACTIVE','profile':_profile_json(p),'station_records':len(elec),'network_counts':dict(sorted(bynet.items(),key=lambda z:-z[1])[:12]),'dense_city_port_proxy':dense,'coverage_gap_candidates':sparse,'tesla_filtered_records':len(tesla),
            'algorithm':'GRIDWEAVE-v1','decision_impact':'Observed station/network density defines the coverage deficit and candidate-priority layer before exact multi-period/N-1 investment optimization.'}

def _case_apex():
    p=_primary_paths()[0]
    if not p.exists(): return {'name':CFG['case'],'status':'ACQUISITION_REQUIRED'}
    h,r=_read_csv(p); assumptions={
      'Model Y Rear-Wheel Drive':{'battery_usable_kwh':72.0,'motor_power_kw':220.0,'drag_coefficient':.24,'frontal_area_m2':2.78,'gear_ratio':9.0},
      'Model Y Premium AWD':{'battery_usable_kwh':79.0,'motor_power_kw':330.0,'drag_coefficient':.24,'frontal_area_m2':2.78,'gear_ratio':9.0},
      'Model Y Performance AWD':{'battery_usable_kwh':79.0,'motor_power_kw':430.0,'drag_coefficient':.24,'frontal_area_m2':2.78,'gear_ratio':9.0}}
    rows=[]
    # conservative transparent engineering proxy; assumption fields are not claimed as Tesla internals.
    for x in r:
        a=assumptions.get(x['variant']); mass=float(x['curb_weight_lb'])*0.45359237; official_range=float(x['epa_range_miles']); official_acc=float(x['zero_to_60_s'])
        consumption=13.4+5.2*(a['drag_coefficient']*a['frontal_area_m2'])+0.0022*(mass-1500)+.55*abs(a['gear_ratio']-9.0)
        pred_range_mi=(100*a['battery_usable_kwh']*.92/consumption)*0.621371
        pred_acc=max(2.8,8.6-0.0105*a['motor_power_kw']+0.00075*mass+.10*abs(a['gear_ratio']-9))
        rows.append({'variant':x['variant'],'official_epa_range_mi':official_range,'proxy_range_mi':pred_range_mi,'range_abs_pct_error':abs(pred_range_mi-official_range)/official_range,
                     'official_0_60_s':official_acc,'proxy_0_60_s':pred_acc,'accel_abs_pct_error':abs(pred_acc-official_acc)/official_acc,'assumptions':a})
    rm=float(statistics.fmean(x['range_abs_pct_error'] for x in rows)); am=float(statistics.fmean(x['accel_abs_pct_error'] for x in rows)); ready=rm<=.25 and am<=.35
    return {'name':CFG['case'],'status':'ACTIVE','profile':_profile_csv(p),'benchmarks':rows,'mean_range_abs_pct_error':rm,'mean_accel_abs_pct_error':am,'benchmark_readiness':'SURROGATE_SCREENING_ALLOWED' if ready else 'EXACT_PHYSICS_REQUIRED',
            'algorithm':'ARCH-SHIELD-v1','decision_impact':'ARCH-SHIELD blocks surrogate-led architecture release when public benchmark error is outside the configured verification band.','assumption_boundary':'Battery usable energy, motor power, drag coefficient, frontal area and gear ratio are explicit repository assumptions used only for calibration diagnostics.'}

def _case_mqi():
    paths=_primary_paths()
    if not all(p.exists() for p in paths): return {'name':CFG['case'],'status':'ACQUISITION_REQUIRED','decision_impact':'GAGE-SHIELD remains in reference mode until Steel Plates Faults observations are acquired.'}
    model=_logistic_case(paths[0],paths[1],positive_hint=lambda s:s not in {'0','0.0','false','normal','pass','good'},max_cols=40)
    return {'name':CFG['case'],'status':'ACTIVE','defect_risk_model':model,'algorithm':'GAGE-SHIELD-v1','decision_impact':'Observed defect-risk calibration is combined with measurement discrimination before inspection-capacity allocation; risk alone cannot authorize containment.'}

def _case_rehab():
    paths=_primary_paths()
    if not all(p.exists() for p in paths): return {'name':CFG['case'],'status':'ACQUISITION_REQUIRED','decision_impact':'MOTION-GUARD remains in research/reference mode until UCI activity/postural-transition observations are acquired.'}
    cols,X,th,tr=_rows_numeric(paths[0],paths[1],max_rows=6000,max_cols=50)
    target=th[0] if th else None; labels=[str(x.get(target,'')) for x in tr[:len(X)]] if target else []
    uniq=sorted(set(labels)); counts={u:labels.count(u) for u in uniq}
    # state confidence proxy from nearest centroid over the six most common states
    common=[u for u,_ in sorted(counts.items(),key=lambda z:-z[1])[:6]]; idx=np.array([x in common for x in labels]); Z=X[idx]; lab=np.array(labels)[idx]
    mu=Z.mean(0); sd=Z.std(0)+1e-9; Z=(Z-mu)/sd; cents={u:Z[lab==u].mean(0) for u in common if (lab==u).any()}
    correct=0; margins=[]
    for z,y in zip(Z[::7],lab[::7]):
        d=sorted((float(np.linalg.norm(z-c)),u) for u,c in cents.items()); correct+=int(d[0][1]==y); margins.append((d[1][0]-d[0][0])/(d[1][0]+1e-9) if len(d)>1 else 0)
    acc=correct/max(1,len(Z[::7])); conf=float(statistics.fmean(margins)) if margins else 0; guard='PROGRESSION_REVIEW_ALLOWED' if acc>=.55 and conf>=.03 else 'HOLD_PROGRESSION'
    return {'name':CFG['case'],'status':'ACTIVE','rows':len(X),'state_target':target,'dominant_states':dict(sorted(counts.items(),key=lambda z:-z[1])[:10]),'nearest_centroid_holdout_accuracy':acc,'mean_state_separation_margin':conf,'motion_guard_state':guard,
            'algorithm':'MOTION-GUARD-v1','decision_impact':'Observed movement-state separability becomes an evidence-readiness gate; low state confidence blocks progression before APACE/digital-twin escalation.','clinical_boundary':'Movement/activity-state validation only; not clinical efficacy evidence.'}

def run_public_case():
    return {'semiconductor':_case_semiconductor,'trust':_case_trust,'kaizen':_case_kaizen,'volterra':_case_volterra,'apex':_case_apex,'mqi':_case_mqi,'rehab':_case_rehab}[CFG['ptype']]()

def data_backbone_status():
    primary=[]
    for p in _primary_paths():
        if p.exists(): primary.append(_profile_json(p) if p.suffix.lower()=='.json' else _profile_csv(p))
        else: primary.append({'path':str(p.relative_to(ROOT)),'exists':False})
    static=[]
    for rel in CFG.get('static_files',[]):
        p=ROOT/rel
        if p.exists(): static.append(_profile_json(p) if p.suffix.lower()=='.json' else _profile_csv(p))
    case=run_public_case(); state=_source_state()
    return {'project':CFG['title'],'dataset':CFG['dataset'],'dataset_state':state,'generated_at_utc':datetime.now(timezone.utc).isoformat(),
            'layers':{'raw':'data/raw','processed':'data/processed','contracts':'data/contracts','dictionaries':'data/dictionaries','provenance':'data/provenance','snapshots':'data/snapshots'},
            'primary_files':primary,'static_context_files':static,'schema_validation':'PASS' if all(x.get('exists',True) for x in primary) else 'ACQUISITION_REQUIRED','case_study':case,
            'current_model_version':CFG.get('model_version'),'claim_boundary':CFG.get('claim_boundary',CFG.get('claim','')),'human_authority':CFG.get('human_authority',CFG.get('authority','HUMAN_REVIEW')),'autonomous_execution':False}

def integrate_decision(decision:dict):
    status=data_backbone_status(); case=status['case_study']; decision['public_data_backbone']=status; decision['real_data_case']=case
    decision.setdefault('tool_trace',[]).insert(0,f"inspect {CFG['dataset']} data backbone and claim ledger")
    decision.setdefault('user_aid',[]).append('review data backbone status, raw hash, validation state and claim category before approval')
    # Evidence-dependent decision gate. Never overwrite the underlying OR/algorithm result; add a human review state.
    if case.get('status')!='ACTIVE':
        decision['public_evidence_gate']='REFERENCE_MODE_HOLD_FOR_REAL_DATA_CLAIM'
    elif CFG['ptype']=='apex' and case.get('benchmark_readiness')=='EXACT_PHYSICS_REQUIRED':
        decision['public_evidence_gate']='EXACT_PHYSICS_REQUIRED'; decision.setdefault('abstention_conditions',[]).append('public benchmark calibration outside release band')
    elif CFG['ptype']=='rehab' and case.get('motion_guard_state')=='HOLD_PROGRESSION':
        decision['public_evidence_gate']='HOLD_PROGRESSION'; decision.setdefault('abstention_conditions',[]).append('movement-state confidence insufficient')
    else:
        decision['public_evidence_gate']='PUBLIC_EVIDENCE_REVIEWABLE'
    return decision
