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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    AuditLog,
    Company,
    DailyAlert,
    DailyQuote,
    Disposal,
    EventFiling,
    FilingDocument,
    FilingInvestor,
    FilingPromoter,
    Investor,
    LlmExtraction,
    Promoter,
    ScrapeRun,
    Transaction,
    UpcomingTrigger,
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
def company_by_code(session: Session, code: str) -> Optional[Company]:
    """Resolve a scrip code or NSE symbol to a tracked company."""
    if not code:
        return None
    return session.scalar(
        select(Company).where(
            (Company.bse_code == code) | (Company.nse_symbol == code)
        ).limit(1)
    )


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
    alert_date: dt.date,
    priority: str,
    channel: str,
    title: str,
    body: str,
    filing_id: Optional[int] = None,
    trigger_id: Optional[int] = None,
    horizon: Optional[int] = None,
) -> tuple[DailyAlert, bool]:
    """Queue one alert, deduplicated.

    Filing alerts key on ``(filing, channel)`` exactly as before. Calendar
    reminders key on ``(trigger, horizon, channel)`` so that a T-30 and a T-7
    reminder for the same deadline can both fire, while re-running either is
    still a no-op.
    """
    if filing_id is not None:
        dedupe_key = f"{filing_id}:{channel}"
    elif trigger_id is not None:
        dedupe_key = f"trigger:{trigger_id}:T-{horizon if horizon is not None else 0}:{channel}"
    else:
        raise ValueError("queue_alert needs either filing_id or trigger_id")

    alert = session.scalar(select(DailyAlert).where(DailyAlert.dedupe_key == dedupe_key))
    if alert is not None:
        return alert, False
    alert = DailyAlert(
        filing_id=filing_id,
        trigger_id=trigger_id,
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
        "triggers": session.scalar(select(func.count()).select_from(UpcomingTrigger)) or 0,
        "llm_extractions": session.scalar(select(func.count()).select_from(LlmExtraction)) or 0,
        "disposals": session.scalar(select(func.count()).select_from(Disposal)) or 0,
        "quotes": session.scalar(select(func.count()).select_from(DailyQuote)) or 0,
    }


# --------------------------------------------------------------------------
# Upcoming triggers (forward calendar)
# --------------------------------------------------------------------------
def upsert_trigger(session: Session, payload: Dict[str, Any]) -> tuple[UpcomingTrigger, bool]:
    """Insert or update a deadline, keyed on ``dedupe_key``.

    Deliberately the opposite of :func:`replace_transactions`: a deadline is
    updated in place, never deleted and recreated, so its id — and the alert
    history pointing at it — survives every re-derivation.
    """
    key = payload["dedupe_key"]
    trigger = session.scalar(select(UpcomingTrigger).where(UpcomingTrigger.dedupe_key == key))
    created = trigger is None
    if trigger is None:
        trigger = UpcomingTrigger(dedupe_key=key)
        session.add(trigger)

    for field, value in payload.items():
        if field == "dedupe_key":
            continue
        # Skip None so a later, sparser pass never nulls richer earlier data.
        if hasattr(trigger, field) and value is not None:
            setattr(trigger, field, value)

    session.flush()
    audit(
        session,
        "upcoming_trigger",
        str(trigger.id),
        "created" if created else "updated",
        {"type": trigger.trigger_type, "date": str(trigger.trigger_date)},
    )
    return trigger, created


def triggers_for_filing(
    session: Session,
    source_filing_id: int,
    statuses: Sequence[str] = ("PENDING",),
) -> list[UpcomingTrigger]:
    return list(
        session.scalars(
            select(UpcomingTrigger).where(
                UpcomingTrigger.source_filing_id == source_filing_id,
                UpcomingTrigger.status.in_(tuple(statuses)),
            )
        )
    )


def cancel_triggers(session: Session, *, source_filing_id: int, keep_keys: set[str]) -> int:
    """Cancel deadlines this filing no longer implies.

    Cancelled, never deleted: a deleted row would lose the alert history that
    references it and would re-fire a reminder on the next refresh.
    """
    cancelled = 0
    for trigger in triggers_for_filing(session, source_filing_id):
        if trigger.dedupe_key not in keep_keys:
            trigger.status = "CANCELLED"
            cancelled += 1
            audit(session, "upcoming_trigger", str(trigger.id), "cancelled", {})
    session.flush()
    return cancelled


def supersede_trigger(session: Session, old: UpcomingTrigger, new: UpcomingTrigger) -> None:
    old.status = "SUPERSEDED"
    old.superseded_by_id = new.id
    session.flush()
    audit(session, "upcoming_trigger", str(old.id), "superseded", {"by": new.id})


def mark_trigger(
    session: Session,
    trigger: UpcomingTrigger,
    status: str,
    fired_at: Optional[dt.datetime] = None,
) -> None:
    trigger.status = status
    if status == "FIRED":
        trigger.fired_at = fired_at or dt.datetime.now(dt.timezone.utc)
    session.flush()


