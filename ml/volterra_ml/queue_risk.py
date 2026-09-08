"""Queue-risk surrogate model: a fast LightGBM approximation of simulation/'s own validated M/G/c
analytical queueing model, trained on ../data/queue_risk_training.csv
(../../simulation/examples/generate_queue_risk_dataset.jl).

**What this model's output is, and isn't.** Every prediction here is `modeled`, full stop — see
../README.md's "Non-negotiable" section and ../../docs/data-sources.md. It is NOT a demand
forecast: the training labels are themselves outputs of `simulation/`'s already-validated
Erlang-C + Allen-Cunneen queueing model (see the generator script's docstring), so this module
learns to approximate a known deterministic model quickly, not to predict real-world demand Tesla
has never published. Four separate target models are trained — mean_wait_min, p_wait,
mean_queue_length, p_wait_over_15min — and reported separately, never blended into one score, per
this project's cross-cutting discipline (see the root CLAUDE.md).

**Why a surrogate at all**: `simulation/`'s Monte Carlo service-time sampling plus the Erlang-C
recursion is fast for one query but requires a Julia process; a trained LightGBM model answers the
same question in microseconds from four numeric inputs (stall_count, max_power_kw, temp_c,
utilization), which is what a future interactive frontend/copilot "what if we build a 20-stall
site here" query needs — see ../../docs/roadmap.md Phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import polars as pl
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "ml" / "data"
TRAINING_CSV = DATA_DIR / "queue_risk_training.csv"
HOLDOUT_CSV = DATA_DIR / "real_sites_holdout.csv"
MODEL_DIR = DATA_DIR / "models"

FEATURE_COLUMNS = ["stall_count", "max_power_kw", "temp_c", "utilization"]
TARGET_COLUMNS = ["mean_wait_min", "p_wait", "mean_queue_length", "p_wait_over_15min"]

# Physical bounds per target, applied only at prediction time (see predict_queue_risk) — NOT
# during train_all_targets' evaluation, which must report the model's honest raw error rather than
# a clamped, artificially-flattering one. LightGBM regressors have no built-in output-range
# constraint, so near-zero true values (e.g. p_wait_over_15min at low utilization) can predict as
# a tiny negative number -- confirmed by reproduction (-5.6e-05 predicted for a real site at
# utilization=0.50) -- which is nonsensical for a probability or a count and must be clamped
# before this is ever shown to a user.
TARGET_BOUNDS: dict[str, tuple[float, float | None]] = {
    "mean_wait_min": (0.0, None),
    "p_wait": (0.0, 1.0),
    "mean_queue_length": (0.0, None),
    "p_wait_over_15min": (0.0, 1.0),
}


def _clamp(value: float, lo: float, hi: float | None) -> float:
    if value < lo:
        return lo
    if hi is not None and value > hi:
        return hi
    return value


@dataclass
class TargetEvaluation:
    """Held-out performance for one target — reported per-target, never averaged into one score."""

    target: str
    mae: float
    r2: float
    n_test: int


def load_training_data(path: Path = TRAINING_CSV) -> pl.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Generate it first, from simulation/:\n"
            "  julia --project=../optimization examples/generate_queue_risk_dataset.jl"
        )
    return pl.read_csv(path)


def train_all_targets(df: pl.DataFrame, *, test_size: float = 0.2, seed: int = 2026
                       ) -> tuple[dict[str, lgb.Booster], list[TargetEvaluation]]:
    """Train one LightGBM regressor per target column, held out via a random train/test split
    (an honest i.i.d. generalization estimate over the swept parameter space — see
    verify_real_sites.py for a check against the real-site-grounded holdout set instead).

    Uses `.to_numpy()`, not `.to_pandas()` — polars' pandas conversion requires pyarrow, an
    otherwise-unneeded dependency here since LightGBM and scikit-learn both accept plain numpy
    arrays directly (confirmed: `.to_pandas()` raised `ModuleNotFoundError` without pyarrow
    installed)."""
    X = df.select(FEATURE_COLUMNS).to_numpy()

    models: dict[str, lgb.Booster] = {}
    evaluations: list[TargetEvaluation] = []

    for target in TARGET_COLUMNS:
        y = df[target].to_numpy()
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=seed)

        train_set = lgb.Dataset(X_train, label=y_train, feature_name=FEATURE_COLUMNS)
        model = lgb.train(
            {"objective": "regression", "metric": "mae", "verbosity": -1, "seed": seed},
            train_set,
            num_boost_round=200,
        )

        preds = model.predict(X_test)
        evaluations.append(TargetEvaluation(
            target=target,
            mae=mean_absolute_error(y_test, preds),
            r2=r2_score(y_test, preds),
            n_test=len(y_test),
        ))
        models[target] = model

    return models, evaluations


def save_models(models: dict[str, lgb.Booster], out_dir: Path = MODEL_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for target, model in models.items():
        model.save_model(str(out_dir / f"{target}.txt"))


def load_models(model_dir: Path = MODEL_DIR) -> dict[str, lgb.Booster]:
    models = {}
    for target in TARGET_COLUMNS:
        path = model_dir / f"{target}.txt"
        if not path.exists():
            raise FileNotFoundError(f"{path} not found — train and save_models() first.")
        models[target] = lgb.Booster(model_file=str(path))
    return models


def predict_queue_risk(models: dict[str, lgb.Booster], *, stall_count: int, max_power_kw: float,
                        temp_c: float, utilization: float) -> dict[str, float]:
    """MODELED queue-risk estimate for one (site design, temperature, load level) query —
    microseconds, no Julia process needed. See module docstring for what this is/isn't.

    Predictions are clamped to each target's physical bounds (TARGET_BOUNDS) — raw LightGBM output
    can otherwise be a nonsensical tiny-negative probability or count near zero."""
    row = [[stall_count, max_power_kw, temp_c, utilization]]
    raw = {target: float(model.predict(row)[0]) for target, model in models.items()}
    return {target: _clamp(value, *TARGET_BOUNDS[target]) for target, value in raw.items()}
