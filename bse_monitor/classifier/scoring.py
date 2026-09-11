"""Opportunity scoring model.

Produces a 0-100 score that ranks filings by how actionable they are, together
with a full breakdown of how the number was reached. Every component is stored
on the filing (``score_breakdown``) so that any ranking decision can be
explained after the fact — which matters when the output drives a trading or
coverage decision.

    bonus     = certainty + size + marquee + promoter + large_stake + lock_in
    headroom  = 100 - base(category)
    score     = base + headroom * (bonus / max_possible_bonus)
                     - recency_decay - low_confidence_penalty
              → clamped to [0, 100]

Bonuses are projected into the headroom *above* the base rather than added to
it. Adding them raw would push every high-base category (QIP 90, promoter sale
95) straight to a clamped 100, collapsing exactly the distinctions the score
exists to make; projecting them keeps a ₹5,000 Cr marquee exit meaningfully
above a ₹60 Cr one while both stay inside their category's band.

The base scores come straight from the event-detection spec (promoter intends
to sell = 95, QIP = 90, board approves fund raise = 85, lock-in expiry = 75,
investor reducing stake = 90); modifiers separate a ₹5,000 Cr Blackstone exit
from a ₹20 Cr preferential allotment to a related party.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CRORE = 1e7

# Components that subtract rather than add; excluded from the bonus pool.
_PENALTY_COMPONENTS = {"recency_decay", "low_confidence"}

DEFAULT_BASE_SCORES: Dict[str, int] = {
    "PromoterSale": 95,
    "OFS": 92,
    "QIP": 90,
    "InvestorExit": 90,
    "BlockDeal": 88,
    "FundRaise": 85,
    "FPO": 82,
    "IPO": 80,
    "PreferentialAllotment": 80,
    "RightsIssue": 78,
    "Other": 20,
}


@dataclasses.dataclass
class ScoreInput:
    category: str
    confidence: float
    certainty: str = "unknown"
    amount_inr: Optional[float] = None
    percent_of_equity: Optional[float] = None
    marquee_investor: bool = False
    promoter_involved: bool = False
    lock_in_expiry_date: Optional[dt.date] = None
    filing_date: Optional[dt.date] = None
    as_of: Optional[dt.date] = None


@dataclasses.dataclass
class ScoreResult:
    score: int
    priority: str
    breakdown: Dict[str, Any]


class OpportunityScorer:
    def __init__(self, config: Optional[Dict[str, Any]] = None, alerts_config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self.base_scores = {**DEFAULT_BASE_SCORES, **(cfg.get("base_scores") or {})}
        self.certainty_modifiers = cfg.get("certainty_modifiers") or {
            "completed": 6,
            "approved": 8,
            "intended": 3,
            "unknown": 0,
        }
        self.amount_modifiers = cfg.get("amount_modifiers") or [
            {"min_cr": 5000, "bonus": 10},
            {"min_cr": 1000, "bonus": 7},
            {"min_cr": 250, "bonus": 4},
            {"min_cr": 50, "bonus": 2},
        ]
        self.marquee_bonus = float(cfg.get("marquee_investor_bonus", 5))
        self.promoter_bonus = float(cfg.get("promoter_involved_bonus", 5))
        self.large_stake_bonus = float(cfg.get("large_stake_bonus", 5))
        self.large_stake_threshold = float(cfg.get("large_stake_threshold_pct", 5.0))
        self.lock_in_bonus = float(cfg.get("lock_in_window_bonus", 4))
        self.lock_in_horizon = int(cfg.get("lock_in_horizon_days", 45))
        self.recency_decay = float(cfg.get("recency_decay_per_day", 1.5))
        self.recency_max = float(cfg.get("recency_max_penalty", 12))
        self.low_confidence_penalty = float(cfg.get("low_confidence_penalty", 10))
        self.min_confidence = float(cfg.get("min_confidence", 0.35))

        # Ceiling of all positive modifiers; the denominator that keeps the
        # projection into headroom bounded and configuration-driven.
        self.max_bonus = (
            max([float(v) for v in self.certainty_modifiers.values()] or [0.0])
            + max([float(t.get("bonus", 0)) for t in self.amount_modifiers] or [0.0])
            + self.marquee_bonus
            + self.promoter_bonus
            + self.large_stake_bonus
            + self.lock_in_bonus
        )

        alerts = alerts_config or {}
        self.high_min = int(alerts.get("high_priority_min_score", 85))
        self.medium_min = int(alerts.get("medium_priority_min_score", 65))
        self.always_high = set(alerts.get("always_high_categories") or [])

    # -- components --------------------------------------------------------
    def _amount_bonus(self, amount_inr: Optional[float]) -> tuple[float, Optional[float]]:
        if not amount_inr or amount_inr <= 0:
            return 0.0, None
        crore = amount_inr / CRORE
        for tier in sorted(self.amount_modifiers, key=lambda t: -float(t.get("min_cr", 0))):
            if crore >= float(tier.get("min_cr", 0)):
                return float(tier.get("bonus", 0)), crore
        return 0.0, crore

    def _recency_penalty(self, filing_date: Optional[dt.date], as_of: Optional[dt.date]) -> float:
        if not filing_date:
            return 0.0
        today = as_of or dt.date.today()
        age_days = (today - filing_date).days
        if age_days <= 0:
            return 0.0
        return min(self.recency_max, age_days * self.recency_decay)

    def _lock_in_bonus(
        self, expiry: Optional[dt.date], as_of: Optional[dt.date]
    ) -> tuple[float, Optional[int]]:
        if not expiry:
            return 0.0, None
        today = as_of or dt.date.today()
        days_out = (expiry - today).days
        if 0 <= days_out <= self.lock_in_horizon:
            return self.lock_in_bonus, days_out
        return 0.0, days_out

    def priority_for(self, score: int, category: str) -> str:
        if category in self.always_high and score >= self.medium_min:
            return "HIGH"
        if score >= self.high_min:
            return "HIGH"
        if score >= self.medium_min:
            return "MEDIUM"
        return "LOW"

    # -- entry point -------------------------------------------------------
    def score(self, payload: ScoreInput) -> ScoreResult:
        base = float(self.base_scores.get(payload.category, self.base_scores["Other"]))
        components: List[Dict[str, Any]] = [
            {"component": "base", "detail": payload.category, "value": base}
        ]

        certainty_bonus = float(self.certainty_modifiers.get(payload.certainty, 0))
        if certainty_bonus:
            components.append(
                {"component": "certainty", "detail": payload.certainty, "value": certainty_bonus}
            )

        amount_bonus, crore = self._amount_bonus(payload.amount_inr)
        if amount_bonus:
            components.append(
                {
                    "component": "deal_size",
                    "detail": f"~Rs {crore:,.0f} Cr" if crore else None,
                    "value": amount_bonus,
                }
            )

        if payload.marquee_investor:
            components.append(
                {"component": "marquee_investor", "detail": None, "value": self.marquee_bonus}
            )

        if payload.promoter_involved:
            components.append(
                {"component": "promoter_involved", "detail": None, "value": self.promoter_bonus}
            )

        if payload.percent_of_equity and payload.percent_of_equity >= self.large_stake_threshold:
            components.append(
                {
                    "component": "large_stake",
                    "detail": f"{payload.percent_of_equity:.2f}%",
                    "value": self.large_stake_bonus,
                }
            )

        lock_bonus, days_out = self._lock_in_bonus(payload.lock_in_expiry_date, payload.as_of)
        if lock_bonus:
            components.append(
                {"component": "lock_in_window", "detail": f"T-{days_out}d", "value": lock_bonus}
            )

        penalty = self._recency_penalty(payload.filing_date, payload.as_of)
        if penalty:
            components.append({"component": "recency_decay", "detail": None, "value": -penalty})

        if payload.confidence < self.min_confidence:
            components.append(
                {
                    "component": "low_confidence",
                    "detail": f"{payload.confidence:.2f}",
                    "value": -self.low_confidence_penalty,
                }
            )

        bonus = sum(
            float(item["value"])
            for item in components
            if item["component"] not in _PENALTY_COMPONENTS and item["component"] != "base"
        )
        penalties = sum(
            float(item["value"])
            for item in components
            if item["component"] in _PENALTY_COMPONENTS
        )

        headroom = max(0.0, 100.0 - base)
        scaled_bonus = headroom * (bonus / self.max_bonus) if self.max_bonus > 0 else 0.0
        raw = base + scaled_bonus + penalties  # penalties are already negative
        score = int(round(max(0.0, min(100.0, raw))))
        priority = self.priority_for(score, payload.category)

        return ScoreResult(
            score=score,
            priority=priority,
            breakdown={
                "components": components,
                "base": base,
                "bonus_raw": round(bonus, 2),
                "bonus_max": round(self.max_bonus, 2),
                "headroom": round(headroom, 2),
                "bonus_scaled": round(scaled_bonus, 2),
                "penalties": round(penalties, 2),
                "raw_total": round(raw, 2),
                "clamped": score,
                "priority": priority,
                "model_version": "score-v1",
            },
        )


def scorer_from_config(config: Any) -> OpportunityScorer:
    scoring_cfg = dict(config.section("scoring"))
    scoring_cfg.setdefault(
        "min_confidence", config.get("classifier.min_confidence", 0.35)
    )
    return OpportunityScorer(scoring_cfg, config.section("alerts"))