def triggers_due(
    session: Session,
    *,
    as_of: Optional[dt.date] = None,
    within_days: int = 90,
    statuses: Sequence[str] = ("PENDING",),
    company_id: Optional[int] = None,
    trigger_types: Optional[Sequence[str]] = None,
    min_confidence: float = 0.0,
    limit: int = 500,
) -> list[tuple[UpcomingTrigger, int]]:
    """Deadlines falling inside the horizon, with days-to-trigger computed.

    The day count is computed in Python against an injected ``as_of`` rather than
    in SQL: date arithmetic differs between SQLite and PostgreSQL, so a SQL
    expression here would pass the tests and misbehave in production.
    """
    today = as_of or dt.date.today()
    horizon = today + dt.timedelta(days=within_days)
    stmt = (
        select(UpcomingTrigger)
        .where(
            UpcomingTrigger.status.in_(tuple(statuses)),
            UpcomingTrigger.trigger_date >= today,
            UpcomingTrigger.trigger_date <= horizon,
            UpcomingTrigger.confidence >= min_confidence,
        )
        .order_by(UpcomingTrigger.trigger_date, UpcomingTrigger.company_id)
        .limit(limit)
    )
    if company_id is not None:
        stmt = stmt.where(UpcomingTrigger.company_id == company_id)
    if trigger_types:
        stmt = stmt.where(UpcomingTrigger.trigger_type.in_(tuple(trigger_types)))

    return [(row, row.days_to(today)) for row in session.scalars(stmt)]


def expire_triggers(session: Session, *, as_of: Optional[dt.date] = None) -> int:
    """Move PENDING deadlines whose date has passed to FIRED."""
    today = as_of or dt.date.today()
    fired = 0
    for trigger in session.scalars(
        select(UpcomingTrigger).where(
            UpcomingTrigger.status == "PENDING", UpcomingTrigger.trigger_date < today
        )
    ):
        mark_trigger(session, trigger, "FIRED")
        fired += 1
    return fired


# --------------------------------------------------------------------------
# LLM extractions
# --------------------------------------------------------------------------
def llm_extraction_by_announcement(
    session: Session, announcement_id: str
) -> Optional[LlmExtraction]:
    return session.scalar(
        select(LlmExtraction).where(LlmExtraction.announcement_id == announcement_id)
    )


def claim_llm_extraction(
    session: Session,
    *,
    filing_id: int,
    announcement_id: str,
    source: str,
    model: str,
    prompt_version: str,
    mode: str = "SYNC",
    custom_id: Optional[str] = None,
    request_fingerprint: Optional[str] = None,
) -> tuple[LlmExtraction, bool]:
    """Reserve the right to call the API for this announcement.

    Returns ``(row, created)``. ``created is False`` means the announcement has
    already been sent and must not be sent again.

    The claim is written and flushed *before* the API call, and the uniqueness of
    ``announcement_id`` is enforced by the database rather than by a prior SELECT:
    a check-then-insert leaves a race under concurrent runs, and a crash between
    the two statements would cause a re-send and a double charge.
    """
    existing = llm_extraction_by_announcement(session, announcement_id)
    if existing is not None:
        return existing, False

    row = LlmExtraction(
        filing_id=filing_id,
        announcement_id=announcement_id,
        source=source,
        model=model,
        prompt_version=prompt_version,
        mode=mode,
        status="PENDING",
        custom_id=custom_id,
        request_fingerprint=request_fingerprint,
        requested_at=dt.datetime.now(dt.timezone.utc),
        attempts=0,
    )
    session.add(row)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        # Lost the race: another worker claimed it between the SELECT and here.
        session.expunge(row)
        winner = llm_extraction_by_announcement(session, announcement_id)
        if winner is not None:
            return winner, False
        raise
    return row, True


def record_llm_result(
    session: Session,
    row: LlmExtraction,
    *,
    call: Any = None,
    verdict: Any = None,
    status: str = "OK",
    error: Optional[str] = None,
    persist_raw: bool = True,
) -> None:
    row.status = status
    row.error = error
    row.completed_at = dt.datetime.now(dt.timezone.utc)

    if call is not None:
        usage = getattr(call, "usage", None)
        if usage is not None:
            row.input_tokens = usage.input_tokens
            row.output_tokens = usage.output_tokens
            row.cache_creation_input_tokens = usage.cache_creation_input_tokens
            row.cache_read_input_tokens = usage.cache_read_input_tokens
        row.cost_usd = getattr(call, "cost_usd", 0) or 0
        row.latency_ms = getattr(call, "latency_ms", None)
        row.attempts = getattr(call, "attempts", row.attempts or 0)
        row.model = getattr(call, "model", row.model) or row.model
        if persist_raw:
            row.raw_response = getattr(call, "response_json", {}) or {}

    if verdict is not None:
        row.event_class = verdict.event_class
        row.holder = verdict.holder
        row.holder_normalized = normalize_name(verdict.holder or "")
        row.holder_type = verdict.holder_type
        row.stake_pct = verdict.stake_pct
        row.effective_date = verdict.effective_date
        row.llm_confidence = verdict.confidence
        row.parsed = verdict.to_dict()

    session.flush()


