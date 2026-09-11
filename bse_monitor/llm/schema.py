"""The strict JSON contract for LLM event extraction.

Deliberately plain Python with no pydantic import at module level, so the whole
validation and reconciliation path is testable with ``anthropic`` absent.

The most important function here is :func:`coerce_event`. ``EventFiling.category``
carries ``CheckConstraint("category IN " + str(CATEGORIES))``, so an unvalidated
``event_class`` reaching ``upsert_filing`` fails the INSERT and takes the whole
filing down with it. Every model output is therefore clamped to the known
taxonomy before it goes anywhere near the database.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Dict, Mapping, Optional

from ..database.models import CATEGORIES

# Must match the DB CHECK constraint exactly — asserted in the tests.
EVENT_CLASSES = tuple(CATEGORIES)

HOLDER_TYPES = (
    "PROMOTER",
    "ANCHOR_INVESTOR",
    "PE_VC",
    "FII_FPI",
    "MUTUAL_FUND",
    "INSURANCE",
    "BANK",
    "RETAIL",
    "COMPANY",
    "OTHER",
    "UNKNOWN",
)

# Used by the Batch API path, which has no `messages.parse` equivalent and must
# send a raw JSON schema.
EVENT_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "event_class",
        "holder",
        "holder_type",
        "stake_pct",
        "effective_date",
        "confidence",
    ],
    "properties": {
        "event_class": {
            "type": "string",
            "enum": list(EVENT_CLASSES),
            "description": "The corporate action this filing announces.",
        },
        "holder": {
            "type": ["string", "null"],
            "description": "Name of the shareholder transacting, or null if none is named.",
        },
        "holder_type": {"type": "string", "enum": list(HOLDER_TYPES)},
        "stake_pct": {
            "type": ["number", "null"],
            "description": "Percentage of total equity involved, 0-100, or null.",
        },
        "effective_date": {
            "type": ["string", "null"],
            "description": "ISO-8601 date (YYYY-MM-DD) the event takes effect, or null.",
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


@dataclasses.dataclass
class LlmVerdict:
    event_class: str
    holder: Optional[str] = None
    holder_type: str = "UNKNOWN"
    stake_pct: Optional[float] = None
    effective_date: Optional[dt.date] = None
    confidence: float = 0.0
    # False when the model returned something outside the contract and we
    # clamped it. Kept so a degraded verdict is visible rather than silent.
    valid: bool = True
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_class": self.event_class,
            "holder": self.holder,
            "holder_type": self.holder_type,
            "stake_pct": self.stake_pct,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "confidence": self.confidence,
            "valid": self.valid,
            "reason": self.reason,
        }


def _coerce_date(raw: Any) -> Optional[dt.date]:
    if not raw:
        return None
    if isinstance(raw, dt.date):
        return raw
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _coerce_float(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def coerce_event(raw: Mapping[str, Any]) -> LlmVerdict:
    """Clamp a model response onto the contract.

    Never raises and never returns a value the database would reject.
    """
    problems: list[str] = []

    event_class = str(raw.get("event_class") or "").strip()
    if event_class not in EVENT_CLASSES:
        problems.append(f"unknown event_class {event_class!r}")
        event_class = "Other"

    holder_type = str(raw.get("holder_type") or "UNKNOWN").strip().upper()
    if holder_type not in HOLDER_TYPES:
        problems.append(f"unknown holder_type {holder_type!r}")
        holder_type = "UNKNOWN"

    stake = _coerce_float(raw.get("stake_pct"))
    if stake is not None and not (0 < stake <= 100):
        problems.append(f"stake_pct out of range: {stake}")
        stake = None

    effective = _coerce_date(raw.get("effective_date"))
    if raw.get("effective_date") and effective is None:
        problems.append(f"unparseable effective_date {raw.get('effective_date')!r}")

    confidence = _coerce_float(raw.get("confidence"))
    if confidence is None:
        problems.append("missing confidence")
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    holder = raw.get("holder")
    holder = str(holder).strip() if holder else None

    # A verdict we had to clamp is not evidence for anything.
    if problems:
        confidence = 0.0

    return LlmVerdict(
        event_class=event_class,
        holder=holder or None,
        holder_type=holder_type,
        stake_pct=stake,
        effective_date=effective,
        confidence=confidence,
        valid=not problems,
        reason="; ".join(problems) or None,
    )
