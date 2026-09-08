"""Checks the trained queue-risk surrogate (../volterra_ml/queue_risk.py) against
../data/real_sites_holdout.csv — ground-truth M/G/c output for the actual real (stall_count,
max_power_kw) of all 17 real Tesla sites, crossed with 3 modeled utilization levels and the real
coldest/warmest NOAA forecast temperatures on file plus a 20C baseline
(../../simulation/examples/generate_queue_risk_dataset.jl).

This is NOT the same check as train_queue_risk_model.py's train/test split: that's an i.i.d.
holdout from the same random sweep distribution the model trained on. This file asks a different,
more concrete question — does the surrogate, trained on a synthetic parameter sweep, actually
generalize to the specific real site configurations in VOLTERRA's real network? Same
cross-validation ethos as warehouse/verify/*.py — checking a result against an independent real
source rather than trusting the training metric alone.

Run from ml/, after train_queue_risk_model.py:
    python examples/verify_real_sites.py
"""

from __future__ import annotations

import polars as pl
from sklearn.metrics import mean_absolute_error, r2_score

from volterra_ml.queue_risk import FEATURE_COLUMNS, HOLDOUT_CSV, TARGET_COLUMNS, load_models, predict_queue_risk


def main() -> None:
    print("=" * 100)
    print("VOLTERRA Phase 6 — queue-risk surrogate vs. real-site holdout (independent of training)")
    print("=" * 100)

    if not HOLDOUT_CSV.exists():
        print(f"\n{HOLDOUT_CSV} not found. Generate it first, from simulation/:")
        print("  julia --project=../optimization examples/generate_queue_risk_dataset.jl")
        return

    holdout = pl.read_csv(HOLDOUT_CSV)
    print(f"\n{len(holdout)} real-site holdout rows loaded "
          f"({holdout['stall_count'].n_unique()} distinct real stall counts).")

    models = load_models()

    X = holdout.select(FEATURE_COLUMNS).to_numpy()
    print("\nPer-target performance on real site configurations (never used in training):")
    for target in TARGET_COLUMNS:
        preds = models[target].predict(X)
        actual = holdout[target].to_numpy()
        mae = mean_absolute_error(actual, preds)
        r2 = r2_score(actual, preds)
        print(f"  {target:20s}  MAE={mae:.4f}  R^2={r2:.4f}")

    print("\nSpot check — 3 distinct real sites at utilization=0.75, temp_c=20.0:")
    # unique(subset=["name"]): several real sites share an identical (stall_count, max_power_kw)
    # -- without deduplicating by name specifically, a plain .head(3) can silently print the same
    # underlying site repeatedly (confirmed by reproduction before `name` was added to this CSV).
    spot = (
        holdout.filter((pl.col("utilization") == 0.75) & (pl.col("temp_c") == 20.0))
        .unique(subset=["name"], keep="first")
        .head(3)
    )
    for row in spot.iter_rows(named=True):
        pred = predict_queue_risk(
            models, stall_count=row["stall_count"], max_power_kw=row["max_power_kw"],
            temp_c=row["temp_c"], utilization=row["utilization"],
        )
        print(f"\n  {row['name']}  (stalls={row['stall_count']} power_kw={row['max_power_kw']})")
        print(f"    mean_wait_min:      ground truth={row['mean_wait_min']:.2f}   surrogate={pred['mean_wait_min']:.2f}")
        print(f"    p_wait_over_15min:  ground truth={row['p_wait_over_15min']:.3f}   surrogate={pred['p_wait_over_15min']:.3f}")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    main()