def llm_rows_for_batch(session: Session, batch_id: str) -> list[LlmExtraction]:
    return list(
        session.scalars(select(LlmExtraction).where(LlmExtraction.batch_id == batch_id))
    )


def reset_llm_row(session: Session, row: LlmExtraction, reason: str) -> None:
    """Return a row to PENDING so a later run retries it."""
    row.status = "PENDING"
    row.error = reason
    row.batch_id = None
    row.attempts = (row.attempts or 0) + 1
    session.flush()


def llm_usage_summary(session: Session, since: Optional[dt.date] = None) -> Dict[str, Any]:
    stmt = select(LlmExtraction)
    if since:
        stmt = stmt.where(LlmExtraction.created_at >= dt.datetime.combine(since, dt.time.min))
    rows = list(session.scalars(stmt))
    reads = sum(r.cache_read_input_tokens or 0 for r in rows)
    inputs = sum(r.input_tokens or 0 for r in rows)
    return {
        "calls": len(rows),
        "ok": sum(1 for r in rows if r.status == "OK"),
        "failed": sum(1 for r in rows if r.status == "FAILED"),
        "input_tokens": inputs,
        "output_tokens": sum(r.output_tokens or 0 for r in rows),
        "cache_read_tokens": reads,
        "cache_hit_rate": round(reads / (reads + inputs), 4) if (reads + inputs) else 0.0,
        "cost_usd": round(float(sum(float(r.cost_usd or 0) for r in rows)), 4),
    }


# --------------------------------------------------------------------------
# Disposals (label spine) and quotes
# --------------------------------------------------------------------------
def disposal_dedupe_key(
    company_id: int,
    holder_key: str,
    trade_date: dt.date,
    side: str,
    quantity: Optional[float],
) -> str:
    """Identity for one trade, independent of which source reported it.

    The same block deal is published by both exchanges and may be filed again as
    a SAST disclosure. Keying on the economics rather than the source is what
    stops one sale being counted three times and inflating every label.
    """
    qty = f"{float(quantity):.0f}" if quantity else ""
    parts = [str(company_id), holder_key, trade_date.isoformat(), side, qty]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def upsert_disposal(session: Session, payload: Dict[str, Any]) -> tuple[Disposal, bool]:
    key = payload["dedupe_key"]
    row = session.scalar(select(Disposal).where(Disposal.dedupe_key == key))
    created = row is None
    if row is None:
        row = Disposal(dedupe_key=key)
        session.add(row)
    for field, value in payload.items():
        if field != "dedupe_key" and hasattr(row, field) and value is not None:
            setattr(row, field, value)
    session.flush()
    return row, created


def disposals_between(
    session: Session,
    *,
    company_id: Optional[int] = None,
    holder_key: Optional[str] = None,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
    side: str = "SELL",
) -> list[Disposal]:
    stmt = select(Disposal).where(Disposal.side == side)
    if company_id is not None:
        stmt = stmt.where(Disposal.company_id == company_id)
    if holder_key:
        stmt = stmt.where(Disposal.holder_key == holder_key)
    if start:
        stmt = stmt.where(Disposal.trade_date >= start)
    if end:
        stmt = stmt.where(Disposal.trade_date <= end)
    return list(session.scalars(stmt.order_by(Disposal.trade_date)))


def upsert_quote(session: Session, payload: Dict[str, Any]) -> tuple[DailyQuote, bool]:
    row = session.scalar(
        select(DailyQuote).where(
            DailyQuote.company_id == payload["company_id"],
            DailyQuote.trade_date == payload["trade_date"],
            DailyQuote.exchange == payload.get("exchange", "BSE"),
        )
    )
    created = row is None
    if row is None:
        row = DailyQuote(**payload)
        session.add(row)
    else:
        for field, value in payload.items():
            if hasattr(row, field) and value is not None:
                setattr(row, field, value)
    session.flush()
    return row, created


def latest_quote(
    session: Session, company_id: int, as_of: Optional[dt.date] = None
) -> Optional[DailyQuote]:
    """Most recent quote strictly at or before ``as_of`` — never after."""
    stmt = select(DailyQuote).where(DailyQuote.company_id == company_id)
    if as_of:
        stmt = stmt.where(DailyQuote.trade_date <= as_of)
    return session.scalar(stmt.order_by(DailyQuote.trade_date.desc()).limit(1))


def average_daily_volume(
    session: Session, company_id: int, as_of: dt.date, window_days: int = 30
) -> Optional[float]:
    start = as_of - dt.timedelta(days=window_days)
    rows = list(
        session.scalars(
            select(DailyQuote).where(
                DailyQuote.company_id == company_id,
                DailyQuote.trade_date > start,
                DailyQuote.trade_date <= as_of,
            )
        )
    )
    volumes = [float(r.volume) for r in rows if r.volume]
    return sum(volumes) / len(volumes) if volumes else None
