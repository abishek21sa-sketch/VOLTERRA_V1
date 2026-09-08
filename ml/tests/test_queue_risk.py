"""Tests for volterra_ml.queue_risk. Deliberately self-contained — uses small synthetic
in-memory data rather than the real generated CSV, so these run fast and don't require Julia or
the warehouse to be present. Correctness of the actual queueing labels is simulation/'s
responsibility (see simulation/test/runtests.jl) — these tests check this module's own training/
evaluation/persistence plumbing.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from volterra_ml.queue_risk import (
    TARGET_BOUNDS,
    TARGET_COLUMNS,
    _clamp,
    load_models,
    load_training_data,
    predict_queue_risk,
    save_models,
    train_all_targets,
)


def _synthetic_training_df(n: int = 500, seed: int = 7) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    stall_count = rng.integers(4, 40, n)
    max_power_kw = rng.uniform(100, 300, n)
    temp_c = rng.uniform(-20, 45, n)
    utilization = rng.uniform(0.5, 0.95, n)
    # Synthetic but structured targets (real signal, not pure noise) so the model has something
    # genuine to fit -- these are NOT meant to resemble real queueing-theory shapes, just to give
    # train_all_targets() a deterministic-ish relationship to recover for testing purposes.
    mean_wait_min = utilization * 10.0 / stall_count + rng.normal(0, 0.05, n)
    p_wait = np.clip(utilization, 0, 1)
    mean_queue_length = utilization * stall_count * 0.1
    p_wait_over_15min = np.clip(p_wait - 0.3, 0, 1)
    return pl.DataFrame({
        "stall_count": stall_count, "max_power_kw": max_power_kw, "temp_c": temp_c,
        "utilization": utilization, "mean_wait_min": mean_wait_min, "p_wait": p_wait,
        "mean_queue_length": mean_queue_length, "p_wait_over_15min": p_wait_over_15min,
    })


def test_load_training_data_missing_file_raises_helpful_error(tmp_path):
    missing = tmp_path / "does_not_exist.csv"
    with pytest.raises(FileNotFoundError, match="generate_queue_risk_dataset"):
        load_training_data(missing)


def test_train_all_targets_returns_one_model_and_evaluation_per_target():
    df = _synthetic_training_df()
    models, evaluations = train_all_targets(df, test_size=0.2, seed=1)

    assert set(models.keys()) == set(TARGET_COLUMNS)
    assert {e.target for e in evaluations} == set(TARGET_COLUMNS)
    # mean_wait_min has strong structured signal in the synthetic data -- model should recover it
    mean_wait_eval = next(e for e in evaluations if e.target == "mean_wait_min")
    assert mean_wait_eval.r2 > 0.5


def test_predict_queue_risk_returns_all_targets_as_floats():
    df = _synthetic_training_df()
    models, _ = train_all_targets(df)

    result = predict_queue_risk(models, stall_count=12, max_power_kw=150.0, temp_c=5.0, utilization=0.7)

    assert set(result.keys()) == set(TARGET_COLUMNS)
    assert all(isinstance(v, float) for v in result.values())


def test_predict_queue_risk_clamps_to_physical_bounds():
    # Every real trained call should already respect its bounds, but confirm predict_queue_risk
    # actually applies TARGET_BOUNDS rather than trusting raw LightGBM output -- a tree regressor
    # has no inherent output-range constraint (see TARGET_BOUNDS' comment: a real trained model
    # predicted a tiny negative p_wait_over_15min near utilization=0.50 before this was added).
    df = _synthetic_training_df()
    models, _ = train_all_targets(df)

    result = predict_queue_risk(models, stall_count=12, max_power_kw=150.0, temp_c=5.0, utilization=0.01)

    for target, value in result.items():
        lo, hi = TARGET_BOUNDS[target]
        assert value >= lo
        if hi is not None:
            assert value <= hi


@pytest.mark.parametrize("value,lo,hi,expected", [
    (-0.001, 0.0, 1.0, 0.0),
    (1.5, 0.0, 1.0, 1.0),
    (0.5, 0.0, 1.0, 0.5),
    (-3.0, 0.0, None, 0.0),
    (100.0, 0.0, None, 100.0),
])
def test_clamp(value, lo, hi, expected):
    assert _clamp(value, lo, hi) == expected


def test_save_and_load_models_round_trip(tmp_path):
    df = _synthetic_training_df()
    models, _ = train_all_targets(df)
    save_models(models, out_dir=tmp_path)

    reloaded = load_models(model_dir=tmp_path)

    query = dict(stall_count=10, max_power_kw=120.0, temp_c=0.0, utilization=0.8)
    original_pred = predict_queue_risk(models, **query)
    reloaded_pred = predict_queue_risk(reloaded, **query)
    for target in TARGET_COLUMNS:
        assert original_pred[target] == pytest.approx(reloaded_pred[target])


def test_load_models_missing_model_raises_helpful_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="save_models"):
        load_models(model_dir=tmp_path)
