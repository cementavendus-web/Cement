"""Persistence: normalisation, dedupe, upserts, alert queueing, checkpoints."""

from __future__ import annotations

import datetime as dt

import pytest

from bse_monitor.database import repository as repo
from bse_monitor.database.models import Company, DailyAlert, EventFiling, Transaction


def test_normalize_name_collapses_suffixes() -> None:
    assert repo.normalize_name("Reliance Industries Ltd.") == repo.normalize_name(
        "RELIANCE INDUSTRIES LIMITED"
    )
    assert repo.normalize_name("") == ""


def test_content_hash_is_stable_and_discriminating() -> None:
    day = dt.date(2026, 9, 9)
    first = repo.content_hash("Vantage Infratech Ltd", "Board approves QIP", day)
    same = repo.content_hash("VANTAGE INFRATECH LIMITED", "board approves qip", day)
    other_day = repo.content_hash("Vantage Infratech Ltd", "Board approves QIP", dt.date(2026, 9, 10))
    assert first == same
    assert first != other_day


def test_content_hash_ignores_xbrl_suffix() -> None:
    day = dt.date(2026, 9, 9)
    assert repo.content_hash("X Ltd", "Board approves QIP", day) == repo.content_hash(
        "X Ltd", "Board approves QIP - XBRL", day
    )


def test_upsert_company_is_idempotent(session) -> None:
    first = repo.upsert_company(session, "Vantage Infratech Limited", bse_code="500325")
    second = repo.upsert_company(session, "VANTAGE INFRATECH LTD", bse_code="500325")
    assert first.id == second.id
    assert session.query(Company).count() == 1


def test_upsert_company_matches_on_name_without_a_code(session) -> None:
    first = repo.upsert_company(session, "Helios Renewable Power Limited", bse_code="543257")
    second = repo.upsert_company(session, "Helios Renewable Power Ltd")
    assert first.id == second.id


def test_upsert_company_does_not_clobber_richer_data(session) -> None:
    company = repo.upsert_company(session, "X Limited", bse_code="500001", isin="INE000A01001")
    repo.upsert_company(session, "X Ltd", nse_symbol="XLTD")
    session.refresh(company)
    assert company.bse_code == "500001"
    assert company.isin == "INE000A01001"
    assert company.nse_symbol == "XLTD"


def _payload(session, digest: str, **overrides) -> dict:
    company = repo.upsert_company(session, "Vantage Infratech Limited", bse_code="500325")
    base = {
        "content_hash": digest,
        "company_id": company.id,
        "source": "BSE",
        "source_filing_id": "abc-1",
        "headline": "Board approves QIP",
        "category": "QIP",
        "classification_confidence": 0.8,
        "opportunity_score": 91,
        "priority": "HIGH",
        "filing_date": dt.date(2026, 9, 9),
    }
    base.update(overrides)
    return base


def test_upsert_filing_dedupes_on_content_hash(session) -> None:
    first, created_first = repo.upsert_filing(session, _payload(session, "hash-1"))
    second, created_second = repo.upsert_filing(session, _payload(session, "hash-1", opportunity_score=95))
    assert created_first is True and created_second is False
    assert first.id == second.id
    assert second.opportunity_score == 95            # enrichment updates in place
    assert session.query(EventFiling).count() == 1


def test_replace_transactions_rewrites_rather_than_appends(session) -> None:
    filing, _ = repo.upsert_filing(session, _payload(session, "hash-2"))
    repo.replace_transactions(session, filing.id, [{"transaction_type": "PRIMARY_ISSUE", "amount_inr": 100}])
    repo.replace_transactions(session, filing.id, [{"transaction_type": "PRIMARY_ISSUE", "amount_inr": 200}])
    rows = session.query(Transaction).filter_by(filing_id=filing.id).all()
    assert len(rows) == 1 and float(rows[0].amount_inr) == 200


def test_investor_upsert_and_link(session) -> None:
    filing, _ = repo.upsert_filing(session, _payload(session, "hash-3"))
    investor = repo.upsert_investor(session, "Blackstone", "PE", is_marquee=True)
    again = repo.upsert_investor(session, "BLACKSTONE")
    assert investor.id == again.id
    repo.link_investor(session, filing.id, investor.id, "context")
    repo.link_investor(session, filing.id, investor.id, "context")   # idempotent
    session.refresh(filing)
    assert len(filing.investor_links) == 1


def test_promoter_scoped_to_company(session) -> None:
    company_a = repo.upsert_company(session, "A Limited", bse_code="1")
    company_b = repo.upsert_company(session, "B Limited", bse_code="2")
    first = repo.upsert_promoter(session, company_a.id, "Anand Krishnan")
    second = repo.upsert_promoter(session, company_b.id, "Anand Krishnan")
    assert first.id != second.id


def test_queue_alert_dedupes_per_channel(session) -> None:
    filing, _ = repo.upsert_filing(session, _payload(session, "hash-4"))
    _, created_first = repo.queue_alert(
        session, filing_id=filing.id, alert_date=dt.date(2026, 9, 9),
        priority="HIGH", channel="slack", title="t", body="b",
    )
    _, created_second = repo.queue_alert(
        session, filing_id=filing.id, alert_date=dt.date(2026, 9, 9),
        priority="HIGH", channel="slack", title="t", body="b",
    )
    assert created_first is True and created_second is False
    assert session.query(DailyAlert).count() == 1


def test_mark_alert_records_sent_time(session) -> None:
    filing, _ = repo.upsert_filing(session, _payload(session, "hash-5"))
    alert, _ = repo.queue_alert(
        session, filing_id=filing.id, alert_date=dt.date(2026, 9, 9),
        priority="HIGH", channel="csv", title="t", body="b",
    )
    repo.mark_alert(session, alert, "SENT")
    assert alert.status == "SENT" and alert.sent_at is not None
    assert repo.pending_alerts(session) == []


def test_run_checkpointing(session) -> None:
    run = repo.start_run(session, "BSE", dt.date(2026, 9, 1), dt.date(2026, 9, 9))
    assert repo.last_successful_run(session, "BSE") is None      # still RUNNING
    repo.finish_run(session, run, "OK", records_seen=10, records_new=7)
    latest = repo.last_successful_run(session, "BSE")
    assert latest is not None and latest.to_date == dt.date(2026, 9, 9)
    assert latest.records_new == 7


def test_failed_run_is_not_a_checkpoint(session) -> None:
    run = repo.start_run(session, "BSE", dt.date(2026, 9, 1), dt.date(2026, 9, 9))
    repo.finish_run(session, run, "FAILED")
    assert repo.last_successful_run(session, "BSE") is None


def test_audit_log_written_on_upsert(session) -> None:
    repo.upsert_filing(session, _payload(session, "hash-6"))
    session.commit()
    from bse_monitor.database.models import AuditLog

    assert session.query(AuditLog).filter_by(entity_type="event_filing").count() >= 1


def test_counts(session) -> None:
    repo.upsert_filing(session, _payload(session, "hash-7"))
    session.commit()
    result = repo.counts(session)
    assert result["companies"] == 1 and result["filings"] == 1
