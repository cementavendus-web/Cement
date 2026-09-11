"""Arbitration between the rule engine and the LLM.

Reuses ``classifier.ml.blend`` rather than inventing a second policy: a
confident rule verdict wins outright, the model may only nudge confidence when
it agrees, and it may override only a low-confidence or ``Other`` verdict. That
policy was argued for once; having two of them would mean two places to get the
precision/recall trade wrong.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional, Tuple

from ..classifier.ml import blend
from ..parser.entities import ExtractionResult
from ..triggers.rules import AnchorSet
from .schema import LlmVerdict


def reconcile(
    rule_category: str,
    rule_confidence: float,
    verdict: Optional[LlmVerdict],
    *,
    rule_floor: float = 0.5,
    override_threshold: float = 0.75,
) -> Tuple[str, float, str]:
    """Return ``(category, confidence, method)``."""
    if verdict is None or not verdict.valid:
        return rule_category, rule_confidence, "rules"
    return blend(
        rule_category,
        rule_confidence,
        (verdict.event_class, verdict.confidence),
        rule_floor=rule_floor,
        ml_override_threshold=override_threshold,
        model_label="llm",
    )


def reconcile_three_way(
    rule_category: str,
    rule_confidence: float,
    ml_prediction: Optional[Tuple[str, float]],
    verdict: Optional[LlmVerdict],
    *,
    rule_floor: float = 0.5,
    override_threshold: float = 0.75,
) -> Tuple[str, float, str, Dict[str, Any]]:
    """Rules vs ML first, then that result vs the LLM.

    The ordering matters and is recorded in the evidence dict, so a surprising
    final category can be traced to the step that produced it.
    """
    after_ml, conf_ml, method_ml = blend(
        rule_category, rule_confidence, ml_prediction, rule_floor=rule_floor
    )
    final, conf, method_llm = reconcile(
        after_ml, conf_ml, verdict,
        rule_floor=rule_floor, override_threshold=override_threshold,
    )

    if method_llm == "rules" and method_ml != "rules":
        method = method_ml
    elif method_llm != "rules" and method_ml != "rules":
        method = "rules+ml+llm"
    else:
        method = method_llm

    evidence = {
        "rule": {"category": rule_category, "confidence": round(rule_confidence, 4)},
        "ml": {"prediction": list(ml_prediction) if ml_prediction else None,
               "after": after_ml, "method": method_ml},
        "llm": verdict.to_dict() if verdict else None,
        "final": {"category": final, "confidence": round(conf, 4), "method": method},
    }
    return final, conf, method[:32], evidence


def merge_verdict_into_extraction(
    extraction: ExtractionResult, verdict: Optional[LlmVerdict]
) -> ExtractionResult:
    """Fill gaps only — never overwrite a deterministic extraction.

    The regex extractors are exact where they fire; the model is probabilistic
    everywhere. Same fill-blanks-only discipline as ``_merge_table_signals`` and
    ``upsert_company``.
    """
    if verdict is None or not verdict.valid:
        return extraction
    if extraction.percent_of_equity is None and verdict.stake_pct is not None:
        extraction.percent_of_equity = verdict.stake_pct
    if extraction.lock_in_expiry_date is None and verdict.effective_date is not None:
        if verdict.event_class in {"InvestorExit", "PromoterSale", "BlockDeal", "OFS"}:
            extraction.lock_in_expiry_date = verdict.effective_date
    return extraction


def verdict_anchor(verdict: Optional[LlmVerdict], category: str) -> AnchorSet:
    """Turn an LLM effective date into a calendar anchor.

    This is where Layer 2 feeds Layer 1: an allotment date the regex cues missed
    still becomes a real deadline, carrying the model's confidence so
    LLM-derived rows rank below regex-derived ones.
    """
    anchors = AnchorSet(confidence=verdict.confidence if verdict else 0.0)
    if verdict is None or not verdict.valid or verdict.effective_date is None:
        return anchors
    effective: dt.date = verdict.effective_date
    if category in {"IPO", "FPO", "QIP", "PreferentialAllotment"}:
        anchors.allotment_date = effective
    elif category in {"RightsIssue", "FundRaise"}:
        anchors.resolution_date = effective
    return anchors
