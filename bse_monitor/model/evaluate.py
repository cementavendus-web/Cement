"""Walk-forward evaluation.

Three metrics, each answering a different question:

* **precision@20/week** — of the twenty names the desk would actually look at in
  a given week, how many sold? This is the number that decides whether the
  system is usable, and it is far more informative than a global AUC because
  nobody reads row 4,000.
* **median lead time** — how many days of warning did a true positive give? A
  model that fires the day before a block is accurate and useless.
* **PR-AUC vs the baseline** — ranking quality over the whole sheet, always
  quoted next to the baseline and the base rate so the number means something.

Folds are walk-forward by year with a **60-day embargo** matching the label
horizon: the last weeks of a training year have labels that resolve inside the
test year, so training on them leaks the test period's outcomes.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import statistics
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

DEFAULT_EMBARGO_DAYS = 60
DEFAULT_TOP_K = 20


@dataclasses.dataclass
class FoldResult:
    year: int
    train_rows: int
    test_rows: int
    test_positives: int
    base_rate: float
    precision_at_k: float
    baseline_precision_at_k: float
    median_lead_time_days: Optional[float]
    pr_auc: float
    baseline_pr_auc: float
    weeks_evaluated: int

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @property
    def lift(self) -> float:
        """Precision@K relative to the base rate. 1.0 means no skill."""
        return round(self.precision_at_k / self.base_rate, 2) if self.base_rate else 0.0


def precision_at_k_per_week(
    rows: Sequence[Dict[str, Any]], scores: Sequence[float], k: int = DEFAULT_TOP_K
) -> tuple[float, int]:
    """Mean precision@k, computed per week then averaged.

    Pooling across weeks would let one week with many positives carry the score;
    the desk reads a fresh top-20 every week, so the metric has to be per week.
    """
    by_week: Dict[dt.date, List[tuple[float, int]]] = {}
    for row, score in zip(rows, scores):
        by_week.setdefault(row["week_end"], []).append((score, int(row.get("label") or 0)))

    precisions: List[float] = []
    for entries in by_week.values():
        entries.sort(key=lambda pair: -pair[0])
        top = entries[:k]
        if top:
            precisions.append(sum(label for _score, label in top) / len(top))
    return (statistics.fmean(precisions) if precisions else 0.0), len(precisions)


def median_lead_time(
    rows: Sequence[Dict[str, Any]], scores: Sequence[float], k: int = DEFAULT_TOP_K
) -> Optional[float]:
    """Median days of warning across true positives in the weekly top-k."""
    by_week: Dict[dt.date, List[tuple[float, Dict[str, Any]]]] = {}
    for row, score in zip(rows, scores):
        by_week.setdefault(row["week_end"], []).append((score, row))

    leads: List[float] = []
    for entries in by_week.values():
        entries.sort(key=lambda pair: -pair[0])
        for _score, row in entries[:k]:
            if row.get("label") and row.get("lead_time_days") is not None:
                leads.append(float(row["lead_time_days"]))
    return round(statistics.median(leads), 1) if leads else None


def pr_auc(labels: Sequence[int], scores: Sequence[float]) -> float:
    """Average precision. Falls back to a manual computation without sklearn."""
    if not labels or not any(labels):
        return 0.0
    try:
        from sklearn.metrics import average_precision_score

        return round(float(average_precision_score(list(labels), list(scores))), 4)
    except ImportError:  # pragma: no cover - sklearn is a declared dependency
        pass

    ordered = sorted(zip(scores, labels), key=lambda pair: -pair[0])
    positives = sum(labels)
    hits = 0
    total = 0.0
    for index, (_score, label) in enumerate(ordered, start=1):
        if label:
            hits += 1
            total += hits / index
    return round(total / positives, 4) if positives else 0.0


def fold_windows(
    years: Sequence[int], embargo_days: int = DEFAULT_EMBARGO_DAYS
) -> List[Dict[str, Any]]:
    """Walk-forward folds: train on everything before the test year, minus the embargo."""
    ordered = sorted(set(years))
    folds: List[Dict[str, Any]] = []
    for test_year in ordered[1:]:
        train_cutoff = dt.date(test_year, 1, 1) - dt.timedelta(days=embargo_days)
        folds.append(
            {
                "test_year": test_year,
                "train_years": [y for y in ordered if y < test_year],
                "train_cutoff": train_cutoff,
            }
        )
    return folds


def evaluate_fold(
    *,
    year: int,
    train_rows: Sequence[Dict[str, Any]],
    test_rows: Sequence[Dict[str, Any]],
    model_scores: Sequence[float],
    baseline_scores_: Sequence[float],
    k: int = DEFAULT_TOP_K,
) -> FoldResult:
    labels = [int(row.get("label") or 0) for row in test_rows]
    positives = sum(labels)
    precision, weeks = precision_at_k_per_week(test_rows, model_scores, k)
    baseline_precision, _ = precision_at_k_per_week(test_rows, baseline_scores_, k)

    return FoldResult(
        year=year,
        train_rows=len(train_rows),
        test_rows=len(test_rows),
        test_positives=positives,
        base_rate=round(positives / len(test_rows), 6) if test_rows else 0.0,
        precision_at_k=round(precision, 4),
        baseline_precision_at_k=round(baseline_precision, 4),
        median_lead_time_days=median_lead_time(test_rows, model_scores, k),
        pr_auc=pr_auc(labels, model_scores),
        baseline_pr_auc=pr_auc(labels, baseline_scores_),
        weeks_evaluated=weeks,
    )


def format_report(folds: Sequence[FoldResult], k: int = DEFAULT_TOP_K) -> str:
    """Markdown table, per year, so degradation is visible rather than averaged away."""
    if not folds:
        return "_No folds evaluated._"

    header = (
        f"| Year | Test rows | Positives | Base rate | "
        f"P@{k} model | P@{k} baseline | Lift | Median lead (d) | PR-AUC model | PR-AUC baseline |"
    )
    divider = "|" + "|".join("---" for _ in range(10)) + "|"
    lines = [header, divider]
    for fold in folds:
        lines.append(
            f"| {fold.year} | {fold.test_rows:,} | {fold.test_positives:,} | "
            f"{fold.base_rate:.3%} | {fold.precision_at_k:.3f} | "
            f"{fold.baseline_precision_at_k:.3f} | {fold.lift}x | "
            f"{fold.median_lead_time_days if fold.median_lead_time_days is not None else '—'} | "
            f"{fold.pr_auc:.4f} | {fold.baseline_pr_auc:.4f} |"
        )

    beaten = sum(1 for f in folds if f.pr_auc > f.baseline_pr_auc)
    lines += [
        "",
        f"Model beats the baseline on PR-AUC in {beaten} of {len(folds)} folds.",
    ]
    return "\n".join(lines)
