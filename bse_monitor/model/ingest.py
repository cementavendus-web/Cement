"""Persisting deals and quotes into the label spine."""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from ..database import repository as repo
from ..scraper.deals import DealRecord

log = logging.getLogger(__name__)


@dataclasses.dataclass
class IngestStats:
    seen: int = 0
    created: int = 0
    duplicate: int = 0
    skipped: int = 0
    errors: List[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seen": self.seen,
            "created": self.created,
            "duplicate": self.duplicate,
            "skipped": self.skipped,
            "errors": self.errors[:20],
        }


def ingest_deals(session: Session, records: Iterable[DealRecord]) -> IngestStats:
    """Upsert deal records, resolving companies and holders as we go."""
    stats = IngestStats()
    for record in records:
        stats.seen += 1
        try:
            company_name = record.get("company_name") or ""
            if not company_name or not record.get("trade_date"):
                stats.skipped += 1
                continue

            company = repo.upsert_company(
                session, name=company_name, bse_code=record.get("symbol")
            )
            holder_key = repo.normalize_name(record.get("holder_name") or "")
            if not holder_key:
                stats.skipped += 1
                continue

            key = repo.disposal_dedupe_key(
                company.id,
                holder_key,
                record["trade_date"],
                record.get("side", "SELL"),
                record.get("quantity"),
            )
            _row, created = repo.upsert_disposal(
                session,
                {
                    "dedupe_key": key,
                    "company_id": company.id,
                    "source": record.get("source", "MANUAL"),
                    "source_filing_id": record.get("source_filing_id"),
                    "holder_name": record.get("holder_name"),
                    "holder_key": holder_key,
                    "holder_type": record.get("holder_type", "UNKNOWN"),
                    "trade_date": record["trade_date"],
                    "side": record.get("side", "SELL"),
                    "quantity": record.get("quantity"),
                    "price": record.get("price"),
                    "value_inr": record.get("value_inr"),
                    "percent_of_equity": record.get("percent_of_equity"),
                    "exchange": record.get("exchange"),
                    "raw": record.get("raw", {}),
                },
            )
            stats.created += int(created)
            stats.duplicate += int(not created)
        except Exception as exc:
            stats.errors.append(str(exc))
            log.exception("Deal ingest failed")
    session.flush()
    log.info("Deals ingested", extra=stats.as_dict())
    return stats


def ingest_quotes(session: Session, quotes: Iterable[Dict[str, Any]]) -> IngestStats:
    """Upsert bhavcopy rows. Quotes for unknown scrips are skipped, not created:
    the bhavcopy covers the whole market and we only track companies that have
    actually filed something."""
    stats = IngestStats()
    for quote in quotes:
        stats.seen += 1
        code = str(quote.get("code") or "").strip()
        company = repo.company_by_code(session, code)
        if company is None:
            stats.skipped += 1
            continue
        _row, created = repo.upsert_quote(
            session,
            {
                "company_id": company.id,
                "trade_date": quote["trade_date"],
                "close_price": quote.get("close_price"),
                "volume": quote.get("volume"),
                "turnover_inr": quote.get("turnover_inr"),
                "exchange": quote.get("exchange", "BSE"),
            },
        )
        stats.created += int(created)
        stats.duplicate += int(not created)
    session.flush()
    return stats
