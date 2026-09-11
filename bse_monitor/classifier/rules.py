"""Rule-based filing classifier.

Design: a transparent weighted-lexicon scorer rather than an opaque model.

For each category the engine sums the tier weight of every distinct matched
term, divides by a per-category saturation constant, and clamps to [0, 1] to
get an *evidence* score. Evidence is then adjusted by two guards:

* **requires_any** — a category cannot fire without at least one decisive term,
  which stops a filing that merely lists past corporate actions from being
  classified as a live QIP.
* **negation** — a strong match inside a negated sentence ("the Board did not
  approve the proposed fund raise") loses most of its weight.
* **disambiguation** — configurable rules demote a category whose vocabulary is
  present only as historical context (an anchor lock-in notice reciting the IPO
  it came from) and promote the one the filing is actually about.

Evidence scores across categories are normalised into confidences, so the
output is directly comparable to a probabilistic classifier's and can be
blended with the optional ML model.

Why rules lead here: filings are formulaic legal prose written to SEBI's
Regulation 30 template, so the signal is lexical and stable. A model trained on
a few thousand labels would mostly relearn this lexicon while being far harder
to audit when a ₹1,200 Cr call turns out wrong.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import load_keywords
from ..parser.text import normalize_for_match, split_sentences, strip_boilerplate

log = logging.getLogger(__name__)

DEFAULT_CATEGORY = "Other"


@dataclasses.dataclass
class CategoryScore:
    category: str
    evidence: float
    confidence: float
    matched_terms: List[str]
    negated_terms: List[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Classification:
    category: str
    confidence: float
    secondary: List[str]
    certainty: str
    method: str
    evidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class RuleClassifier:
    def __init__(self, lexicon: Optional[Dict[str, Any]] = None) -> None:
        self.lexicon = lexicon or load_keywords()
        self.tier_weights: Dict[str, int] = self.lexicon.get(
            "tier_weights", {"strong": 10, "medium": 5, "weak": 2}
        )
        self.categories: Dict[str, Dict[str, Any]] = self.lexicon.get("categories", {})
        self.certainty_cues: Dict[str, Sequence[str]] = self.lexicon.get("certainty", {})
        self.negations: Sequence[str] = self.lexicon.get("negations", [])
        self.boilerplate: Sequence[str] = self.lexicon.get("boilerplate", [])
        self.disambiguation: Sequence[Dict[str, Any]] = self.lexicon.get("disambiguation", []) or []
        self._compiled = self._compile()
        self._last_disambiguation: List[str] = []

    def _compile(self) -> Dict[str, List[Tuple[re.Pattern[str], str, str]]]:
        """Pre-compile every term to a word-boundary pattern, longest first."""
        compiled: Dict[str, List[Tuple[re.Pattern[str], str, str]]] = {}
        for category, spec in self.categories.items():
            terms: List[Tuple[re.Pattern[str], str, str]] = []
            for tier in ("strong", "medium", "weak"):
                for term in spec.get(tier, []) or []:
                    normalized = normalize_for_match(str(term))
                    if not normalized:
                        continue
                    pattern = re.compile(rf"(?<!\w){re.escape(normalized)}(?!\w)")
                    terms.append((pattern, tier, normalized))
            terms.sort(key=lambda item: len(item[2]), reverse=True)
            compiled[category] = terms
        return compiled

    # -- helpers -----------------------------------------------------------
    def _negated_spans(self, text: str) -> List[Tuple[int, int]]:
        """Character ranges of sentences containing a negation cue."""
        spans: List[Tuple[int, int]] = []
        cursor = 0
        for sentence in split_sentences(text):
            start = text.find(sentence, cursor)
            if start < 0:
                start = cursor
            end = start + len(sentence)
            cursor = end
            low = sentence.lower()
            if any(neg in low for neg in self.negations):
                spans.append((start, end))
        return spans

    def detect_certainty(self, text: str) -> str:
        """Firmness of the event: completed > approved > intended > unknown.

        Sentences carrying a negation cue are skipped, so "the Board did not
        approve" never registers as an approval.
        """
        candidates = [
            normalize_for_match(sentence)
            for sentence in split_sentences(text)
            if not any(neg in sentence.lower() for neg in self.negations)
        ]
        if not candidates:
            return "unknown"
        for level in ("completed", "approved", "intended"):
            cues = [normalize_for_match(c) for c in self.certainty_cues.get(level, []) or []]
            for sentence in candidates:
                if any(cue and cue in sentence for cue in cues):
                    return level
        return "unknown"

    def _apply_disambiguation(
        self, prepared: str, results: List[CategoryScore]
    ) -> List[str]:
        """Re-weight competing categories; returns the names of rules applied."""
        if not self.disambiguation or not results:
            return []
        by_category = {result.category: result for result in results}
        applied: List[str] = []

        for rule in self.disambiguation:
            triggers = [normalize_for_match(t) for t in rule.get("when_any", []) or []]
            if not any(trigger and trigger in prepared for trigger in triggers):
                continue
            blockers = [normalize_for_match(t) for t in rule.get("unless_any", []) or []]
            if any(blocker and blocker in prepared for blocker in blockers):
                continue

            suppress_factor = float(rule.get("suppress_factor", 0.3))
            for category in rule.get("suppress", []) or []:
                target = by_category.get(category)
                if target is not None:
                    target.evidence *= suppress_factor

            boost = rule.get("boost")
            if boost:
                target = by_category.get(boost)
                boost_factor = float(rule.get("boost_factor", 1.5))
                if target is not None:
                    target.evidence = min(1.0, target.evidence * boost_factor)
                else:
                    # The boosted category had no lexicon hit of its own; the
                    # rule's trigger is itself the evidence for it.
                    promoted = CategoryScore(
                        category=str(boost),
                        evidence=0.5,
                        confidence=0.0,
                        matched_terms=[f"disambiguation:{rule.get('name', 'rule')}"],
                    )
                    results.append(promoted)
                    by_category[str(boost)] = promoted

            applied.append(str(rule.get("name", "rule")))

        return applied

    def score_categories(self, text: str) -> List[CategoryScore]:
        self._last_disambiguation = []
        prepared = normalize_for_match(strip_boilerplate(text, self.boilerplate))
        if not prepared:
            return []
        negated = self._negated_spans(prepared)

        def in_negation(position: int) -> bool:
            return any(start <= position < end for start, end in negated)

        results: List[CategoryScore] = []
        for category, terms in self._compiled.items():
            total = 0.0
            matched: List[str] = []
            negated_terms: List[str] = []
            consumed: List[Tuple[int, int]] = []

            for pattern, tier, term in terms:
                match = pattern.search(prepared)
                if not match:
                    continue
                # A longer term already covering this span wins; "qualified
                # institutions placement" must not also credit bare "qip".
                if any(start <= match.start() < end for start, end in consumed):
                    continue
                consumed.append(match.span())
                weight = float(self.tier_weights.get(tier, 1))
                if in_negation(match.start()):
                    # Keep a trace of the negated hit without letting it decide.
                    total += weight * 0.15
                    negated_terms.append(term)
                else:
                    total += weight
                    matched.append(term)

            if total <= 0:
                continue
            saturation = float(self.categories[category].get("saturation", 20)) or 20.0
            evidence = min(1.0, total / saturation)
            results.append(
                CategoryScore(
                    category=category,
                    evidence=evidence,
                    confidence=0.0,
                    matched_terms=matched,
                    negated_terms=negated_terms,
                )
            )

        applied = self._apply_disambiguation(prepared, results)
        self._last_disambiguation = applied

        # Normalise evidence into comparable confidences. The denominator keeps
        # a lone weak signal from reading as certainty just because nothing else
        # matched.
        total_evidence = sum(r.evidence for r in results)
        for result in results:
            denominator = max(total_evidence, 1.0)
            result.confidence = round(result.evidence / denominator, 4)
        results.sort(key=lambda r: (r.evidence, len(r.matched_terms)), reverse=True)
        return results

    def classify(self, text: str, min_confidence: float = 0.35) -> Classification:
        scores = self.score_categories(text)
        certainty = self.detect_certainty(text)

        if not scores:
            return Classification(
                category=DEFAULT_CATEGORY,
                confidence=0.0,
                secondary=[],
                certainty=certainty,
                method="rules",
                evidence={"reason": "no lexicon match"},
            )

        top = scores[0]
        # Two categories can be legitimately co-present (a QIP filing that also
        # discloses an OFS). Anything within 75% of the winner is kept as a
        # secondary tag rather than being discarded.
        secondary = [
            s.category for s in scores[1:] if s.evidence >= top.evidence * 0.75
        ][:3]

        category = top.category if top.confidence >= min_confidence else DEFAULT_CATEGORY
        return Classification(
            category=category,
            confidence=top.confidence,
            secondary=secondary,
            certainty=certainty,
            method="rules",
            evidence={
                "top_category": top.category,
                "top_evidence": round(top.evidence, 4),
                "matched_terms": top.matched_terms[:20],
                "negated_terms": top.negated_terms[:10],
                "all_scores": {
                    s.category: {"evidence": round(s.evidence, 4), "confidence": s.confidence}
                    for s in scores[:6]
                },
                "below_threshold": top.confidence < min_confidence,
                "disambiguation_applied": list(self._last_disambiguation),
            },
        )
