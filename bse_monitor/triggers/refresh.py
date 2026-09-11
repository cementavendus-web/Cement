"""Idempotent persistence of derived deadlines.

The contract is deliberately the opposite of ``replace_transactions()``:

* a deadline that still derives is **updated in place**, keeping its id and the
  alert history that points at it;
* a deadline that no longer derives is **cancelled, not deleted** — deleting it
  would orphan its alerts and cause the reminder to re-fire on the next refresh;
* a deadline re-derived under a newer ``rule_version`` **supersedes** the old row
  rather than silently replacing it, so a regulatory change leaves an audit trail.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import repository as repo
from ..database.models import EventFiling, UpcomingTrigger
from ..parser.entities import ExtractionResult
from .rules import (
    RULE_VERSION,
    AnchorSet,
    Subject,
    TriggerCandidate,
    derive_triggers,
)

log = logging.getLogger(__name__)


@dataclasses.dataclass
class RefreshStats:
    filings_scanned: int = 0
    created: int = 0
    updated: int = 0
    cancelled: int = 0
    superseded: int = 0
    skipped: int = 0
    errors: List[str] = dataclasses.field(default_factory=list)

    def merge(self, other: "RefreshStats") -> None:
        self.filings_scanned += other.filings_scanned
        self.created += other.created
        self.updated += other.updated
        self.cancelled += other.cancelled
        self.superseded += other.superseded
        self.skipped += other.skipped
        self.errors.extend(other.errors)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "filings_scanned": self.filings_scanned,
            "created": self.created,
            "updated": self.updated,
            "cancelled": self.cancelled,
            "superseded": self.superseded,
            "skipped": self.skipped,
            "errors": self.errors[:20],
        }


def anchors_from_filing(
    filing: EventFiling,
    extraction: Optional[ExtractionResult] = None,
    text: Optional[str] = None,
) -> AnchorSet:
    """Collect the dates a filing offers to count from.

    The results timestamp uses ``filing_datetime`` (or dissemination time), not
    ``filing_date``: the 48-hour trading-window rule is wall-clock and a
    date-only anchor would put the reopen at the wrong hour.
    """
    from ..parser.entities import (
        extract_allotment_date,
        extract_listing_date,
        extract_resolution_date,
    )

    body = text if text is not None else (filing.search_text or filing.headline or "")

    if extraction is not None:
        allotment = extraction.allotment_date
        listing = extraction.listing_date
        resolution = extraction.resolution_date
    else:
        allotment = extract_allotment_date(body)
        listing = extract_listing_date(body)
        resolution = extract_resolution_date(body)

    results_dt = filing.filing_datetime or filing.dissemination_datetime

    return AnchorSet(
        allotment_date=allotment,
        listing_date=listing,
        resolution_date=resolution,
        results_datetime=results_dt,
        filing_date=filing.filing_date,
        confidence=float(filing.classification_confidence or 0.5),
    )


def subjects_from_filing(session: Session, filing: EventFiling) -> List[Subject]:
    """Holders named in the filing, typed for subject-scoped rules."""
    subjects: List[Subject] = []
    for link in filing.investor_links or []:
        investor = getattr(link, "investor", None)
        if investor is None:
            continue
        kind = "ANCHOR_INVESTOR" if filing.category == "IPO" else "PREIPO_SHAREHOLDER"
        subjects.append(Subject(name=investor.name, subject_type=kind))
    for link in filing.promoter_links or []:
        promoter = getattr(link, "promoter", None)
        if promoter is not None:
            subjects.append(Subject(name=promoter.name, subject_type="PROMOTER"))
    return subjects


def _payload_for(
    candidate: TriggerCandidate, filing: EventFiling, company_id: int
) -> Dict[str, Any]:
    return {
        "dedupe_key": candidate.dedupe_key(company_id),
        "company_id": company_id,
        "source_filing_id": filing.id,
        "trigger_type": candidate.trigger_type,
        "subject_key": candidate.subject_key,
        "subject_name": candidate.subject_name,
        "subject_type": candidate.subject_type,
        "anchor_date": candidate.anchor_date,
        "anchor_basis": candidate.anchor_basis,
        "anchor_datetime": candidate.anchor_datetime,
        "trigger_date": candidate.trigger_date,
        "trigger_datetime": candidate.trigger_datetime,
        "offset_days": candidate.offset_days,
        "rule_version": candidate.rule_version,
        "confidence": candidate.confidence,
        "num_shares": candidate.num_shares,
        "percent_of_equity": candidate.percent_of_equity,
        "amount_inr": candidate.amount_inr,
        "evidence": candidate.evidence,
        "notes": candidate.notes,
        # The point-in-time guard: this deadline was not knowable before the
        # filing that revealed it. The ML panel filters on exactly this.
        "known_from_date": filing.filing_date,
        "status": "PENDING",
    }


def refresh_for_filing(
    session: Session,
    filing: EventFiling,
    *,
    extraction: Optional[ExtractionResult] = None,
    text: Optional[str] = None,
    offsets: Optional[Dict[str, int]] = None,
    rule_version: str = RULE_VERSION,
    as_of: Optional[dt.date] = None,
) -> RefreshStats:
    stats = RefreshStats(filings_scanned=1)
    if filing.company_id is None:
        stats.skipped += 1
        return stats

    body = text if text is not None else (filing.search_text or filing.headline or "")
    anchors = anchors_from_filing(filing, extraction, body)
    subjects = subjects_from_filing(session, filing)

    candidates = derive_triggers(
        category=filing.category,
        text=body,
        anchors=anchors,
        subjects=subjects,
        offsets=offsets,
        rule_version=rule_version,
        as_of=as_of,
    )
    if not candidates:
        # No early return: a filing that has been corrected and now implies no
        # deadline must still cancel the rows it previously produced, or they
        # live on forever and keep re-alerting.
        stats.skipped += 1
        stats.cancelled += repo.cancel_triggers(
            session, source_filing_id=filing.id, keep_keys=set()
        )
        return stats

    keep_keys: set[str] = set()
    for candidate in candidates:
        payload = _payload_for(candidate, filing, filing.company_id)
        keep_keys.add(payload["dedupe_key"])

        # An older rule_version for the same natural key is superseded, not
        # overwritten — the regulation changed, and both facts matter.
        stale = session.scalar(
            select(UpcomingTrigger).where(
                UpcomingTrigger.company_id == filing.company_id,
                UpcomingTrigger.trigger_type == candidate.trigger_type,
                UpcomingTrigger.anchor_date == candidate.anchor_date,
                UpcomingTrigger.subject_key == candidate.subject_key,
                UpcomingTrigger.rule_version != rule_version,
                UpcomingTrigger.status == "PENDING",
            )
        )

        trigger, created = repo.upsert_trigger(session, payload)
        if created:
            stats.created += 1
        else:
            stats.updated += 1

        if stale is not None and stale.id != trigger.id:
            repo.supersede_trigger(session, stale, trigger)
            stats.superseded += 1

    stats.cancelled += repo.cancel_triggers(
        session, source_filing_id=filing.id, keep_keys=keep_keys
    )
    return stats


def refresh_calendar(
    session: Session,
    *,
    since: Optional[dt.date] = None,
    until: Optional[dt.date] = None,
    company_id: Optional[int] = None,
    categories: Optional[Sequence[str]] = None,
    offsets: Optional[Dict[str, int]] = None,
    rule_version: str = RULE_VERSION,
    as_of: Optional[dt.date] = None,
    dry_run: bool = False,
) -> RefreshStats:
    """Re-derive the calendar over a window of filings. Safe to re-run."""
    stats = RefreshStats()
    stmt = select(EventFiling).where(EventFiling.is_duplicate.is_(False))
    if since:
        stmt = stmt.where(EventFiling.filing_date >= since)
    if until:
        stmt = stmt.where(EventFiling.filing_date <= until)
    if company_id is not None:
        stmt = stmt.where(EventFiling.company_id == company_id)
    if categories:
        stmt = stmt.where(EventFiling.category.in_(tuple(categories)))

    for filing in session.scalars(stmt.order_by(EventFiling.filing_date)):
        try:
            stats.merge(
                refresh_for_filing(
                    session,
                    filing,
                    offsets=offsets,
                    rule_version=rule_version,
                    as_of=as_of,
                )
            )
        except Exception as exc:  # one bad filing must not kill the refresh
            stats.errors.append(f"filing {filing.id}: {exc}")
            log.exception("Calendar refresh failed", extra={"filing_id": filing.id})

    if dry_run:
        session.rollback()
    else:
        session.flush()
    log.info("Calendar refreshed", extra=stats.as_dict())
    return stats


def expire_triggers(session: Session, *, as_of: Optional[dt.date] = None) -> int:
    return repo.expire_triggers(session, as_of=as_of)
