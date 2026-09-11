"""Gating, dedupe and persistence for the LLM pass.

Gating is rule-prefiltered: the LLM is called only on filings the rule engine
already finds interesting, or cannot classify. At BSE's ~3,000 filings a day
that is a few hundred calls rather than all of them. The trade is explicit —
the rules become the recall ceiling for anything the LLM might otherwise have
caught on its own.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from typing import Any, Dict, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from ..database import repository as repo
from ..database.models import EventFiling
from .client import LlmClient
from .prompts import PROMPT_VERSION
from .schema import LlmVerdict

log = logging.getLogger(__name__)

PRIORITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

# Categories always worth a second read, regardless of score.
DEFAULT_CATEGORIES: Tuple[str, ...] = (
    "OFS", "BlockDeal", "InvestorExit", "PromoterSale",
    "QIP", "IPO", "FPO", "RightsIssue", "PreferentialAllotment", "FundRaise",
)


def announcement_key(
    source: str, source_filing_id: Optional[str], content_hash_value: str
) -> str:
    """Stable dedupe identity for one announcement.

    ``source_filing_id`` is nullable and only unique *per source*, so it is not
    safe alone; ``content_hash`` is NOT NULL and globally unique and is the
    guaranteed fallback.
    """
    if source_filing_id:
        return f"{source}:{source_filing_id}"
    return f"hash:{content_hash_value}"


def request_fingerprint(model: str, prompt_version: str, page_text: str) -> str:
    return hashlib.sha256(
        f"{model}|{prompt_version}|{page_text}".encode("utf-8")
    ).hexdigest()


def page_text_for(
    document_meta: Optional[Dict[str, Any]],
    fallback_text: str,
    *,
    max_pages: int = 3,
    max_chars: int = 12_000,
) -> Tuple[str, str]:
    """First-N-pages text, with the slice provenance recorded.

    Three sources in priority order. The third exists because the pipeline's
    cached-document path returns a single joined string from ``filing_documents``
    with no page structure and no guarantee the local file still exists — that
    path genuinely cannot produce a true page slice, so it is labelled
    ``char_budget`` rather than quietly pretending otherwise.
    """
    meta = document_meta or {}
    pages = meta.get("pages") or []
    if pages:
        text = "\n".join(pages[:max_pages]).strip()
        if text:
            return text[:max_chars], f"pages:1-{min(max_pages, len(pages))}"

    extracted = meta.get("extracted_text") or ""
    if extracted:
        return extracted[:max_chars], "char_budget"

    return (fallback_text or "")[:max_chars], "headline_only"


@dataclasses.dataclass
class ExtractStats:
    calls: int = 0
    cached: int = 0
    failed: int = 0
    skipped: int = 0
    cost_usd: float = 0.0


class LlmEventExtractor:
    def __init__(
        self,
        client: LlmClient,
        *,
        prompt_version: str = PROMPT_VERSION,
        max_calls_per_run: int = 200,
        min_priority: str = "MEDIUM",
        categories: Sequence[str] = (),
        send_unclassified: bool = True,
        persist_raw: bool = True,
    ) -> None:
        self.client = client
        self.prompt_version = prompt_version
        self.max_calls_per_run = max_calls_per_run
        self.min_priority = str(min_priority).upper()
        self.categories = tuple(categories) or DEFAULT_CATEGORIES
        self.send_unclassified = send_unclassified
        self.persist_raw = persist_raw
        self.stats = ExtractStats()

    @property
    def available(self) -> bool:
        return self.client is not None and self.client.available

    def should_extract(self, filing: EventFiling) -> bool:
        """Rule-prefilter: interesting, or unclassifiable."""
        if not self.available or self.stats.calls >= self.max_calls_per_run:
            return False
        if filing.is_duplicate:
            return False
        if filing.category in self.categories:
            return PRIORITY_ORDER.get(filing.priority, 0) >= PRIORITY_ORDER.get(
                self.min_priority, 1
            )
        # An `Other` verdict the rules were not confident about is exactly the
        # long tail the LLM exists to catch.
        if self.send_unclassified and filing.category == "Other":
            return float(filing.classification_confidence or 0.0) < 0.35
        return False

    def extract(
        self,
        session: Session,
        filing: EventFiling,
        *,
        title: str,
        page_text: str,
        page_slice: str,
        force: bool = False,
    ) -> Optional[LlmVerdict]:
        """Run one extraction, or return the stored verdict if already done."""
        key = announcement_key(filing.source, filing.source_filing_id, filing.content_hash)

        row, created = repo.claim_llm_extraction(
            session,
            filing_id=filing.id,
            announcement_id=key,
            source=filing.source,
            model=self.client.model,
            prompt_version=self.prompt_version,
            mode="SYNC",
            request_fingerprint=request_fingerprint(
                self.client.model, self.prompt_version, page_text
            ),
        )

        if not created and not force:
            # Already sent. This is the dedupe guarantee, and it is enforced by a
            # unique constraint rather than by bookkeeping, so a crash mid-flight
            # cannot cause a re-send.
            self.stats.cached += 1
            return _verdict_from_row(row)

        call = self.client.extract_event(
            title=title, page_text=page_text, page_slice=page_slice
        )
        row.page_slice = page_slice
        self.stats.calls += 1
        self.stats.cost_usd += call.cost_usd

        if not call.ok or call.verdict is None:
            self.stats.failed += 1
            repo.record_llm_result(
                session, row, call=call, status="FAILED",
                error=call.error, persist_raw=self.persist_raw,
            )
            return None

        repo.record_llm_result(
            session, row, call=call, verdict=call.verdict,
            status="OK", persist_raw=self.persist_raw,
        )
        return call.verdict


def _verdict_from_row(row: Any) -> Optional[LlmVerdict]:
    if row is None or row.status != "OK" or not row.event_class:
        return None
    return LlmVerdict(
        event_class=row.event_class,
        holder=row.holder,
        holder_type=row.holder_type or "UNKNOWN",
        stake_pct=row.stake_pct,
        effective_date=row.effective_date,
        confidence=row.llm_confidence or 0.0,
    )
