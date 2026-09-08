from __future__ import annotations
from pathlib import Path
import csv, json, hashlib, math, statistics, zipfile, io, re
ROOT=Path(__file__).resolve().parents[1]
MANIFEST=json.loads((Path(__file__).with_name("source_manifest.json")).read_text())
CONTRACT=json.loads((Path(__file__).with_name("project_contract.json")).read_text())
from empirical.domain import domain_diagnostics
EXTERNAL=ROOT/'data'/'external'
EXCLUDE={"README.md","fetch_public_data.ps1","REFERENCE_FIXTURE_NOT_EXTERNAL.csv","nist_cvd_cci.csv"}

def _safe_float(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except Exception:return None

def _read_csv_bytes(raw, delimiter=None, max_rows=10000):
    text=raw.decode('utf-8',errors='ignore').strip()
    if not text:return [],[]
    lines=text.splitlines()
    if delimiter is None:
        delimiter=',' if sum(',' in x for x in lines[:5])>=2 else None
    rows=[]
    if delimiter:
        r=csv.reader(lines,delimiter=delimiter)
        allrows=list(r)
        if not allrows:return [],[]
        first=allrows[0]
        header_like=any(_safe_float(x) is None for x in first)
        header=[str(x).strip() or f'c{i}' for i,x in enumerate(first)] if header_like else [f'c{i}' for i in range(len(first))]
        data=allrows[1:] if header_like else allrows
        for rr in data[:max_rows]: rows.append({header[i]:rr[i] if i<len(rr) else '' for i in range(len(header))})
        return header,rows
    # whitespace table
    allrows=[re.split(r'\s+',x.strip()) for x in lines if x.strip()]
    if not allrows:return [],[]
    first=allrows[0]; header_like=any(_safe_float(x) is None for x in first)
    header=[str(x).strip() or f'c{i}' for i,x in enumerate(first)] if header_like else [f'c{i}' for i in range(len(first))]
    data=allrows[1:] if header_like else allrows
    for rr in data[:max_rows]: rows.append({header[i]:rr[i] if i<len(rr) else '' for i in range(len(header))})
    return header,rows

def _flatten_numeric(obj,prefix='root',out=None):
    out=[] if out is None else out
    if isinstance(obj,dict):
        for k,v in obj.items(): _flatten_numeric(v,f'{prefix}.{k}',out)
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:5000]): _flatten_numeric(v,f'{prefix}[{i}]',out)
    elif isinstance(obj,(int,float)) and not isinstance(obj,bool): out.append((prefix,float(obj)))
    return out

def _load_path(path:Path):
    raw=path.read_bytes(); sha=hashlib.sha256(raw).hexdigest()
    info={'exists':True,'path':str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),'bytes':len(raw),'sha256':sha}
    suffix=path.suffix.lower()
    try:
        if suffix=='.csv':
            hdr,rows=_read_csv_bytes(raw,','); return info,hdr,rows
        if suffix=='.json':
            obj=json.loads(raw.decode('utf-8',errors='ignore'))
            if isinstance(obj,list) and obj and isinstance(obj[0],dict):
                hdr=sorted(set().union(*(x.keys() for x in obj if isinstance(x,dict)))); return info,hdr,obj[:10000]
            nums=_flatten_numeric(obj); rows=[{'metric':k,'value':v,'order':i} for i,(k,v) in enumerate(nums)]
            return info,['metric','value','order'],rows
        if suffix=='.zip':
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                candidates=[n for n in z.namelist() if n.lower().endswith(('.csv','.data','.txt','.nna')) and not n.endswith('/')]
                candidates=sorted(candidates,key=lambda n:z.getinfo(n).file_size,reverse=True)
                if candidates:
                    n=candidates[0]; member=z.read(n); hdr,rows=_read_csv_bytes(member,None)
                    info['archive_member']=n; return info,hdr,rows
        if suffix in {'.txt','.data','.nna','.dat'}:
            hdr,rows=_read_csv_bytes(raw,None); return info,hdr,rows
    except Exception as e: info['parse_error']=type(e).__name__
    return info,[],[]

def _external_candidates():
    return [p for p in EXTERNAL.iterdir() if p.is_file() and p.name not in EXCLUDE and not p.name.endswith('.partial')]

def _choose_evidence():
    real=_external_candidates()
    if real:
        return 'refreshed_external',max(real,key=lambda p:p.stat().st_size)
    nist=EXTERNAL/'nist_cvd_cci.csv'
    if nist.exists(): return 'published_external_snapshot',nist
    local=ROOT/CONTRACT['local_evidence']
    return 'offline_reference',local

