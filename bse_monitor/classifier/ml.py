"""Optional statistical classifier layered on top of the rule engine.

The rule engine is the system of record. This module exists for the long tail:
filings that are phrased unusually enough to miss the lexicon. It is trained on
*rule-labelled* filings (weak supervision) — the rules generate labels for the
thousands of unambiguous filings, and the model learns to generalise from their
surrounding language to the ambiguous ones.

Blending is deliberately conservative: the model can only raise confidence in,
or override, a rule verdict when the rule engine was itself unsure. That keeps
an auditable lexicon match from being overturned by a probability.

scikit-learn and joblib are optional; without them ``MLClassifier.available``
is False and the pipeline runs rules-only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

try:  # pragma: no cover - exercised only when scikit-learn is installed
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover
    SKLEARN_AVAILABLE = False

try:  # pragma: no cover
    import joblib

    JOBLIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    JOBLIB_AVAILABLE = False


class MLClassifier:
    def __init__(self, model_path: Optional[str | Path] = None) -> None:
        self.model_path = Path(model_path) if model_path else None
        self.model: Any = None
        self.classes_: List[str] = []
        if self.model_path and self.model_path.exists():
            self.load()

    @property
    def available(self) -> bool:
        return SKLEARN_AVAILABLE and self.model is not None

    # -- training ----------------------------------------------------------
    @staticmethod
    def build_pipeline() -> Any:
        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn is required to train the ML classifier")
        return Pipeline(
            [
                (
                    "tfidf",
                    TfidfVectorizer(
                        # Word bigrams capture the phrases that carry the signal
                        # ("offer for sale", "lock in expiry") without the cost
                        # of a character-level model.
                        ngram_range=(1, 2),
                        min_df=2,
                        max_df=0.85,
                        sublinear_tf=True,
                        strip_accents="unicode",
                        lowercase=True,
                        max_features=60_000,
                    ),
                ),
                (
                    "clf",
                    LogisticRegression(
                        max_iter=2000,
                        C=4.0,
                        # Filing categories are heavily imbalanced — routine
                        # disclosures swamp OFS announcements.
                        class_weight="balanced",
                        n_jobs=None,
                    ),
                ),
            ]
        )

    def train(self, texts: Sequence[str], labels: Sequence[str]) -> Dict[str, Any]:
        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn is required to train the ML classifier")
        if len(texts) != len(labels):
            raise ValueError("texts and labels must be the same length")
        distinct = sorted(set(labels))
        if len(distinct) < 2:
            raise ValueError("need at least two distinct labels to train")

        self.model = self.build_pipeline()
        self.model.fit(list(texts), list(labels))
        self.classes_ = list(self.model.named_steps["clf"].classes_)
        stats = {"samples": len(texts), "classes": self.classes_}
        log.info("ML classifier trained", extra=stats)
        return stats

    def save(self, path: Optional[str | Path] = None) -> Path:
        if not JOBLIB_AVAILABLE:
            raise RuntimeError("joblib is required to persist the model")
        target = Path(path or self.model_path or "./data/models/filing_clf.joblib")
        target.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self.model, "classes": self.classes_}, target)
        self.model_path = target
        return target

    def load(self, path: Optional[str | Path] = None) -> bool:
        target = Path(path or self.model_path or "")
        if not (JOBLIB_AVAILABLE and target and target.exists()):
            return False
        try:
            payload = joblib.load(target)
            self.model = payload["model"]
            self.classes_ = payload.get("classes", [])
            log.info("ML classifier loaded", extra={"path": str(target)})
            return True
        except Exception as exc:  # pragma: no cover - corrupt artefact
            log.error("Failed to load ML model", extra={"error": str(exc)})
            self.model = None
            return False

    # -- inference ---------------------------------------------------------
    def predict(self, text: str) -> Optional[Tuple[str, float]]:
        if not self.available or not text:
            return None
        try:
            probabilities = self.model.predict_proba([text])[0]
        except Exception as exc:  # pragma: no cover
            log.warning("ML prediction failed", extra={"error": str(exc)})
            return None
        best = int(max(range(len(probabilities)), key=lambda i: probabilities[i]))
        return str(self.model.classes_[best]), float(probabilities[best])


def blend(
    rule_category: str,
    rule_confidence: float,
    ml_prediction: Optional[Tuple[str, float]],
    rule_floor: float = 0.5,
    ml_override_threshold: float = 0.75,
    *,
    model_label: str = "ml",
) -> Tuple[str, float, str]:
    """Combine rule and model verdicts.

    Returns ``(category, confidence, method)``. ``model_label`` names the second
    opinion in the returned method string, so the same arbitration serves both
    the local ML model and the LLM without a second implementation.

    * The rules win outright whenever they were confident (``>= rule_floor``);
      at most the model nudges confidence up when it agrees.
    * The model may override only a low-confidence or ``Other`` rule verdict,
      and only when it is itself confident.
    """
    if ml_prediction is None:
        return rule_category, rule_confidence, "rules"

    ml_category, ml_confidence = ml_prediction

    if rule_confidence >= rule_floor:
        if ml_category == rule_category:
            return (
                rule_category,
                min(1.0, round((rule_confidence + ml_confidence) / 2 + 0.1, 4)),
                f"rules+{model_label}",
            )
        return rule_category, rule_confidence, "rules"

    if ml_confidence >= ml_override_threshold and ml_category != rule_category:
        return ml_category, round(ml_confidence, 4), model_label

    if ml_category == rule_category:
        return (
            rule_category,
            round(max(rule_confidence, ml_confidence * 0.8), 4),
            f"rules+{model_label}",
        )

    return rule_category, rule_confidence, "rules"
