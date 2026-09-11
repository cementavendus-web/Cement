"""LightGBM training, walk-forward evaluation and SHAP explanation.

LightGBM and SHAP are optional imports, in keeping with the rest of the package:
without them the system still runs and the baseline still ranks.

Two design points worth stating:

* ``feature_set_version`` is saved with every artefact and checked at load, so a
  model can never be scored with a feature set it was not trained on. That is a
  silent-wrong-answer failure mode, not a crash.
* SHAP values are turned into sentences through a phrasebook rather than shown
  raw. "days_to_nearest_trigger = 12 (+0.31)" is not a reason a human can act
  on; "anchor lock-in expires in 12 days" is.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .baseline import baseline_scores
from .evaluate import DEFAULT_EMBARGO_DAYS, DEFAULT_TOP_K, FoldResult, evaluate_fold, fold_windows
from .features import FEATURE_NAMES, FEATURE_SET_VERSION

log = logging.getLogger(__name__)

try:  # pragma: no cover - optional
    import lightgbm as lgb

    LIGHTGBM_AVAILABLE = True
except ImportError:  # pragma: no cover
    LIGHTGBM_AVAILABLE = False

try:  # pragma: no cover - optional
    import shap

    SHAP_AVAILABLE = True
except ImportError:  # pragma: no cover
    SHAP_AVAILABLE = False

try:  # pragma: no cover - optional
    import joblib

    JOBLIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    JOBLIB_AVAILABLE = False

DEFAULT_PARAMS: Dict[str, Any] = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 40,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
}

# SHAP feature -> readable reason. The second element renders the value.
PHRASEBOOK: Dict[str, Any] = {
    "days_to_nearest_trigger": lambda v: (
        f"trigger in {int(v)}d" if v is not None else "no upcoming trigger"
    ),
    "triggers_within_30d": lambda v: f"{int(v)} trigger(s) inside 30d",
    "triggers_within_60d": lambda v: f"{int(v)} trigger(s) inside 60d",
    "triggers_within_90d": lambda v: f"{int(v)} trigger(s) inside 90d",
    "nearest_trigger_is_anchor": lambda v: "nearest trigger is an anchor lock-in",
    "nearest_trigger_is_promoter": lambda v: "nearest trigger is a promoter lock-in",
    "days_since_ipo": lambda v: f"listed {int(v)}d ago",
    "holder_prior_sells_here": lambda v: f"{int(v)} prior sale(s) in this name",
    "holder_prior_sells_anywhere": lambda v: f"{int(v)} prior sale(s) across the market",
    "days_since_holder_last_sell": lambda v: f"last sold {int(v)}d ago",
    "holder_weeks_observed": lambda v: f"held for ~{int(v)} weeks",
    "holder_sell_rate": lambda v: f"historic sell rate {v:.2f}/wk",
    "last_stake_pct": lambda v: f"last disclosed stake {v:.2f}%",
    "stake_value_cr": lambda v: f"stake ~Rs {v:,.0f} Cr",
    "days_of_adv_to_liquidate": lambda v: f"{v:.1f} days of ADV to exit",
    "filings_4w": lambda v: f"{int(v)} filing(s) in 4 weeks",
    "filings_12w": lambda v: f"{int(v)} filing(s) in 12 weeks",
    "max_score_12w": lambda v: f"peak filing score {int(v)}",
    "sell_filings_12w": lambda v: f"{int(v)} sell-side filing(s) in 12 weeks",
    "is_marquee": lambda v: "marquee institutional holder",
}


def describe(feature: str, value: Any) -> str:
    renderer = PHRASEBOOK.get(feature)
    if renderer is None:
        return f"{feature}={value}"
    try:
        return renderer(value)
    except (TypeError, ValueError):
        return feature.replace("_", " ")


@dataclasses.dataclass
class TrainedModel:
    booster: Any
    feature_names: Sequence[str]
    feature_set_version: str
    trained_at: dt.datetime
    params: Dict[str, Any]
    base_rate: float
    n_rows: int

    def predict(self, frame) -> List[float]:
        return [float(p) for p in self.booster.predict(frame[list(self.feature_names)])]


def _frame(rows: Sequence[Mapping[str, Any]]):
    import pandas as pd

    frame = pd.DataFrame(list(rows))
    for name in FEATURE_NAMES:
        if name not in frame.columns:
            frame[name] = None
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return frame


def train(
    rows: Sequence[Mapping[str, Any]],
    *,
    params: Optional[Mapping[str, Any]] = None,
    num_boost_round: int = 300,
) -> Optional[TrainedModel]:
    """Fit a binary classifier. Returns None when LightGBM is unavailable."""
    if not LIGHTGBM_AVAILABLE:
        log.warning("LightGBM not installed; baseline ranking only")
        return None
    if not rows:
        return None

    frame = _frame(rows)
    labels = frame["label"].astype(int)
    positives = int(labels.sum())
    if positives == 0 or positives == len(labels):
        log.warning("Training set has a single class; skipping", extra={"positives": positives})
        return None

    settings = {**DEFAULT_PARAMS, **(params or {})}
    # Sell events are rare; without this the model predicts "no" everywhere and
    # scores well on accuracy while being useless.
    settings["scale_pos_weight"] = (len(labels) - positives) / positives

    dataset = lgb.Dataset(frame[list(FEATURE_NAMES)], label=labels, free_raw_data=False)
    booster = lgb.train(settings, dataset, num_boost_round=num_boost_round)

    return TrainedModel(
        booster=booster,
        feature_names=FEATURE_NAMES,
        feature_set_version=FEATURE_SET_VERSION,
        trained_at=dt.datetime.now(dt.timezone.utc),
        params=settings,
        base_rate=positives / len(labels),
        n_rows=len(labels),
    )


def save_model(model: TrainedModel, path: str | Path) -> Path:
    if not JOBLIB_AVAILABLE:
        raise RuntimeError("joblib is required to persist the model")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "booster": model.booster,
            "feature_names": list(model.feature_names),
            "feature_set_version": model.feature_set_version,
            "trained_at": model.trained_at.isoformat(),
            "params": model.params,
            "base_rate": model.base_rate,
            "n_rows": model.n_rows,
        },
        target,
    )
    return target


def load_model(path: str | Path) -> Optional[TrainedModel]:
    """Load an artefact, refusing one trained on a different feature set."""
    target = Path(path)
    if not (JOBLIB_AVAILABLE and target.exists()):
        return None
    payload = joblib.load(target)
    stored = payload.get("feature_set_version")
    if stored != FEATURE_SET_VERSION:
        # Scoring across feature sets gives wrong answers rather than errors,
        # so this refuses rather than coercing.
        log.error(
            "Model feature set mismatch; refusing to load",
            extra={"stored": stored, "current": FEATURE_SET_VERSION},
        )
        return None
    return TrainedModel(
        booster=payload["booster"],
        feature_names=payload["feature_names"],
        feature_set_version=stored,
        trained_at=dt.datetime.fromisoformat(payload["trained_at"]),
        params=payload.get("params", {}),
        base_rate=payload.get("base_rate", 0.0),
        n_rows=payload.get("n_rows", 0),
    )


def walk_forward(
    rows: Sequence[Mapping[str, Any]],
    *,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
    k: int = DEFAULT_TOP_K,
    params: Optional[Mapping[str, Any]] = None,
) -> List[FoldResult]:
    """Train on each year's past, test on the year, report per fold."""
    years = sorted({row["year"] for row in rows})
    results: List[FoldResult] = []

    for fold in fold_windows(years, embargo_days):
        cutoff = fold["train_cutoff"]
        train_rows = [
            row for row in rows
            if row["year"] in fold["train_years"] and row["week_end"] <= cutoff
        ]
        test_rows = [row for row in rows if row["year"] == fold["test_year"]]
        if not train_rows or not test_rows:
            continue

        model = train(train_rows, params=params)
        if model is None:
            model_scores = baseline_scores(test_rows)
        else:
            model_scores = model.predict(_frame(test_rows))

        results.append(
            evaluate_fold(
                year=fold["test_year"],
                train_rows=train_rows,
                test_rows=test_rows,
                model_scores=model_scores,
                baseline_scores_=baseline_scores(test_rows),
                k=k,
            )
        )
    return results


