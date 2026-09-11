"""SQLAlchemy ORM models.

The schema targets PostgreSQL but stays SQLite-compatible so the test suite can
run against an in-memory database: JSON columns use a variant that resolves to
``JSONB`` on PostgreSQL and plain ``JSON`` elsewhere, and enumerations are plain
strings guarded by CHECK constraints rather than native PG enums (which would
otherwise require a migration for every new category).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

# JSONB on PostgreSQL, JSON everywhere else.
JSONType = JSON().with_variant(JSONB(), "postgresql")

CATEGORIES = (
    "IPO",
    "FPO",
    "QIP",
    "RightsIssue",
    "OFS",
    "BlockDeal",
    "InvestorExit",
    "PromoterSale",
    "FundRaise",
    "PreferentialAllotment",
    "Other",
)
PRIORITIES = ("HIGH", "MEDIUM", "LOW")
SOURCES = ("BSE", "NSE", "SEBI", "IR")
TRANSACTION_TYPES = (
    "PRIMARY_ISSUE",
    "SECONDARY_SALE",
    "PLEDGE",
    "LOCK_IN_EXPIRY",
    "BUYBACK",
    "OTHER",
)

# Forward-calendar deadline types. Each maps to exactly one rule in
# bse_monitor/triggers/rules.py.
TRIGGER_TYPES = (
    "ANCHOR_LOCKIN_30D",
    "ANCHOR_LOCKIN_90D",
    "PREIPO_LOCKIN_6M",
    "PROMOTER_EXCESS_LOCKIN_6M",
    "PROMOTER_MPC_LOCKIN_18M",
    "CAPEX_OBJECTS_1Y",
    "CAPEX_OBJECTS_3Y",
    "MPS_COMPLIANCE_25PCT",
    "QIP_RESOLUTION_EXPIRY_365D",
    "TRADING_WINDOW_REOPEN_48H",
)
TRIGGER_STATUSES = ("PENDING", "FIRED", "SUPERSEDED", "CANCELLED")
# What the offset is counted from.
ANCHOR_BASES = ("ALLOTMENT", "LISTING", "RESOLUTION", "RESULTS_DECLARED", "FILING", "WATCHLIST")
SUBJECT_TYPES = ("ANCHOR_INVESTOR", "PREIPO_SHAREHOLDER", "PROMOTER", "COMPANY", "UNKNOWN")

LLM_MODES = ("SYNC", "BATCH")
LLM_STATUSES = ("PENDING", "SUBMITTED", "OK", "FAILED", "SKIPPED")


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    bse_code = Column(String(16), unique=True, index=True)
    nse_symbol = Column(String(32), index=True)
    isin = Column(String(12), index=True)
    name = Column(String(512), nullable=False)
    # Lower-cased, suffix-stripped name used for fuzzy resolution across sources.
    normalized_name = Column(String(512), nullable=False, index=True)
    sector = Column(String(128))
    industry = Column(String(128))
    market_cap_cr = Column(Numeric(18, 2))
    is_active = Column(Boolean, default=True, nullable=False)
    extra = Column(JSONType, default=dict)

    filings = relationship("EventFiling", back_populates="company")
    promoters = relationship("Promoter", back_populates="company")
    upcoming_triggers = relationship("UpcomingTrigger", back_populates="company")

    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_companies_normalized_name"),
        Index("ix_companies_name_lower", "normalized_name"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Company {self.bse_code} {self.name!r}>"


class EventFiling(Base, TimestampMixin):
    __tablename__ = "event_filings"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True)

    source = Column(String(8), nullable=False, default="BSE")
    # Exchange-assigned identifier; the primary idempotency key for re-scrapes.
    source_filing_id = Column(String(128), index=True)
    # sha256 over (company, normalised headline, filing date) — catches the same
    # announcement arriving through a second source or with a reworded id.
    content_hash = Column(String(64), nullable=False, unique=True, index=True)

    headline = Column(Text, nullable=False)
    body_text = Column(Text)
    # headline + body + PDF text; what the classifier actually reads.
    search_text = Column(Text)

    category = Column(String(32), nullable=False, default="Other", index=True)
    subcategory = Column(String(64))
    secondary_categories = Column(JSONType, default=list)
    classification_confidence = Column(Float, default=0.0, nullable=False)
    classification_method = Column(String(32), default="rules")
    classification_evidence = Column(JSONType, default=dict)

    opportunity_score = Column(Integer, default=0, nullable=False, index=True)
    score_breakdown = Column(JSONType, default=dict)
    priority = Column(String(8), default="LOW", nullable=False, index=True)
    certainty = Column(String(16), default="unknown")

    filing_datetime = Column(DateTime(timezone=True), index=True)
    dissemination_datetime = Column(DateTime(timezone=True))
    filing_date = Column(Date, index=True)

    exchange_url = Column(Text)
    pdf_url = Column(Text)
    pdf_processed = Column(Boolean, default=False, nullable=False)

    is_duplicate = Column(Boolean, default=False, nullable=False)
    duplicate_of_id = Column(Integer, ForeignKey("event_filings.id", ondelete="SET NULL"))

    raw_payload = Column(JSONType, default=dict)
    scrape_run_id = Column(Integer, ForeignKey("scrape_runs.id", ondelete="SET NULL"))

    company = relationship("Company", back_populates="filings")
    documents = relationship("FilingDocument", back_populates="filing", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="filing", cascade="all, delete-orphan")
    investor_links = relationship(
        "FilingInvestor", back_populates="filing", cascade="all, delete-orphan"
    )
    promoter_links = relationship(
        "FilingPromoter", back_populates="filing", cascade="all, delete-orphan"
    )
    alerts = relationship("DailyAlert", back_populates="filing", cascade="all, delete-orphan")
    # No delete-orphan: a deadline outlives the filing that revealed it.
    derived_triggers = relationship("UpcomingTrigger", back_populates="source_filing")
    llm_extractions = relationship(
        "LlmExtraction", back_populates="filing", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "category IN " + str(CATEGORIES), name="ck_event_filings_category"
        ),
        CheckConstraint("priority IN " + str(PRIORITIES), name="ck_event_filings_priority"),
        CheckConstraint("source IN " + str(SOURCES), name="ck_event_filings_source"),
        CheckConstraint(
            "opportunity_score >= 0 AND opportunity_score <= 100",
            name="ck_event_filings_score_range",
        ),
        UniqueConstraint("source", "source_filing_id", name="uq_filings_source_id"),
        Index("ix_filings_cat_score_date", "category", "opportunity_score", "filing_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<EventFiling {self.id} {self.category} score={self.opportunity_score}>"


class FilingDocument(Base, TimestampMixin):
    __tablename__ = "filing_documents"

    id = Column(Integer, primary_key=True)
    filing_id = Column(
        Integer, ForeignKey("event_filings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url = Column(Text, nullable=False)
    local_path = Column(Text)
    # Content digest, so the same attachment linked from two filings downloads once.
    sha256 = Column(String(64), index=True)
    byte_size = Column(Integer)
    page_count = Column(Integer)
    extraction_method = Column(String(32))  # pdfplumber | pymupdf | ocr | none
    ocr_used = Column(Boolean, default=False, nullable=False)
    text_chars = Column(Integer, default=0)
    extracted_text = Column(Text)
    tables = Column(JSONType, default=list)
    download_status = Column(String(16), default="PENDING")  # PENDING|OK|FAILED|SKIPPED
    error = Column(Text)

    filing = relationship("EventFiling", back_populates="documents")

    __table_args__ = (
        UniqueConstraint("filing_id", "url", name="uq_document_filing_url"),
    )


class Investor(Base, TimestampMixin):
    __tablename__ = "investors"

    id = Column(Integer, primary_key=True)
    name = Column(String(256), nullable=False)
    normalized_name = Column(String(256), nullable=False, unique=True, index=True)
    investor_type = Column(String(32), default="Unknown")
    country = Column(String(8))
    is_marquee = Column(Boolean, default=False, nullable=False)
    is_tracked = Column(Boolean, default=True, nullable=False)
    aliases = Column(JSONType, default=list)
    # Set when the entity was harvested from text rather than the seed registry.
    discovered = Column(Boolean, default=False, nullable=False)

    filing_links = relationship("FilingInvestor", back_populates="investor")


class Promoter(Base, TimestampMixin):
    __tablename__ = "promoters"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name = Column(String(256), nullable=False)
    normalized_name = Column(String(256), nullable=False, index=True)
    role = Column(String(64))  # Promoter | Promoter Group | Person Acting in Concert
    is_active = Column(Boolean, default=True, nullable=False)

    company = relationship("Company", back_populates="promoters")
    filing_links = relationship("FilingPromoter", back_populates="promoter")

    __table_args__ = (
        UniqueConstraint("company_id", "normalized_name", name="uq_promoter_company_name"),
    )


class FilingInvestor(Base):
    """Association between a filing and an investor mentioned in it."""

    __tablename__ = "filing_investors"

    filing_id = Column(Integer, ForeignKey("event_filings.id", ondelete="CASCADE"), primary_key=True)
    investor_id = Column(Integer, ForeignKey("investors.id", ondelete="CASCADE"), primary_key=True)
    mention_context = Column(Text)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    filing = relationship("EventFiling", back_populates="investor_links")
    investor = relationship("Investor", back_populates="filing_links")


class FilingPromoter(Base):
    __tablename__ = "filing_promoters"

    filing_id = Column(Integer, ForeignKey("event_filings.id", ondelete="CASCADE"), primary_key=True)
    promoter_id = Column(Integer, ForeignKey("promoters.id", ondelete="CASCADE"), primary_key=True)
    mention_context = Column(Text)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    filing = relationship("EventFiling", back_populates="promoter_links")
    promoter = relationship("Promoter", back_populates="filing_links")


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    filing_id = Column(
        Integer, ForeignKey("event_filings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    investor_id = Column(Integer, ForeignKey("investors.id", ondelete="SET NULL"))
    promoter_id = Column(Integer, ForeignKey("promoters.id", ondelete="SET NULL"))

    transaction_type = Column(String(32), default="OTHER", nullable=False)
    direction = Column(String(8))  # BUY | SELL | ISSUE
    security_type = Column(String(32))  # EQUITY | WARRANT | NCD | FCCB | GDR | ADR

    num_shares = Column(Numeric(20, 2))
    price_per_share = Column(Numeric(18, 4))
    amount_inr = Column(Numeric(20, 2))
    percent_of_equity = Column(Float)
    issue_size_inr = Column(Numeric(20, 2))

    lock_in_expiry_date = Column(Date, index=True)
    board_meeting_date = Column(Date, index=True)
    record_date = Column(Date)

    currency = Column(String(8), default="INR")
    extraction_confidence = Column(Float, default=0.0)
    extra = Column(JSONType, default=dict)

    filing = relationship("EventFiling", back_populates="transactions")

    __table_args__ = (
        CheckConstraint(
            "transaction_type IN " + str(TRANSACTION_TYPES),
            name="ck_transactions_type",
        ),
    )


class DailyAlert(Base, TimestampMixin):
    __tablename__ = "daily_alerts"

    id = Column(Integer, primary_key=True)
    alert_date = Column(Date, nullable=False, index=True)
    filing_id = Column(Integer, ForeignKey("event_filings.id", ondelete="CASCADE"), index=True)
    # A calendar deadline can raise an alert with no filing behind it, so this
    # and filing_id are both nullable and exactly one is set per alert.
    trigger_id = Column(Integer, ForeignKey("upcoming_triggers.id", ondelete="CASCADE"), index=True)
    priority = Column(String(8), nullable=False, default="LOW")
    channel = Column(String(16), nullable=False)  # email|slack|teams|csv
    status = Column(String(16), nullable=False, default="PENDING")  # PENDING|SENT|FAILED|SKIPPED
    title = Column(Text)
    body = Column(Text)
    sent_at = Column(DateTime(timezone=True))
    error = Column(Text)
    # (filing, channel) for filing alerts, or (trigger, horizon, channel) for
    # calendar reminders — the horizon component is what lets a T-30 and a T-7
    # reminder for the same deadline coexist without either suppressing the other.
    dedupe_key = Column(String(96), nullable=False, unique=True, index=True)

    filing = relationship("EventFiling", back_populates="alerts")
    trigger = relationship("UpcomingTrigger", back_populates="alerts")

    __table_args__ = (
        CheckConstraint("priority IN " + str(PRIORITIES), name="ck_alerts_priority"),
    )


class ScrapeRun(Base):
    """Audit trail + checkpoint store for incremental scraping."""

    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True)
    source = Column(String(8), nullable=False, index=True)
    run_started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    run_finished_at = Column(DateTime(timezone=True))
    status = Column(String(16), default="RUNNING", nullable=False)  # RUNNING|OK|PARTIAL|FAILED
    from_date = Column(Date)
    to_date = Column(Date)
    records_seen = Column(Integer, default=0)
    records_new = Column(Integer, default=0)
    records_duplicate = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    checkpoint = Column(JSONType, default=dict)
    notes = Column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    entity_type = Column(String(32), nullable=False, index=True)
    entity_id = Column(String(64), index=True)
    action = Column(String(32), nullable=False)
    actor = Column(String(64), default="pipeline")
    details = Column(JSONType, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UpcomingTrigger(Base, TimestampMixin):
    """A dated, forward-looking deadline derived from a filing.

    This is deliberately NOT hung off ``Transaction``: ``replace_transactions()``
    deletes and re-inserts transaction rows on every re-extraction, so a calendar
    keyed to them would lose identity — and its alert history — on every run.
    Identity here comes from the *filing facts* via ``dedupe_key``, so re-deriving
    the same deadline finds the same row.

    ``days_to_trigger`` is never stored. It is computed against an injected
    ``as_of`` so queries stay deterministic in tests, and because date arithmetic
    differs between SQLite and PostgreSQL a stored/hybrid column would pass tests
    and fail in production.
    """

    __tablename__ = "upcoming_triggers"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: deleting a filing must never delete a live deadline.
    source_filing_id = Column(
        Integer, ForeignKey("event_filings.id", ondelete="SET NULL"), index=True
    )

    trigger_type = Column(String(48), nullable=False, index=True)
    # Whose shares are released. normalize_name() of the holder, or "" for a
    # company-wide deadline. NOT NULL because NULL never equals NULL in a unique
    # index — a nullable key component would silently permit duplicates.
    subject_key = Column(String(128), nullable=False, default="", server_default="")
    subject_name = Column(String(256))
    subject_type = Column(String(32), nullable=False, default="COMPANY")

    anchor_date = Column(Date, nullable=False, index=True)
    anchor_basis = Column(String(32), nullable=False)
    # Only populated for the 48-hour trading-window rule, which needs wall-clock.
    anchor_datetime = Column(DateTime(timezone=True))

    trigger_date = Column(Date, nullable=False, index=True)
    trigger_datetime = Column(DateTime(timezone=True))
    offset_days = Column(Integer)

    rule_version = Column(String(16), nullable=False, default="v1")
    status = Column(String(16), nullable=False, default="PENDING", index=True)
    superseded_by_id = Column(
        Integer, ForeignKey("upcoming_triggers.id", ondelete="SET NULL")
    )

    confidence = Column(Float, default=0.0, nullable=False)
    num_shares = Column(Numeric(20, 2))
    percent_of_equity = Column(Float)
    amount_inr = Column(Numeric(20, 2))

    evidence = Column(JSONType, default=dict)
    notes = Column(Text)
    # When this deadline first became knowable — the filing date behind it. The
    # point-in-time guard the ML panel filters on, so a model never sees a
    # deadline before the market could have.
    known_from_date = Column(Date, index=True)
    fired_at = Column(DateTime(timezone=True))

    dedupe_key = Column(String(64), nullable=False, unique=True, index=True)

    company = relationship("Company", back_populates="upcoming_triggers")
    source_filing = relationship("EventFiling", back_populates="derived_triggers")
    alerts = relationship("DailyAlert", back_populates="trigger")

    __table_args__ = (
        CheckConstraint("trigger_type IN " + str(TRIGGER_TYPES), name="ck_triggers_type"),
        CheckConstraint("status IN " + str(TRIGGER_STATUSES), name="ck_triggers_status"),
        CheckConstraint("anchor_basis IN " + str(ANCHOR_BASES), name="ck_triggers_basis"),
        CheckConstraint("subject_type IN " + str(SUBJECT_TYPES), name="ck_triggers_subject"),
        # Every rule is a forward offset; a backwards deadline is a bug.
        CheckConstraint("trigger_date >= anchor_date", name="ck_triggers_forward"),
        UniqueConstraint(
            "company_id",
            "trigger_type",
            "anchor_date",
            "subject_key",
            "rule_version",
            name="uq_triggers_natural",
        ),
        Index("ix_triggers_status_date", "status", "trigger_date"),
        Index("ix_triggers_company_type", "company_id", "trigger_type"),
    )

    def days_to(self, as_of: Optional[dt.date] = None) -> int:
        """Days from ``as_of`` to the deadline; negative once it has passed.

        A method rather than a property: a zero-argument property would have to
        call ``date.today()`` and make every test time-dependent.
        """
        return (self.trigger_date - (as_of or dt.date.today())).days

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<UpcomingTrigger {self.trigger_type} {self.trigger_date} {self.status}>"


class LlmExtraction(Base, TimestampMixin):
    """One LLM pass over one announcement.

    ``announcement_id`` is uniquely constrained, which is what makes "a filing is
    never sent twice" a database guarantee rather than application bookkeeping —
    a crash between the check and the call cannot cause a re-send, and therefore
    cannot cause a double charge.
    """

    __tablename__ = "llm_extractions"

    id = Column(Integer, primary_key=True)
    filing_id = Column(
        Integer, ForeignKey("event_filings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    announcement_id = Column(String(160), nullable=False, unique=True, index=True)
    source = Column(String(8), nullable=False, default="BSE")

    model = Column(String(64), nullable=False)
    prompt_version = Column(String(16), nullable=False, default="v1")
    mode = Column(String(16), nullable=False, default="SYNC")
    status = Column(String(16), nullable=False, default="PENDING", index=True)

    batch_id = Column(String(96), index=True)
    # What batch results are keyed by. Results arrive in arbitrary order.
    custom_id = Column(String(96), index=True)
    request_fingerprint = Column(String(64))
    # "pages:1-3" normally; "char_budget" when only joined cached text existed.
    page_slice = Column(String(32))

    event_class = Column(String(32))
    holder = Column(String(256))
    holder_normalized = Column(String(256), index=True)
    holder_type = Column(String(32))
    stake_pct = Column(Float)
    effective_date = Column(Date)
    llm_confidence = Column(Float)

    parsed = Column(JSONType, default=dict)
    raw_response = Column(JSONType, default=dict)

    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cache_creation_input_tokens = Column(Integer, default=0)
    cache_read_input_tokens = Column(Integer, default=0)
    cost_usd = Column(Numeric(12, 6), default=0)

    latency_ms = Column(Integer)
    attempts = Column(Integer, default=0)
    error = Column(Text)
    requested_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    filing = relationship("EventFiling", back_populates="llm_extractions")

    __table_args__ = (
        CheckConstraint("mode IN " + str(LLM_MODES), name="ck_llm_mode"),
        CheckConstraint("status IN " + str(LLM_STATUSES), name="ck_llm_status"),
        CheckConstraint("source IN " + str(SOURCES), name="ck_llm_source"),
        Index("ix_llm_status_model", "status", "model"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<LlmExtraction {self.announcement_id} {self.status}>"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


__all__: list[str] = [
    "Base",
    "Company",
    "EventFiling",
    "FilingDocument",
    "Investor",
    "Promoter",
    "FilingInvestor",
    "FilingPromoter",
    "Transaction",
    "DailyAlert",
    "ScrapeRun",
    "AuditLog",
    "UpcomingTrigger",
    "LlmExtraction",
    "CATEGORIES",
    "TRIGGER_TYPES",
    "TRIGGER_STATUSES",
    "ANCHOR_BASES",
    "SUBJECT_TYPES",
    "LLM_MODES",
    "LLM_STATUSES",
    "PRIORITIES",
    "TRANSACTION_TYPES",
    "utcnow",
]


def _unused(_: Any) -> None:  # pragma: no cover
    return None