def _numeric_columns(rows,headers):
    out={}
    for h in headers:
        vals=[_safe_float(r.get(h)) for r in rows[:10000] if isinstance(r,dict)]
        vals=[v for v in vals if v is not None]
        if len(vals)>=max(3,min(10,len(rows)//4 or 3)): out[h]=vals
    return out

def _numeric_summary(rows,headers):
    nums=_numeric_columns(rows,headers); summary=[]
    for h,v in nums.items():
        try: sd=statistics.pstdev(v) if len(v)>1 else 0.0
        except: sd=0.0
        summary.append({'factor':h,'n':len(v),'mean':round(statistics.fmean(v),6),'sd':round(sd,6),'min':round(min(v),6),'max':round(max(v),6)})
    return sorted(summary,key=lambda x:x['sd'],reverse=True)[:12]

def _cohort_shift(rows,headers):
    nums=_numeric_columns(rows,headers); n=len(rows); out=[]
    if n<6:return []
    cut=n//2
    for h in nums:
        a=[_safe_float(r.get(h)) for r in rows[:cut]]; b=[_safe_float(r.get(h)) for r in rows[cut:]]
        a=[x for x in a if x is not None]; b=[x for x in b if x is not None]
        if len(a)<2 or len(b)<2:continue
        ma,mb=statistics.fmean(a),statistics.fmean(b); allv=a+b; sd=statistics.pstdev(allv) if len(allv)>1 else 0
        z=(mb-ma)/(sd+1e-12)
        out.append({'factor':h,'first_half_mean':round(ma,6),'second_half_mean':round(mb,6),'delta':round(mb-ma,6),'standardized_shift':round(z,4)})
    return sorted(out,key=lambda x:abs(x['standardized_shift']),reverse=True)[:10]

def _categorical_rank(rows,headers):
    ranks=[]
    for h in headers:
        vals=[str(r.get(h,'')) for r in rows[:10000] if str(r.get(h,'')) not in {'','None','nan'}]
        if not vals:continue
        unique=set(vals)
        if 1<len(unique)<=min(30,max(5,len(vals)//2)):
            counts={u:vals.count(u) for u in unique}; top=sorted(counts.items(),key=lambda x:x[1],reverse=True)[:8]
            ranks.append({'factor':h,'unique':len(unique),'top_levels':[{'level':u,'count':c} for u,c in top]})
    return ranks[:6]

def run_empirical_reference():
    mode,path=_choose_evidence(); profile,headers,rows=_load_path(path)
    profile['rows']=len(rows); profile['columns']=len(headers); profile['data_mode']=mode
    summary=_numeric_summary(rows,headers); shifts=_cohort_shift(rows,headers); categories=_categorical_rank(rows,headers)
    top_shift=shifts[0] if shifts else None; top_var=summary[0] if summary else None
    facts=[f"Evidence mode: {mode}.",f"Loaded {len(rows):,} analyzable records with {len(headers)} fields from {profile.get('path','unknown')}."]
    if top_shift:facts.append(f"Largest first-half vs second-half standardized shift: {top_shift['factor']} ({top_shift['standardized_shift']:+.2f} SD).")
    if top_var:facts.append(f"Highest-variation numeric factor in the current evidence: {top_var['factor']} (SD {top_var['sd']}).")
    inference=(f"{top_shift['factor']} is a high-value diagnostic factor because its distribution changes most strongly across the evidence ordering." if top_shift else "The local evidence is too small for a stable cohort-shift ranking; use it only as a reference fixture.")
    recommendation=(f"Drill into {top_shift['factor']} before changing {CONTRACT['decision']}; then compare the predictive model, original algorithm and OR policy on the same scoped evidence." if top_shift else f"Refresh the public source before changing {CONTRACT['decision']}.")
    limitation=("This analysis uses refreshed/published external observations and still does not establish causality." if mode!='offline_reference' else "This is repository reference evidence, not an external observational dataset; do not use it for real-world performance claims.")
    promoted=mode in {'refreshed_external','published_external_snapshot'}
    return {
      'data_mode':mode,'empirical_promotion':('EXTERNAL_EVIDENCE_ACTIVE' if promoted else 'REFERENCE_ONLY'),
      'source':MANIFEST,'local_profile':profile,'entity_grain':CONTRACT['entity_grain'],'historical_intelligence':CONTRACT['historical_intelligence'],
      'numeric_summary':summary,'cohort_shift':shifts,'categorical_explorer':categories,'domain_diagnostics':domain_diagnostics(),
      'case_study':{'name':MANIFEST.get('case','Empirical case study'),'questions':CONTRACT['case_questions'],'facts':facts,'inferences':[inference],'recommendation':recommendation,'limitations':[limitation]},
      'analysis_stack':['source provenance','schema/data-quality checks','entity/factor drilldown','cohort/history comparison','diagnostic ranking','predictive model','original algorithm','OR/simulation escalation','counterfactual challenge','human decision'],
      'operator_action':f"{CONTRACT['actor']} reviews provenance, cohort shift, prediction, authored algorithm and OR escalation before approving or holding {CONTRACT['decision']}.",
      'claim_boundary':'External-source results are claimed only when data_mode is refreshed_external or published_external_snapshot; offline_reference remains reference evidence.'
    }


# Public-data promotion wrapper: preserves legacy/reference evidence while surfacing the structured backbone.
_legacy_run_empirical_reference = run_empirical_reference
def run_empirical_reference():
    out=_legacy_run_empirical_reference()
    from empirical.public_data_backbone import data_backbone_status
    pub=data_backbone_status(); out['public_data_backbone']=pub
    state=pub.get('dataset_state')
    if state=='PUBLISHED_EXTERNAL_ACTIVE': out['data_mode']='published_external_snapshot'; out['empirical_promotion']='EXTERNAL_EVIDENCE_ACTIVE'
    elif state=='REFRESHED_EXTERNAL_ACTIVE': out['data_mode']='refreshed_external'; out['empirical_promotion']='EXTERNAL_EVIDENCE_ACTIVE'
    else: out['public_data_acquisition']='REQUIRED'
    out['claim_boundary']=pub.get('claim_boundary',out.get('claim_boundary'))
    return out