def shap_reasons(
    model: Optional[TrainedModel],
    rows: Sequence[Mapping[str, Any]],
    top_n: int = 3,
) -> List[List[str]]:
    """Top-n contributing features per row, rendered as sentences.

    Falls back to the baseline's own reasons when SHAP or the model is absent,
    so the brief always has a "why" column.
    """
    from .baseline import baseline_reasons

    if model is None or not SHAP_AVAILABLE or not rows:
        return [baseline_reasons(row) for row in rows]

    try:
        frame = _frame(rows)[list(model.feature_names)]
        explainer = shap.TreeExplainer(model.booster)
        values = explainer.shap_values(frame)
        if isinstance(values, list):           # older SHAP returns per-class
            values = values[-1]
    except Exception as exc:  # pragma: no cover - explainer edge cases
        log.warning("SHAP failed; using baseline reasons", extra={"error": str(exc)})
        return [baseline_reasons(row) for row in rows]

    out: List[List[str]] = []
    for index, row in enumerate(rows):
        contributions = sorted(
            zip(model.feature_names, values[index]),
            key=lambda pair: -abs(pair[1]),
        )
        reasons: List[str] = []
        for feature, contribution in contributions:
            if len(reasons) >= top_n:
                break
            value = row.get(feature)
            if value is None or contribution == 0:
                continue
            direction = "+" if contribution > 0 else "-"
            reasons.append(f"{describe(feature, value)} ({direction}{abs(contribution):.2f})")
        out.append(reasons or baseline_reasons(row))
    return out
