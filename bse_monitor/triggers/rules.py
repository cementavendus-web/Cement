"""Derivation of forward deadlines from a classified filing.

Pure functions only: no session, no I/O, and no ``date.today()``. Everything
time-dependent is passed in, which is what makes the derived dates reproducible
and the tests deterministic.

Offsets are configuration, not constants. SEBI changes lock-in periods (the
promoter minimum-contribution lock-in moved from 3 years to 18 months in 2021,
and anchor lock-ins were split into 30/90-day tranches in 2022), so a hard-coded
period would be a latent correctness bug the day the regulation moves.

Rules only fire when their evidence is actually present. Inventing a capex
3-year deadline for every IPO would flood the calendar with dates that are not
real, which is worse than missing them.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .dates import add_days, add_hours, add_months, add_years

RULE_VERSION = "v1"

# Every offset is overridable from configs/config.yaml -> triggers.offsets.
DEFAULT_OFFSETS: Dict[str, int] = {
    "anchor_lockin_short_days": 30,
    "anchor_lockin_long_days": 90,
    "preipo_lockin_months": 6,
    "promoter_excess_lockin_months": 6,
    "promoter_mpc_lockin_months": 18,
    "capex_objects_short_months": 12,
    "capex_objects_long_months": 36,
    "mps_compliance_months": 36,
    "qip_resolution_validity_days": 365,
    "trading_window_reopen_hours": 48,
}


@dataclasses.dataclass(frozen=True)
class TriggerRule:
    trigger_type: str
    anchor_basis: str
    offset_key: str
    unit: str                      # "days" | "months" | "hours"
    subject_type: str
    label: str
    fallback_basis: Optional[str] = None
    # When set, the rule only fires if this gate returns True for the text.
    gate: Optional[str] = None


RULES: Tuple[TriggerRule, ...] = (
    TriggerRule("ANCHOR_LOCKIN_30D", "ALLOTMENT", "anchor_lockin_short_days", "days",
                "ANCHOR_INVESTOR", "Anchor lock-in (50% tranche)", fallback_basis="LISTING"),
    TriggerRule("ANCHOR_LOCKIN_90D", "ALLOTMENT", "anchor_lockin_long_days", "days",
                "ANCHOR_INVESTOR", "Anchor lock-in (balance)", fallback_basis="LISTING"),
    TriggerRule("PREIPO_LOCKIN_6M", "ALLOTMENT", "preipo_lockin_months", "months",
                "PREIPO_SHAREHOLDER", "Pre-IPO shareholder lock-in", fallback_basis="LISTING"),
    TriggerRule("PROMOTER_EXCESS_LOCKIN_6M", "ALLOTMENT", "promoter_excess_lockin_months",
                "months", "PROMOTER", "Promoter excess-holding lock-in", fallback_basis="LISTING"),
    TriggerRule("PROMOTER_MPC_LOCKIN_18M", "ALLOTMENT", "promoter_mpc_lockin_months",
                "months", "PROMOTER", "Promoter minimum-contribution lock-in",
                fallback_basis="LISTING"),
    TriggerRule("CAPEX_OBJECTS_1Y", "ALLOTMENT", "capex_objects_short_months", "months",
                "PROMOTER", "Promoter lock-in (capex objects)", fallback_basis="LISTING",
                gate="capex_objects"),
    TriggerRule("CAPEX_OBJECTS_3Y", "ALLOTMENT", "capex_objects_long_months", "months",
                "PROMOTER", "Promoter lock-in (capex objects, extended)",
                fallback_basis="LISTING", gate="capex_objects"),
    TriggerRule("MPS_COMPLIANCE_25PCT", "LISTING", "mps_compliance_months", "months",
                "COMPANY", "Minimum public shareholding 25%", gate="mps_shortfall"),
    TriggerRule("QIP_RESOLUTION_EXPIRY_365D", "RESOLUTION", "qip_resolution_validity_days",
                "days", "COMPANY", "QIP special resolution expiry"),
    TriggerRule("TRADING_WINDOW_REOPEN_48H", "RESULTS_DECLARED", "trading_window_reopen_hours",
                "hours", "COMPANY", "Trading window reopens"),
)
RULES_BY_TYPE: Dict[str, TriggerRule] = {rule.trigger_type: rule for rule in RULES}

# Which rules are even considered for a given classified category.
CATEGORY_RULES: Dict[str, Tuple[str, ...]] = {
    "IPO": (
        "ANCHOR_LOCKIN_30D", "ANCHOR_LOCKIN_90D", "PREIPO_LOCKIN_6M",
        "PROMOTER_EXCESS_LOCKIN_6M", "PROMOTER_MPC_LOCKIN_18M",
        "CAPEX_OBJECTS_1Y", "CAPEX_OBJECTS_3Y", "MPS_COMPLIANCE_25PCT",
    ),
    "FPO": ("MPS_COMPLIANCE_25PCT",),
    "QIP": ("QIP_RESOLUTION_EXPIRY_365D",),
    "PreferentialAllotment": ("PREIPO_LOCKIN_6M", "PROMOTER_EXCESS_LOCKIN_6M"),
    "RightsIssue": ("MPS_COMPLIANCE_25PCT",),
    "FundRaise": ("QIP_RESOLUTION_EXPIRY_365D",),
}

# The trading-window rule is category-independent: any filing that declares
# results reopens the window.
ALWAYS_CONSIDERED: Tuple[str, ...] = ("TRADING_WINDOW_REOPEN_48H",)


# --------------------------------------------------------------------------
# Evidence gates
# --------------------------------------------------------------------------
_CAPEX_CUES = (
    "capital expenditure",
    "capex",
    "objects of the issue",
    "setting up of",
    "expansion of the manufacturing",
    "purchase of plant and machinery",
)
_MPS_CUES = (
    "minimum public shareholding",
    "public shareholding",
    "rule 19a",
    "mps compliance",
    "25% public",
)
_RESULTS_CUES = (
    "financial results",
    "audited results",
    "unaudited results",
    "quarterly results",
    "results for the quarter",
    "declaration of results",
)

_GATES = {
    "capex_objects": _CAPEX_CUES,
    "mps_shortfall": _MPS_CUES,
    "results_declared": _RESULTS_CUES,
}


def _has_cue(text: str, cues: Sequence[str]) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in cues)


def has_capex_objects(text: str) -> bool:
    return _has_cue(text, _CAPEX_CUES)


def mentions_mps_shortfall(text: str) -> bool:
    return _has_cue(text, _MPS_CUES)


def mentions_results_declaration(text: str) -> bool:
    return _has_cue(text, _RESULTS_CUES)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
@dataclasses.dataclass
class Subject:
    """Whose shares a deadline releases."""

    name: str
    subject_type: str = "COMPANY"
    key: str = ""
    percent_of_equity: Optional[float] = None
    num_shares: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.key:
            self.key = _normalize_subject(self.name)


def _normalize_subject(raw: str) -> str:
    """Local, dependency-free mirror of repository.normalize_name().

    Kept local so this module stays importable without the database layer; the
    two are asserted equivalent in the tests.
    """
    if not raw:
        return ""
    text = re.sub(r"[^\w\s&]", " ", str(raw).lower())
    drop = {"limited", "ltd", "pvt", "private", "inc", "plc", "llp", "co", "company", "and", "&"}
    return " ".join(tok for tok in text.split() if tok and tok not in drop).strip()


@dataclasses.dataclass
class AnchorSet:
    """The dates a filing gives us to count from."""

    allotment_date: Optional[dt.date] = None
    listing_date: Optional[dt.date] = None
    resolution_date: Optional[dt.date] = None
    results_datetime: Optional[dt.datetime] = None
    filing_date: Optional[dt.date] = None
    confidence: float = 0.5

    def for_basis(self, basis: str) -> Optional[dt.date]:
        return {
            "ALLOTMENT": self.allotment_date,
            "LISTING": self.listing_date,
            "RESOLUTION": self.resolution_date,
            "RESULTS_DECLARED": self.results_datetime.date() if self.results_datetime else None,
            "FILING": self.filing_date,
        }.get(basis)

    def any_known(self) -> bool:
        return any(
            (self.allotment_date, self.listing_date, self.resolution_date,
             self.results_datetime, self.filing_date)
        )


@dataclasses.dataclass
class TriggerCandidate:
    trigger_type: str
    subject_key: str
    subject_name: Optional[str]
    subject_type: str
    anchor_date: dt.date
    anchor_basis: str
    trigger_date: dt.date
    offset_days: int
    rule_version: str
    confidence: float
    evidence: Dict[str, Any] = dataclasses.field(default_factory=dict)
    anchor_datetime: Optional[dt.datetime] = None
    trigger_datetime: Optional[dt.datetime] = None
    num_shares: Optional[float] = None
    percent_of_equity: Optional[float] = None
    amount_inr: Optional[float] = None
    notes: Optional[str] = None

    def dedupe_key(self, company_id: int) -> str:
        return dedupe_key(
            company_id, self.trigger_type, self.anchor_date, self.subject_key, self.rule_version
        )


def dedupe_key(
    company_id: int,
    trigger_type: str,
    anchor_date: dt.date,
    subject_key: str,
    rule_version: str,
) -> str:
    """Stable identity for a deadline, independent of any transaction row."""
    parts = [
        str(company_id),
        trigger_type,
        anchor_date.isoformat() if anchor_date else "",
        subject_key or "",
        rule_version,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------
def _apply_offset(
    anchor: dt.date, rule: TriggerRule, offsets: Mapping[str, int]
) -> Tuple[dt.date, int]:
    amount = int(offsets.get(rule.offset_key, DEFAULT_OFFSETS[rule.offset_key]))
    if rule.unit == "days":
        target = add_days(anchor, amount)
    elif rule.unit == "months":
        target = add_months(anchor, amount)
    elif rule.unit == "hours":
        target = add_days(anchor, 0)  # refined by the caller using the datetime
    else:  # pragma: no cover - guarded by the rule table
        raise ValueError(f"unknown unit {rule.unit!r}")
    return target, (target - anchor).days


def derive_triggers(
    *,
    category: str,
    text: str = "",
    anchors: Optional[AnchorSet] = None,
    subjects: Sequence[Subject] = (),
    offsets: Optional[Mapping[str, int]] = None,
    rule_version: str = RULE_VERSION,
    as_of: Optional[dt.date] = None,
) -> List[TriggerCandidate]:
    """Derive every deadline this filing implies.

    Returns an empty list when no anchor date is known — the calendar never
    invents a deadline from a filing that did not state one.
    """
    anchors = anchors or AnchorSet()
    effective = {**DEFAULT_OFFSETS, **(offsets or {})}
    if not anchors.any_known():
        return []

    considered = tuple(CATEGORY_RULES.get(category, ())) + ALWAYS_CONSIDERED
    out: List[TriggerCandidate] = []

    for trigger_type in considered:
        rule = RULES_BY_TYPE[trigger_type]

        if rule.gate and not _has_cue(text, _GATES[rule.gate]):
            continue

        basis = rule.anchor_basis
        anchor = anchors.for_basis(basis)
        if anchor is None and rule.fallback_basis:
            basis = rule.fallback_basis
            anchor = anchors.for_basis(basis)
        if anchor is None:
            continue

        if rule.unit == "hours":
            # The only wall-clock rule. Requires the results timestamp, and its
            # date component may legitimately roll into the next day.
            if not anchors.results_datetime or not mentions_results_declaration(text):
                continue
            hours = int(effective.get(rule.offset_key, DEFAULT_OFFSETS[rule.offset_key]))
            trigger_dt = add_hours(anchors.results_datetime, hours)
            candidate = TriggerCandidate(
                trigger_type=trigger_type,
                subject_key="",
                subject_name=None,
                subject_type=rule.subject_type,
                anchor_date=anchors.results_datetime.date(),
                anchor_basis="RESULTS_DECLARED",
                anchor_datetime=anchors.results_datetime,
                trigger_date=trigger_dt.date(),
                trigger_datetime=trigger_dt,
                offset_days=(trigger_dt.date() - anchors.results_datetime.date()).days,
                rule_version=rule_version,
                confidence=anchors.confidence,
                evidence={"rule": rule.label, "basis": "RESULTS_DECLARED", "hours": hours},
            )
            out.append(candidate)
            continue

        trigger_date, offset_days = _apply_offset(anchor, rule, effective)

        # Subject-scoped rules emit one row per matching holder; with no holders
        # identified they still emit a single company-wide row, because the
        # deadline exists whether or not we know who it releases.
        matching = [s for s in subjects if s.subject_type == rule.subject_type]
        targets: Sequence[Optional[Subject]] = matching or [None]

        for subject in targets:
            out.append(
                TriggerCandidate(
                    trigger_type=trigger_type,
                    subject_key=subject.key if subject else "",
                    subject_name=subject.name if subject else None,
                    subject_type=rule.subject_type,
                    anchor_date=anchor,
                    anchor_basis=basis,
                    trigger_date=trigger_date,
                    offset_days=offset_days,
                    rule_version=rule_version,
                    confidence=anchors.confidence,
                    num_shares=subject.num_shares if subject else None,
                    percent_of_equity=subject.percent_of_equity if subject else None,
                    evidence={
                        "rule": rule.label,
                        "basis": basis,
                        "offset_key": rule.offset_key,
                        "offset": effective.get(rule.offset_key),
                        "unit": rule.unit,
                        "fallback_used": basis != rule.anchor_basis,
                    },
                )
            )

    return out
