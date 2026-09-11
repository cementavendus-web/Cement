"""Persistence helpers: idempotent upserts, dedupe, checkpoints, audit trail.

All writes go through this module so that duplicate detection and audit logging
happen in exactly one place. Every helper is safe to call repeatedly with the
same input — re-running a scrape window must never create a second row.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from typing import Any, Dict, Iterable, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AuditLog,
    Company,
    DailyAlert,
    EventFiling,
    FilingDocument,
    FilingInvestor,
    FilingPromoter,
    Investor,
    Promoter,
    ScrapeRun,
    Transaction,
)

log = logging.getLogger(__name__)

_SUFFIXES = (
    "limited",
    "ltd",
    "ltd.",
    "private",
    "pvt",
    "pvt.",
    "corporation",
    "corp",
    "company",
    "co",
    "co.",
    "inc",
    "plc",
    "llp",
    "&",
    "and",
)


def normalize_name(raw: str) -> str:
    """Collapse a company/investor name to a comparable key.

    ``"Reliance Industries Ltd."`` and ``"RELIANCE INDUSTRIES LIMITED"`` must
    resolve to the same company even though BSE, NSE and PDF cover pages all
    spell it differently.
    """
    if not raw:
        return ""
    text = re.sub(r"[^\w\s&]", " ", str(raw).lower())
    tokens = [tok for tok in text.split() if tok and tok not in _SUFFIXES]
    return " ".join(tokens).strip()


def content_hash(company_key: str, headline: str, filing_date: Optional[dt.date]) -> str:
    """Stable identity for an announcement across sources and re-scrapes."""
    headline_key = re.sub(r"\s+", " ", (headline or "").strip().lower())
    # Exchange headlines often differ only by trailing punctuation or a suffix
    # such as "- XBRL"; stripping those keeps cross-source dedupe working.
    headline_key = re.sub(r"[\-–]\s*xbrl\s*$", "", headline_key).strip(" .-")
    parts = [normalize_name(company_key), headline_key, filing_date.isoformat() if filing_date else ""]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Companies
# --------------------------------------------------------------------------
def upsert_company(
    session: Session,
    name: str,
    bse_code: Optional[str] = None,
    nse_symbol: Optional[str] = None,
    isin: Optional[str] = None,
    **fields: Any,
) -> Company:
    normalized = normalize_name(name)
    company: Optional[Company] = None

    if bse_code:
        company = session.scalar(select(Company).where(Company.bse_code == str(bse_code)))
    if company is None and isin:
        company = session.scalar(select(Company).where(Company.isin == isin))
    if company is None and normalized:
        company = session.scalar(select(Company).where(Company.normalized_name == normalized))

    if company is None:
        company = Company(name=name, normalized_name=normalized)
        session.add(company)

    # Only fill blanks: never let a sparse NSE row clobber richer BSE data.
    if bse_code and not company.bse_code:
        company.bse_code = str(bse_code)
    if nse_symbol and not company.nse_symbol:
        company.nse_symbol = nse_symbol
    if isin and not company.isin:
        company.isin = isin
    if name and len(name) > len(company.name or ""):
        company.name = name
    for key, value in fields.items():
        if value is not None and hasattr(company, key):
            setattr(company, key, value)

    session.flush()
    return company


# --------------------------------------------------------------------------
# Filings
# --------------------------------------------------------------------------
def find_filing(session: Session, *, content_hash_value: str) -> Optional[EventFiling]:
    return session.scalar(
        select(EventFiling).where(EventFiling.content_hash == content_hash_value)
    )


def upsert_filing(session: Session, payload: Dict[str, Any]) -> tuple[EventFiling, bool]:
    """Insert or update a filing.

    Returns ``(filing, created)``. An existing row is updated in place so that a
    later pass — for example one that has since parsed the PDF — can enrich a
    filing discovered by an earlier, lighter run.
    """
    digest = payload["content_hash"]
    filing = find_filing(session, content_hash_value=digest)
    created = filing is None
    if filing is None:
        filing = EventFiling(content_hash=digest)
        session.add(filing)

    for key, value in payload.items():
        if key == "content_hash":
            continue
        if hasattr(filing, key) and value is not None:
            setattr(filing, key, value)

    session.flush()
    audit(
        session,
        "event_filing",
        str(filing.id),
        "created" if created else "updated",
        {"category": filing.category, "score": filing.opportunity_score},
    )
    return filing, created


def mark_duplicate(session: Session, filing: EventFiling, original: EventFiling) -> None:
    filing.is_duplicate = True
    filing.duplicate_of_id = original.id
    session.flush()


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------
def upsert_document(session: Session, filing_id: int, url: str, **fields: Any) -> FilingDocument:
    doc = session.scalar(
        select(FilingDocument).where(
            FilingDocument.filing_id == filing_id, FilingDocument.url == url
        )
    )
    if doc is None:
        doc = FilingDocument(filing_id=filing_id, url=url)
        session.add(doc)
    for key, value in fields.items():
        if hasattr(doc, key) and value is not None:
            setattr(doc, key, value)
    session.flush()
    return doc


def document_by_sha(session: Session, sha256: str) -> Optional[FilingDocument]:
    """Look up an already-extracted attachment so identical PDFs parse once."""
    return session.scalar(
        select(FilingDocument)
        .where(FilingDocument.sha256 == sha256, FilingDocument.download_status == "OK")
        .limit(1)
    )


# --------------------------------------------------------------------------
# Investors / promoters
# --------------------------------------------------------------------------
def upsert_investor(
    session: Session,
    name: str,
    investor_type: str = "Unknown",
    is_marquee: bool = False,
    aliases: Optional[Sequence[str]] = None,
    country: Optional[str] = None,
    discovered: bool = False,
) -> Investor:
    normalized = normalize_name(name)
    investor = session.scalar(select(Investor).where(Investor.normalized_name == normalized))
    if investor is None:
        investor = Investor(
            name=name,
            normalized_name=normalized,
            investor_type=investor_type,
            is_marquee=is_marquee,
            aliases=list(aliases or []),
            country=country,
            discovered=discovered,
        )
        session.add(investor)
    else:
        if investor_type != "Unknown":
            investor.investor_type = investor_type
        investor.is_marquee = investor.is_marquee or is_marquee
        if aliases:
            merged = sorted({*(investor.aliases or []), *aliases})
            investor.aliases = merged
        if country and not investor.country:
            investor.country = country
    session.flush()
    return investor


def upsert_promoter(
    session: Session, company_id: Optional[int], name: str, role: str = "Promoter"
) -> Promoter:
    normalized = normalize_name(name)
    promoter = session.scalar(
        select(Promoter).where(
            Promoter.company_id == company_id, Promoter.normalized_name == normalized
        )
    )
    if promoter is None:
        promoter = Promoter(
            company_id=company_id, name=name, normalized_name=normalized, role=role
        )
        session.add(promoter)
    session.flush()
    return promoter


def link_investor(
    session: Session, filing_id: int, investor_id: int, context: str = "", confidence: float = 1.0
) -> None:
    exists = session.get(FilingInvestor, {"filing_id": filing_id, "investor_id": investor_id})
    if exists is None:
        session.add(
            FilingInvestor(
                filing_id=filing_id,
                investor_id=investor_id,
                mention_context=context[:2000],
                confidence=confidence,
            )
        )
        session.flush()


def link_promoter(
    session: Session, filing_id: int, promoter_id: int, context: str = "", confidence: float = 1.0
) -> None:
    exists = session.get(FilingPromoter, {"filing_id": filing_id, "promoter_id": promoter_id})
    if exists is None:
        session.add(
            FilingPromoter(
                filing_id=filing_id,
                promoter_id=promoter_id,
                mention_context=context[:2000],
                confidence=confidence,
            )
        )
        session.flush()


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------
def replace_transactions(
    session: Session, filing_id: int, rows: Iterable[Dict[str, Any]]
) -> list[Transaction]:
    """Transactions are derived data — rewrite them wholesale on re-extraction."""
    for existing in session.scalars(
        select(Transaction).where(Transaction.filing_id == filing_id)
    ):
        session.delete(existing)
    session.flush()

    created: list[Transaction] = []
    for row in rows:
        txn = Transaction(filing_id=filing_id, **row)
        session.add(txn)
        created.append(txn)
    session.flush()
    return created


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
def queue_alert(
    session: Session,
    *,
    filing_id: int,
    alert_date: dt.date,
    priority: str,
    channel: str,
    title: str,
    body: str,
) -> tuple[DailyAlert, bool]:
    dedupe_key = f"{filing_id}:{channel}"
    alert = session.scalar(select(DailyAlert).where(DailyAlert.dedupe_key == dedupe_key))
    if alert is not None:
        return alert, False
    alert = DailyAlert(
        filing_id=filing_id,
        alert_date=alert_date,
        priority=priority,
        channel=channel,
        title=title[:500],
        body=body,
        dedupe_key=dedupe_key,
        status="PENDING",
    )
    session.add(alert)
    session.flush()
    return alert, True


def pending_alerts(session: Session, channel: Optional[str] = None) -> list[DailyAlert]:
    stmt = select(DailyAlert).where(DailyAlert.status == "PENDING")
    if channel:
        stmt = stmt.where(DailyAlert.channel == channel)
    return list(session.scalars(stmt.order_by(DailyAlert.priority, DailyAlert.id)))


def mark_alert(
    session: Session, alert: DailyAlert, status: str, error: Optional[str] = None
) -> None:
    alert.status = status
    alert.error = error
    if status == "SENT":
        alert.sent_at = dt.datetime.now(dt.timezone.utc)
    session.flush()


# --------------------------------------------------------------------------
# Runs / checkpoints / audit
# --------------------------------------------------------------------------
def start_run(
    session: Session, source: str, from_date: dt.date, to_date: dt.date
) -> ScrapeRun:
    run = ScrapeRun(source=source, from_date=from_date, to_date=to_date, status="RUNNING")
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run: ScrapeRun, status: str, **counters: Any) -> None:
    run.status = status
    run.run_finished_at = dt.datetime.now(dt.timezone.utc)
    for key, value in counters.items():
        if hasattr(run, key):
            setattr(run, key, value)
    session.flush()


def last_successful_run(session: Session, source: str) -> Optional[ScrapeRun]:
    """Latest checkpoint for a source; drives the incremental scrape window."""
    return session.scalar(
        select(ScrapeRun)
        .where(ScrapeRun.source == source, ScrapeRun.status.in_(("OK", "PARTIAL")))
        .order_by(ScrapeRun.run_started_at.desc())
        .limit(1)
    )


def audit(
    session: Session,
    entity_type: str,
    entity_id: Optional[str],
    action: str,
    details: Optional[Dict[str, Any]] = None,
    actor: str = "pipeline",
) -> None:
    session.add(
        AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            details=details or {},
        )
    )


def counts(session: Session) -> Dict[str, int]:
    return {
        "companies": session.scalar(select(func.count()).select_from(Company)) or 0,
        "filings": session.scalar(select(func.count()).select_from(EventFiling)) or 0,
        "documents": session.scalar(select(func.count()).select_from(FilingDocument)) or 0,
        "transactions": session.scalar(select(func.count()).select_from(Transaction)) or 0,
        "alerts": session.scalar(select(func.count()).select_from(DailyAlert)) or 0,
    }
