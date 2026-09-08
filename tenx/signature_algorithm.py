"""GRIDWEAVE resilient topology reference contract."""


def build_gridweave_topology(demand, candidates, critical):
    if not demand or not candidates:
        raise ValueError("demand and candidates are required")
    remaining = set(range(len(demand)))
    pool = [dict(c) for c in candidates]
    opened = []
    while remaining and pool:
        choice = max(pool, key=lambda c: sum(demand[i] for i in remaining if i in c["covers"]) / max(float(c["capex"]), 1e-9))
        opened.append(choice["id"])
        remaining -= set(choice["covers"])
        pool = [c for c in pool if c["id"] != choice["id"]]
    for corridor in critical:
        coverers = [c["id"] for c in candidates if corridor in c["covers"]]
        while sum(corridor in c["covers"] for c in candidates if c["id"] in opened) < 2 and coverers:
            next_id = next((c for c in coverers if c not in opened), None)
            if next_id is None:
                break
            opened.append(next_id)
    return list(dict.fromkeys(opened))


def ablation(demand, candidates, critical):
    """Remove N-1 repair for a declared nominal coverage ablation."""
    return build_gridweave_topology(demand, candidates, [])


def sensitivity(demand, candidates, critical, demand_multiplier=1.0):
    return build_gridweave_topology([float(x) * demand_multiplier for x in demand], candidates, critical)
