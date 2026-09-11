"""End-to-end orchestration: scrape → normalise → PDF → classify → score → persist → alert.

The pipeline is written to be re-runnable over any window without producing
duplicates or double alerts, which is what makes both the 15-minute intraday
cadence and the weekly reconciliation sweep safe to run against the same data.

Resilience properties:

* **Incremental** — the scrape window starts from the last successful run's
  checkpoint, with a configurable overlap so a filing disseminated during a run
  is not skipped by the next one.
* **Checkpointed** — every run writes a ``scrape_runs`` row before it fetches
  anything, and updates it at the end. A crash leaves a RUNNING row that the
  next run's window calculation ignores, so it re-covers the same period.
* **Idempotent** — the content hash gates inserts; a re-scraped filing updates
  in place and never re-queues an alert (the alert dedupe key is per filing and
  channel).
* **Degradable** — a PDF that will not download, an OCR binary that is missing,
  or a Slack webhook that is down each downgrade the run to PARTIAL instead of
  failing it.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from .alerts.dispatcher import AlertDispatcher, dispatcher_from_config
from .classifier.ml import MLClassifier, blend
from .classifier.rules import RuleClassifier
from .classifier.scoring import OpportunityScorer, ScoreInput, scorer_from_config
from .config import Config, load_investors
from .database import repository as repo
from .database.models import EventFiling
from .parser.entities import ExtractionResult, InvestorMatcher, extract_all
from .parser.text import clean_text
from .pdf_processor.downloader import PdfDownloader
from .pdf_processor.extractor import PdfExtractor, extractor_from_config
from .pdf_processor.tables import parse_tables, summarise
from .scraper.base import client_from_config
from .scraper.bse import BSE_HEADERS, BseScraper
from .scraper.models import RawFiling

log = logging.getLogger(__name__)

# Re-cover this much of the previous window. BSE occasionally back-dates a
# dissemination timestamp, so a zero-overlap window loses filings.
CHECKPOINT_OVERLAP_DAYS = 1


@dataclass
class RunStats:
    source: str = "BSE"
    seen: int = 0
    new: int = 0
    updated: int = 0
    duplicates: int = 0
    pdfs_processed: int = 0
    pdfs_failed: int = 0
    alerts_queued: int = 0
    alerts_sent: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "seen": self.seen,
            "new": self.new,
            "updated": self.updated,
            "duplicates": self.duplicates,
            "pdfs_processed": self.pdfs_processed,
            "pdfs_failed": self.pdfs_failed,
            "alerts_queued": self.alerts_queued,
            "alerts_sent": self.alerts_sent,
            "errors": self.errors[:20],
        }


class MonitorPipeline:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.rule_classifier = RuleClassifier()
        self.scorer: OpportunityScorer = scorer_from_config(config)
        self.dispatcher: AlertDispatcher = dispatcher_from_config(config)
        self.matcher = InvestorMatcher(load_investors())
        self.nlp = self._load_spacy()

        self.ml: Optional[MLClassifier] = None
        if config.get("classifier.use_ml_model", False):
            candidate = MLClassifier(config.get("classifier.ml_model_path"))
            self.ml = candidate if candidate.available else None
            if self.ml is None:
                log.warning("ML model requested but unavailable; running rules-only")

        self.pdf_enabled = bool(config.get("pdf.enabled", True))
        self.pdf_extractor: Optional[PdfExtractor] = (
            extractor_from_config(config) if self.pdf_enabled else None
        )
        self.min_confidence = float(config.get("classifier.min_confidence", 0.35))

    def _load_spacy(self) -> Any:
        """spaCy is optional; entity discovery falls back to regex without it."""
        model_name = self.config.get("classifier.spacy_model", "en_core_web_sm")
        try:
            import spacy

            return spacy.load(model_name)
        except Exception as exc:
            log.info(
                "spaCy model unavailable; using regex entity discovery",
                extra={"model": model_name, "error": str(exc)},
            )
            return None

    # ------------------------------------------------------------------
    # Window calculation
    # ------------------------------------------------------------------
    def resolve_window(
        self,
        session: Session,
        source: str,
        from_date: Optional[dt.date] = None,
        to_date: Optional[dt.date] = None,
    ) -> Tuple[dt.date, dt.date]:
        end = to_date or dt.date.today()
        if from_date:
            return from_date, end
        last = repo.last_successful_run(session, source)
        if last and last.to_date:
            start = last.to_date - dt.timedelta(days=CHECKPOINT_OVERLAP_DAYS)
        else:
            start = end - dt.timedelta(days=int(self.config.get("scraper.lookback_days", 3)))
        return min(start, end), end

    # ------------------------------------------------------------------
    # Per-filing processing
    # ------------------------------------------------------------------
    def process_filing(
        self, session: Session, raw: RawFiling, stats: RunStats, run_id: Optional[int] = None
    ) -> Optional[EventFiling]:
        company_key = raw.bse_code or raw.nse_symbol or raw.company_name
        digest = repo.content_hash(
            company_key=raw.company_name or company_key,
            headline=raw.headline,
            filing_date=raw.filing_date,
        )

        existing = repo.find_filing(session, content_hash_value=digest)
        # Re-processing an already-enriched filing buys nothing and costs a PDF
        # download, so short-circuit unless the earlier pass never got the PDF.
        if existing is not None and existing.pdf_processed:
            stats.duplicates += 1
            return existing

        company = repo.upsert_company(
            session,
            name=raw.company_name or "Unknown",
            bse_code=raw.bse_code,
            nse_symbol=raw.nse_symbol,
            isin=raw.isin,
        )

        base_text = clean_text(raw.combined_text())
        pdf_text, document_meta = self._process_pdf(session, raw, stats)
        search_text = "\n\n".join(part for part in (base_text, pdf_text) if part).strip()

        classification = self.rule_classifier.classify(search_text, self.min_confidence)
        category, confidence, method = classification.category, classification.confidence, classification.method
        if self.ml is not None:
            category, confidence, method = blend(
                classification.category, classification.confidence, self.ml.predict(search_text)
            )

        extraction = extract_all(
            search_text, matcher=self.matcher, nlp=self.nlp, discover_investors=True
        )
        extraction = self._merge_table_signals(extraction, document_meta)

        score_result = self.scorer.score(
            ScoreInput(
                category=category,
                confidence=confidence,
                certainty=classification.certainty,
                amount_inr=extraction.amount_inr,
                percent_of_equity=extraction.percent_of_equity,
                marquee_investor=extraction.marquee_investor,
                promoter_involved=extraction.promoter_involved,
                lock_in_expiry_date=extraction.lock_in_expiry_date,
                filing_date=raw.filing_date,
            )
        )

        filing, created = repo.upsert_filing(
            session,
            {
                "content_hash": digest,
                "company_id": company.id,
                "source": raw.source,
                "source_filing_id": raw.source_filing_id,
                "headline": raw.headline or "(no headline)",
                "body_text": raw.body_text,
                "search_text": search_text[:1_000_000],
                "category": category,
                "subcategory": raw.subcategory_hint,
                "secondary_categories": classification.secondary,
                "classification_confidence": confidence,
                "classification_method": method,
                "classification_evidence": classification.evidence,
                "opportunity_score": score_result.score,
                "score_breakdown": score_result.breakdown,
                "priority": score_result.priority,
                "certainty": classification.certainty,
                "filing_datetime": raw.filing_datetime,
                "filing_date": raw.filing_date,
                "dissemination_datetime": raw.dissemination_datetime,
                "exchange_url": raw.exchange_url,
                "pdf_url": raw.pdf_url,
                "pdf_processed": bool(pdf_text),
                "raw_payload": raw.raw_payload,
                "scrape_run_id": run_id,
            },
        )

        if document_meta:
            repo.upsert_document(session, filing.id, document_meta.pop("url"), **document_meta)

        self._persist_entities(session, filing, company.id, extraction)
        self._persist_transaction(session, filing, company.id, category, extraction)

        if created:
            stats.new += 1
        else:
            stats.updated += 1
        return filing

    # ------------------------------------------------------------------
    def _process_pdf(
        self, session: Session, raw: RawFiling, stats: RunStats
    ) -> Tuple[str, Dict[str, Any]]:
        """Download and extract the attachment; never fatal."""
        if not (self.pdf_enabled and self.pdf_extractor and raw.pdf_url):
            return "", {}

        candidates: Sequence[str] = raw.raw_payload.get("_pdf_candidates") or [raw.pdf_url]
        client = client_from_config(self.config, BSE_HEADERS)
        downloader = PdfDownloader(
            client,
            self.config.resolve_path("pdf.download_dir", "./data/pdfs"),
            int(self.config.get("pdf.max_bytes", 50 * 1024 * 1024)),
        )
        try:
            result = downloader.download(candidates, raw.filing_date)
        finally:
            client.close()

        if not result.ok or result.path is None:
            stats.pdfs_failed += 1
            return "", {
                "url": raw.pdf_url,
                "download_status": result.status,
                "error": result.error,
            }

        # Same attachment seen before? Reuse the stored text instead of re-OCRing.
        cached = repo.document_by_sha(session, result.sha256 or "")
        if cached is not None and cached.extracted_text:
            stats.pdfs_processed += 1
            return cached.extracted_text, {
                "url": raw.pdf_url,
                "local_path": cached.local_path,
                "sha256": cached.sha256,
                "byte_size": cached.byte_size,
                "page_count": cached.page_count,
                "extraction_method": cached.extraction_method,
                "ocr_used": cached.ocr_used,
                "text_chars": cached.text_chars,
                "extracted_text": cached.extracted_text,
                "tables": cached.tables,
                "download_status": "OK",
            }

        output = self.pdf_extractor.extract(result.path)
        stats.pdfs_processed += 1
        if output.error and not output.text:
            stats.pdfs_failed += 1
            stats.errors.append(f"pdf:{raw.source_filing_id}:{output.error}")

        return output.text, {
            "url": raw.pdf_url,
            "local_path": str(result.path),
            "sha256": result.sha256,
            "byte_size": result.byte_size,
            "page_count": output.page_count,
            "extraction_method": output.method,
            "ocr_used": output.ocr_used,
            "text_chars": output.char_count,
            "extracted_text": output.text[:1_000_000],
            "tables": output.tables[:50],
            "download_status": "OK",
            "error": output.error,
        }

    @staticmethod
    def _merge_table_signals(
        extraction: ExtractionResult, document_meta: Dict[str, Any]
    ) -> ExtractionResult:
        """Let table data fill gaps the free-text extractor could not."""
        tables = document_meta.get("tables") or []
        if not tables:
            return extraction
        rows = parse_tables(tables)
        if not rows:
            return extraction
        stats = summarise(rows)
        if extraction.num_shares is None and stats.get("total_shares"):
            extraction.num_shares = float(stats["total_shares"])
        if extraction.percent_of_equity is None and stats.get("max_percent"):
            extraction.percent_of_equity = float(stats["max_percent"])
        if extraction.price_per_share is None and stats.get("prices"):
            extraction.price_per_share = float(stats["prices"][-1])
        return extraction

    def _persist_entities(
        self, session: Session, filing: EventFiling, company_id: int, extraction: ExtractionResult
    ) -> None:
        for mention in extraction.investors:
            investor = repo.upsert_investor(
                session,
                name=mention.name,
                investor_type=mention.entity_type,
                is_marquee=mention.is_marquee,
                discovered=mention.discovered,
            )
            repo.link_investor(
                session, filing.id, investor.id, mention.context, mention.confidence
            )
        for mention in extraction.promoters:
            promoter = repo.upsert_promoter(session, company_id, mention.name)
            repo.link_promoter(
                session, filing.id, promoter.id, mention.context, mention.confidence
            )

    @staticmethod
    def _transaction_shape(category: str) -> Tuple[str, Optional[str]]:
        if category in {"QIP", "FPO", "IPO", "RightsIssue", "PreferentialAllotment", "FundRaise"}:
            return "PRIMARY_ISSUE", "ISSUE"
        if category in {"OFS", "BlockDeal", "InvestorExit", "PromoterSale"}:
            return "SECONDARY_SALE", "SELL"
        return "OTHER", None

    def _persist_transaction(
        self,
        session: Session,
        filing: EventFiling,
        company_id: int,
        category: str,
        extraction: ExtractionResult,
    ) -> None:
        has_content = any(
            value is not None
            for value in (
                extraction.amount_inr,
                extraction.num_shares,
                extraction.price_per_share,
                extraction.percent_of_equity,
                extraction.lock_in_expiry_date,
                extraction.board_meeting_date,
            )
        )
        if not has_content:
            return
        transaction_type, direction = self._transaction_shape(category)
        if extraction.lock_in_expiry_date and transaction_type == "OTHER":
            transaction_type = "LOCK_IN_EXPIRY"
        repo.replace_transactions(
            session,
            filing.id,
            [
                {
                    "company_id": company_id,
                    "transaction_type": transaction_type,
                    "direction": direction,
                    "security_type": "EQUITY",
                    "num_shares": extraction.num_shares,
                    "price_per_share": extraction.price_per_share,
                    "amount_inr": extraction.amount_inr,
                    "percent_of_equity": extraction.percent_of_equity,
                    # Issue size is a primary-market concept: for a raise the
                    # deal amount *is* the issue size, while the identical
                    # "aggregating Rs X crore" phrasing in a block-deal
                    # disclosure is a transaction value and nothing more.
                    "issue_size_inr": (
                        extraction.amount_inr if transaction_type == "PRIMARY_ISSUE" else None
                    ),
                    "lock_in_expiry_date": extraction.lock_in_expiry_date,
                    "board_meeting_date": extraction.board_meeting_date,
                    "record_date": extraction.record_date,
                    "extraction_confidence": filing.classification_confidence,
                    "extra": {"amount_raw": extraction.amount_raw},
                }
            ],
        )

    # ------------------------------------------------------------------
    # Run entry points
    # ------------------------------------------------------------------
    def run(
        self,
        session: Session,
        from_date: Optional[dt.date] = None,
        to_date: Optional[dt.date] = None,
        source: str = "BSE",
        send_alerts: bool = True,
        limit: Optional[int] = None,
    ) -> RunStats:
        stats = RunStats(source=source)
        start, end = self.resolve_window(session, source, from_date, to_date)
        run = repo.start_run(session, source, start, end)
        session.commit()
        log.info("Run started", extra={"source": source, "from": str(start), "to": str(end)})

        processed: List[EventFiling] = []
        scraper = self._build_scraper(source)
        try:
            for index, raw in enumerate(self._iter_raw(scraper, source, start, end)):
                if limit is not None and index >= limit:
                    break
                stats.seen += 1
                try:
                    filing = self.process_filing(session, raw, stats, run.id)
                    if filing is not None:
                        processed.append(filing)
                except Exception as exc:  # one bad filing must not kill the run
                    stats.errors.append(f"{raw.source_filing_id}: {exc}")
                    log.exception(
                        "Filing failed", extra={"filing_id": raw.source_filing_id}
                    )
                    session.rollback()
                if stats.seen % 50 == 0:
                    session.commit()
                    log.info("Progress", extra=stats.as_dict())
            session.commit()
        except Exception as exc:
            stats.errors.append(f"run: {exc}")
            log.exception("Run failed")
            session.rollback()
            repo.finish_run(session, run, "FAILED", errors=len(stats.errors), notes=str(exc)[:500])
            session.commit()
            raise
        finally:
            scraper.close()

        if send_alerts and processed:
            stats.alerts_queued = self.dispatcher.queue(session, processed)
            session.commit()
            delivery = self.dispatcher.flush(session)
            stats.alerts_sent = delivery["sent"]
            self.dispatcher.export_csv(processed, end)
            session.commit()

        status = "PARTIAL" if stats.errors else "OK"
        repo.finish_run(
            session,
            run,
            status,
            records_seen=stats.seen,
            records_new=stats.new,
            records_duplicate=stats.duplicates,
            errors=len(stats.errors),
            checkpoint={"last_to_date": end.isoformat(), **stats.as_dict()},
        )
        session.commit()
        log.info("Run finished", extra={"status": status, **stats.as_dict()})
        return stats

    def _build_scraper(self, source: str) -> Any:
        if source == "NSE":
            from .scraper.nse import NseScraper

            return NseScraper(self.config)
        if source == "SEBI":
            from .scraper.sebi import SebiScraper

            return SebiScraper(self.config)
        return BseScraper(self.config)

    @staticmethod
    def _iter_raw(
        scraper: Any, source: str, start: dt.date, end: dt.date
    ) -> Iterable[RawFiling]:
        if source == "SEBI":
            return scraper.fetch_filings(start, end)
        return scraper.fetch_announcements(start, end)

    def process_batch(
        self, session: Session, raws: Sequence[RawFiling], send_alerts: bool = False
    ) -> Tuple[RunStats, List[EventFiling]]:
        """Process already-fetched filings. Used by tests and by replays."""
        stats = RunStats()
        processed: List[EventFiling] = []
        for raw in raws:
            stats.seen += 1
            filing = self.process_filing(session, raw, stats)
            if filing is not None:
                processed.append(filing)
        session.commit()
        if send_alerts and processed:
            stats.alerts_queued = self.dispatcher.queue(session, processed)
            stats.alerts_sent = self.dispatcher.flush(session)["sent"]
            self.dispatcher.export_csv(processed)
            session.commit()
        return stats, processed
