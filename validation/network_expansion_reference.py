"""Independent SciPy/HiGHS reference for VOLTERRA's greenfield network expansion MILP.

The reference is intentionally small and solver-agnostic relative to the Julia/JuMP
production model. It exists to validate formulation invariants in environments where
Julia is not installed; it is not a replacement for the native Julia acceptance gate.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


@dataclass(frozen=True)
class Site:
    site_id: str
    fixed_cost: float
    stall_cost: float
    power_kw: float
    max_stalls: int
    grid_kw: tuple[float, ...]


@dataclass(frozen=True)
class Corridor:
    segment_id: str
    demand: tuple[float, ...]
    critical: bool = False


def solve_reference(
    sites: list[Site], corridors: list[Corridor], coverage: Mapping[tuple[str, str], bool],
    budgets: tuple[float, ...], *, sessions_per_stall: float = 900.0,
    unserved_penalty: float = 80.0, discount_rate: float = 0.08,
    n_minus_one_critical: bool = False,
) -> dict:
    T=len(budgets); S=len(sites); C=len(corridors)
    if not T or any(len(s.grid_kw)!=T for s in sites) or any(len(c.demand)!=T for c in corridors):
        raise ValueError('period dimensions must agree')
    # variable blocks: open[S,T] binary, add[S,T] integer, assign[C,S,T] binary, unserved[C,T] binary
    n_open=S*T; n_add=S*T; n_assign=C*S*T; n_unserved=C*T
    critical=[ci for ci,c in enumerate(corridors) if c.critical]
    n_backup=(len(critical)*S*S) if n_minus_one_critical else 0
    off_open=0; off_add=n_open; off_assign=off_add+n_add; off_unserved=off_assign+n_assign; off_backup=off_unserved+n_unserved; n=off_backup+n_backup
    def io(s,t): return off_open+s*T+t
    def ia(s,t): return off_add+s*T+t
    def ix(c,s,t): return off_assign+(c*S+s)*T+t
    def iu(c,t): return off_unserved+c*T+t
    def ib(k,s,h): return off_backup+(k*S+s)*S+h

    cvec=np.zeros(n)
    for s,site in enumerate(sites):
        for t in range(T):
            d=1/(1+discount_rate)**t
            cvec[io(s,t)]=d*site.fixed_cost; cvec[ia(s,t)]=d*site.stall_cost
    for ci,corr in enumerate(corridors):
        for t in range(T): cvec[iu(ci,t)]=(1/(1+discount_rate)**t)*unserved_penalty*corr.demand[t]

    integrality=np.ones(n,dtype=int)
    lo=np.zeros(n); hi=np.ones(n)
    for s,site in enumerate(sites):
        for t in range(T): hi[ia(s,t)]=site.max_stalls
    rows=[]; lbs=[]; ubs=[]
    def addrow(entries, lb=-np.inf, ub=np.inf):
        r=np.zeros(n)
        for idx,val in entries.items(): r[idx]+=val
        rows.append(r); lbs.append(lb); ubs.append(ub)

    for s,site in enumerate(sites):
        addrow({io(s,t):1 for t in range(T)}, ub=1)
        for t in range(T):
            entries={ia(s,k):1 for k in range(t+1)}
            for k in range(t+1): entries[io(s,k)]=entries.get(io(s,k),0)-site.max_stalls
            addrow(entries, ub=0)
            entries={ia(s,k):site.power_kw for k in range(t+1)}
            addrow(entries, ub=site.grid_kw[t])
    for ci,corr in enumerate(corridors):
        for t in range(T):
            eligible=[s for s,site in enumerate(sites) if coverage.get((corr.segment_id,site.site_id),False)]
            entries={iu(ci,t):1}
            for s in eligible: entries[ix(ci,s,t)]=1
            addrow(entries, lb=1, ub=1)
            for s in range(S):
                if s in eligible:
                    entries={ix(ci,s,t):1}
                    for k in range(t+1): entries[io(s,k)]=entries.get(io(s,k),0)-1
                    addrow(entries, ub=0)
                else: addrow({ix(ci,s,t):1}, lb=0, ub=0)
    for s in range(S):
        for t in range(T):
            entries={}
            for ci,corr in enumerate(corridors): entries[ix(ci,s,t)]=corr.demand[t]
            for k in range(t+1): entries[ia(s,k)]=entries.get(ia(s,k),0)-sessions_per_stall
            addrow(entries, ub=0)
    for t,budget in enumerate(budgets):
        entries={}
        for s,site in enumerate(sites):
            entries[io(s,t)]=site.fixed_cost; entries[ia(s,t)]=site.stall_cost
        addrow(entries, ub=budget)
    for ci,corr in enumerate(corridors):
        if corr.critical: addrow({iu(ci,T-1):1}, lb=0, ub=0)

    # N-1 critical-corridor resilience: at the final horizon, every critical
    # corridor must remain assignable after loss of any single candidate site,
    # and surviving sites must have enough installed service capacity jointly.
    if n_minus_one_critical:
        for k,ci in enumerate(critical):
            corr=corridors[ci]
            for h in range(S):
                eligible=[s for s,site in enumerate(sites) if s != h and coverage.get((corr.segment_id,site.site_id),False)]
                if not eligible:
                    # Explicitly infeasible contingency rather than silently dropping it.
                    addrow({}, lb=1, ub=1)
                    continue
                addrow({ib(k,s,h):1 for s in eligible}, lb=1, ub=1)
                for s in range(S):
                    if s in eligible:
                        entries={ib(k,s,h):1}
                        for q in range(T): entries[io(s,q)]=entries.get(io(s,q),0)-1
                        addrow(entries, ub=0)
                    else:
                        addrow({ib(k,s,h):1}, lb=0, ub=0)
        for h in range(S):
            for s in range(S):
                entries={}
                for k,ci in enumerate(critical):
                    entries[ib(k,s,h)]=corridors[ci].demand[T-1]
                for q in range(T): entries[ia(s,q)]=entries.get(ia(s,q),0)-sessions_per_stall
                addrow(entries, ub=0)

    res=milp(cvec, integrality=integrality, bounds=Bounds(lo,hi), constraints=LinearConstraint(np.vstack(rows),np.asarray(lbs),np.asarray(ubs)), options={'presolve':True})
    if not res.success or res.x is None: return {'status':'INFEASIBLE','message':res.message}
    opened={}
    stalls={}
    assignments={}
    for s,site in enumerate(sites):
        for t in range(T):
            if res.x[io(s,t)]>.5: opened[site.site_id]=t+1
        stalls[site.site_id]=[int(round(sum(res.x[ia(s,k)] for k in range(t+1)))) for t in range(T)]
    for ci,corr in enumerate(corridors):
        for t in range(T):
            for s,site in enumerate(sites):
                if res.x[ix(ci,s,t)]>.5: assignments[(corr.segment_id,t+1)]=site.site_id
    contingencies={}
    if n_minus_one_critical:
        for h,outage in enumerate(sites):
            for k,ci in enumerate(critical):
                for s,site in enumerate(sites):
                    if res.x[ib(k,s,h)]>.5:
                        contingencies[(corridors[ci].segment_id,outage.site_id)]=site.site_id
    return {'status':'OPTIMAL','objective':float(res.fun),'opened_period':opened,'stalls_by_period':stalls,'assignments':assignments,'n_minus_one_critical':bool(n_minus_one_critical),'contingency_assignments':contingencies}
