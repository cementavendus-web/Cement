"""The three reporting views the system exists to produce.

1. **Daily Events Table** — everything classified today, ranked by score.
2. **Fund Raise Pipeline** — companies with a live or proposed primary raise.
3. **Potential Sell-Down Pipeline** — companies where promoters or financial
   investors look likely to sell into the secondary market.

Each returns a list of dicts and, when pandas is installed, a DataFrame, so the
same query backs a CSV export, an email table and a notebook.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..alerts.formatting import format_inr, format_row
from ..database.models import Company, EventFiling, Transaction

log = logging.getLogger(__name__)

FUND_RAISE_CATEGORIES = ("QIP", "FPO", "IPO", "RightsIssue", "PreferentialAllotment", "FundRaise")
SELL_DOWN_CATEGORIES = ("OFS", "BlockDeal", "InvestorExit", "PromoterSale")

DAILY_COLUMNS = ("date", "company", "event_type", "investor", "value", "score")


def _base_query():
    return (
        select(EventFiling)
        .options(
            selectinload(EventFiling.company),
            selectinload(EventFiling.transactions),
            selectinload(EventFiling.investor_links),
        )
        .where(EventFiling.is_duplicate.is_(False))
    )


def daily_events(
    session: Session,
    for_date: Optional[dt.date] = None,
    min_score: int = 0,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    day = for_date or dt.date.today()
    stmt = (
        _base_query()
        .where(EventFiling.filing_date == day, EventFiling.opportunity_score >= min_score)
        .order_by(EventFiling.opportunity_score.desc(), EventFiling.id.desc())
        .limit(limit)
    )
    return [format_row(filing) for filing in session.scalars(stmt)]


def fund_raise_pipeline(
    session: Session,
    since: Optional[dt.date] = None,
    lookback_days: int = 90,
    min_score: int = 60,
) -> List[Dict[str, Any]]:
    """Live primary-issuance pipeline.

    Rows are deduplicated per (company, category) keeping the highest-scoring —
    a QIP typically generates a board-meeting intimation, an approval and an
    allotment, and the pipeline should show one line per raise, at its most
    advanced stage.
    """
    start = since or (dt.date.today() - dt.timedelta(days=lookback_days))
    stmt = (
        _base_query()
        .where(
            EventFiling.category.in_(FUND_RAISE_CATEGORIES),
            EventFiling.filing_date >= start,
            EventFiling.opportunity_score >= min_score,
        )
        .order_by(EventFiling.opportunity_score.desc(), EventFiling.filing_date.desc())
    )

    best: Dict[tuple, Dict[str, Any]] = {}
    for filing in session.scalars(stmt):
        company = filing.company
        key = (getattr(company, "id", None), filing.category)
        row = format_row(filing)
        transaction = next(iter(filing.transactions), None)
        row.update(
            {
                "stage": _stage_for(filing),
                "issue_size": format_inr(
                    float(transaction.issue_size_inr) if transaction and transaction.issue_size_inr else None
                ),
                "board_meeting_date": (
                    transaction.board_meeting_date.isoformat()
                    if transaction and transaction.board_meeting_date
                    else ""
                ),
                "confidence": round(filing.classification_confidence, 3),
            }
        )
        existing = best.get(key)
        if existing is None or row["score"] > existing["score"]:
            best[key] = row
    return sorted(best.values(), key=lambda r: -r["score"])


def sell_down_pipeline(
    session: Session,
    since: Optional[dt.date] = None,
    lookback_days: int = 120,
    min_score: int = 60,
    lock_in_horizon_days: int = 90,
) -> List[Dict[str, Any]]:
    """Companies at risk of a secondary supply event.

    Two independent entry criteria, unioned:

    * a filing classified into one of the sell-down categories, and
    * any filing with an extracted lock-in expiry inside the horizon — an
      anchor lock-in release is a supply event even when the filing that
      disclosed it was an ordinary allotment intimation.
    """
    start = since or (dt.date.today() - dt.timedelta(days=lookback_days))
    horizon = dt.date.today() + dt.timedelta(days=lock_in_horizon_days)

    stmt = (
        _base_query()
        .outerjoin(Transaction, Transaction.filing_id == EventFiling.id)
        .where(
            EventFiling.filing_date >= start,
            or_(
                EventFiling.category.in_(SELL_DOWN_CATEGORIES),
                Transaction.lock_in_expiry_date.between(dt.date.today(), horizon),
            ),
        )
        .order_by(EventFiling.opportunity_score.desc())
        .distinct()
    )

    rows: List[Dict[str, Any]] = []
    seen: set[tuple] = set()
    for filing in session.scalars(stmt):
        if filing.opportunity_score < min_score and filing.category not in SELL_DOWN_CATEGORIES:
            continue
        key = (getattr(filing.company, "id", None), filing.category)
        if key in seen:
            continue
        seen.add(key)
        transaction = next(iter(filing.transactions), None)
        row = format_row(filing)
        row.update(
            {
                "seller": _seller_for(filing),
                "stake_pct": (
                    f"{transaction.percent_of_equity:.2f}%"
                    if transaction and transaction.percent_of_equity
                    else ""
                ),
                "lock_in_expiry": (
                    transaction.lock_in_expiry_date.isoformat()
                    if transaction and transaction.lock_in_expiry_date
                    else ""
                ),
                "trigger": _sell_down_trigger(filing, transaction, horizon),
            }
        )
        rows.append(row)
    return sorted(rows, key=lambda r: -r["score"])


def _stage_for(filing: EventFiling) -> str:
    return {
        "completed": "Completed / Allotted",
        "approved": "Approved",
        "intended": "Proposed / Under consideration",
    }.get(filing.certainty, "Disclosed")


def _seller_for(filing: EventFiling) -> str:
    investors = [
        link.investor.name for link in filing.investor_links or [] if link.investor
    ]
    promoters = [
        link.promoter.name for link in filing.promoter_links or [] if link.promoter
    ]
    parties = promoters + investors
    if parties:
        return ", ".join(parties[:3])
    return "Promoter" if filing.category == "PromoterSale" else "—"


def _sell_down_trigger(
    filing: EventFiling, transaction: Optional[Transaction], horizon: dt.date
) -> str:
    if transaction and transaction.lock_in_expiry_date and transaction.lock_in_expiry_date <= horizon:
        return f"Lock-in expiry {transaction.lock_in_expiry_date.isoformat()}"
    return {
        "OFS": "OFS announced",
        "BlockDeal": "Block/bulk deal disclosed",
        "PromoterSale": "Promoter sale signalled",
        "InvestorExit": "Investor exit signalled",
    }.get(filing.category, filing.category)


def to_dataframe(rows: Sequence[Dict[str, Any]], columns: Optional[Sequence[str]] = None):
    """Return a pandas DataFrame, or None when pandas is not installed."""
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover - optional
        log.warning("pandas not installed; returning None instead of a DataFrame")
        return None
    frame = pd.DataFrame(list(rows))
    if columns is not None and not frame.empty:
        keep = [col for col in columns if col in frame.columns]
        frame = frame[keep]
    return frame


def write_csv(rows: Sequence[Dict[str, Any]], path: str, columns: Optional[Sequence[str]] = None) -> str:
    import csv
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        target.write_text("", encoding="utf-8")
        return str(target)
    fieldnames = list(columns or rows[0].keys())
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return str(target)


def company_watchlist(session: Session, min_score: int = 70, limit: int = 100) -> List[Dict[str, Any]]:
    """Companies ranked by the strength of their recent event flow."""
    stmt = (
        select(Company, EventFiling)
        .join(EventFiling, EventFiling.company_id == Company.id)
        .where(
            EventFiling.opportunity_score >= min_score,
            EventFiling.is_duplicate.is_(False),
        )
        .order_by(EventFiling.opportunity_score.desc())
        .limit(limit * 4)
    )
    aggregate: Dict[int, Dict[str, Any]] = {}
    for company, filing in session.execute(stmt):
        entry = aggregate.setdefault(
            company.id,
            {
                "company": company.name,
                "bse_code": company.bse_code or "",
                "events": 0,
                "max_score": 0,
                "categories": set(),
                "latest": None,
            },
        )
        entry["events"] += 1
        entry["max_score"] = max(entry["max_score"], filing.opportunity_score)
        entry["categories"].add(filing.category)
        if filing.filing_date and (entry["latest"] is None or filing.filing_date > entry["latest"]):
            entry["latest"] = filing.filing_date
    rows = []
    for entry in aggregate.values():
        entry["categories"] = ", ".join(sorted(entry["categories"]))
        entry["latest"] = entry["latest"].isoformat() if entry["latest"] else ""
        rows.append(entry)
    return sorted(rows, key=lambda r: (-r["max_score"], -r["events"]))[:limit]
