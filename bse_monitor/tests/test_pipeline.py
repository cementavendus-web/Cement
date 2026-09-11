"""End-to-end pipeline behaviour, alerts and reporting views — fully offline."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from bse_monitor.alerts.channels import CsvChannel, SlackChannel, build_channels
from bse_monitor.alerts.dispatcher import AlertDispatcher
from bse_monitor.alerts.formatting import format_inr, markdown_table
from bse_monitor.database import repository as repo
from bse_monitor.database.models import DailyAlert, EventFiling, Investor, Transaction
from bse_monitor.pipeline import MonitorPipeline
from bse_monitor.reports import daily_events, fund_raise_pipeline, sell_down_pipeline
from bse_monitor.scraper.models import RawFiling

SAMPLES = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "sample_filings.json").read_text(
        encoding="utf-8"
    )
)


def _raws() -> list[RawFiling]:
    return [
        RawFiling(
            source=item.get("source", "BSE"),
            source_filing_id=item["source_filing_id"],
            company_name=item["company_name"],
            bse_code=item.get("bse_code"),
            headline=item.get("headline", ""),
            body_text=item.get("body_text", ""),
            filing_datetime=dt.datetime.fromisoformat(item["filing_datetime"]),
        )
        for item in SAMPLES
    ]


@pytest.fixture()
def pipeline(config) -> MonitorPipeline:
    return MonitorPipeline(config)


@pytest.fixture()
def loaded(pipeline: MonitorPipeline, session):
    stats, filings = pipeline.process_batch(session, _raws())
    return stats, filings


def test_all_samples_are_ingested(loaded) -> None:
    stats, filings = loaded
    assert stats.seen == len(SAMPLES)
    assert stats.new == len(SAMPLES)
    assert len(filings) == len(SAMPLES)


def test_expected_categories(loaded) -> None:
    _stats, filings = loaded
    by_id = {f.source_filing_id: f for f in filings}
    assert by_id["DEMO-0001"].category == "QIP"
    assert by_id["DEMO-0002"].category == "OFS"
    assert by_id["DEMO-0003"].category in {"InvestorExit", "BlockDeal"}
    assert by_id["DEMO-0004"].category == "RightsIssue"
    assert by_id["DEMO-0005"].category == "InvestorExit"      # lock-in, not IPO
    assert by_id["DEMO-0006"].category == "PreferentialAllotment"
    assert by_id["DEMO-0007"].category == "Other"             # routine disclosure
    assert by_id["DEMO-0008"].category == "Other"             # negated fund raise
    assert by_id["DEMO-0010"].category == "IPO"


def test_high_value_events_outrank_routine_ones(loaded) -> None:
    _stats, filings = loaded
    by_id = {f.source_filing_id: f for f in filings}
    assert by_id["DEMO-0003"].opportunity_score > by_id["DEMO-0007"].opportunity_score
    assert by_id["DEMO-0007"].priority == "LOW"
    assert by_id["DEMO-0002"].priority == "HIGH"


def test_extracted_transaction_values(loaded, session) -> None:
    _stats, filings = loaded
    qip = next(f for f in filings if f.source_filing_id == "DEMO-0001")
    txn = session.query(Transaction).filter_by(filing_id=qip.id).one()
    assert float(txn.amount_inr) == pytest.approx(1200 * 1e7)
    assert txn.transaction_type == "PRIMARY_ISSUE"
    assert txn.board_meeting_date == dt.date(2026, 9, 9)


def test_issue_size_only_applies_to_primary_issues(loaded, session) -> None:
    """A block deal's "aggregating Rs X crore" is a trade value, not an issue size."""
    _stats, filings = loaded
    by_id = {f.source_filing_id: f for f in filings}

    qip = session.query(Transaction).filter_by(filing_id=by_id["DEMO-0001"].id).one()
    assert float(qip.issue_size_inr) == pytest.approx(1200 * 1e7)

    block = session.query(Transaction).filter_by(filing_id=by_id["DEMO-0003"].id).one()
    assert block.transaction_type == "SECONDARY_SALE"
    assert block.issue_size_inr is None
    assert float(block.amount_inr) == pytest.approx(5400 * 1e7)


def test_lock_in_expiry_is_captured(loaded, session) -> None:
    _stats, filings = loaded
    filing = next(f for f in filings if f.source_filing_id == "DEMO-0005")
    txn = session.query(Transaction).filter_by(filing_id=filing.id).one()
    assert txn.lock_in_expiry_date == dt.date(2026, 9, 30)


def test_investors_are_persisted_and_flagged(loaded, session) -> None:
    names = {row.name for row in session.query(Investor).all()}
    assert {"Blackstone", "ChrysCapital", "General Atlantic", "Warburg Pincus"} <= names
    assert session.query(Investor).filter_by(name="Blackstone").one().is_marquee is True


def test_promoter_is_linked(loaded) -> None:
    _stats, filings = loaded
    ofs = next(f for f in filings if f.source_filing_id == "DEMO-0002")
    assert any(link.promoter.name == "Anand Krishnan" for link in ofs.promoter_links)


def test_score_breakdown_is_stored_and_explainable(loaded) -> None:
    _stats, filings = loaded
    filing = next(f for f in filings if f.source_filing_id == "DEMO-0003")
    components = {c["component"] for c in filing.score_breakdown["components"]}
    assert "base" in components and "marquee_investor" in components


def test_rerun_is_idempotent(pipeline: MonitorPipeline, session) -> None:
    """Re-scraping the same window must not create a second row or alert."""
    pipeline.process_batch(session, _raws())
    first_count = session.query(EventFiling).count()
    stats, _ = pipeline.process_batch(session, _raws())
    assert session.query(EventFiling).count() == first_count
    assert stats.new == 0


def test_one_bad_filing_does_not_kill_the_batch(pipeline: MonitorPipeline, session) -> None:
    raws = _raws()
    raws[0].company_name = ""          # degenerate but must not raise
    stats, filings = pipeline.process_batch(session, raws)
    assert len(filings) == len(raws)


# -- alerts ----------------------------------------------------------------
def test_alerts_are_queued_only_for_actionable_filings(config, session, pipeline) -> None:
    _stats, filings = pipeline.process_batch(session, _raws())
    dispatcher = AlertDispatcher(config.section("alerts"))
    queued = dispatcher.queue(session, filings)
    assert queued > 0
    alerted = {a.filing_id for a in session.query(DailyAlert).all()}
    routine = next(f for f in filings if f.source_filing_id == "DEMO-0007")
    assert routine.id not in alerted


def test_alert_flush_marks_sent(config, session, pipeline) -> None:
    _stats, filings = pipeline.process_batch(session, _raws())
    dispatcher = AlertDispatcher(config.section("alerts"))
    dispatcher.queue(session, filings)
    result = dispatcher.flush(session)
    assert result["sent"] > 0
    assert repo.pending_alerts(session) == []


def test_failed_delivery_stays_pending_for_retry(config, session, pipeline, monkeypatch) -> None:
    _stats, filings = pipeline.process_batch(session, _raws())
    dispatcher = AlertDispatcher(config.section("alerts"))
    dispatcher.queue(session, filings)
    for channel in dispatcher.channels.values():
        monkeypatch.setattr(channel, "send", lambda *_a, **_k: (False, "webhook down"))
    result = dispatcher.flush(session)
    assert result["sent"] == 0 and result["failed"] > 0
    assert len(repo.pending_alerts(session)) > 0       # retried next run


def test_csv_export_written(config, session, pipeline, tmp_path) -> None:
    _stats, filings = pipeline.process_batch(session, _raws())
    dispatcher = AlertDispatcher(config.section("alerts"))
    path = dispatcher.export_csv(filings, dt.date(2026, 9, 11))
    assert path and Path(path).exists()
    assert "Vantage Infratech" in Path(path).read_text(encoding="utf-8")


def test_disabled_channels_are_not_built() -> None:
    channels = build_channels({"channels": {"csv": {"enabled": True, "output_dir": "/tmp/x"}, "slack": {"enabled": False}}})
    assert set(channels) == {"csv"}
    assert isinstance(channels["csv"], CsvChannel)


def test_channel_min_priority_filter() -> None:
    channel = SlackChannel({"enabled": True, "min_priority": "HIGH", "webhook_url": "https://x"})
    assert channel.accepts("HIGH") and not channel.accepts("MEDIUM")


def test_slack_without_webhook_reports_an_error() -> None:
    ok, error = SlackChannel({"enabled": True}).send("t", "b", "HIGH")
    assert ok is False and "webhook_url" in error


# -- reports ---------------------------------------------------------------
def test_daily_events_report(session, pipeline) -> None:
    pipeline.process_batch(session, _raws())
    rows = daily_events(session, dt.date(2026, 9, 11))
    assert rows and all(set(("date", "company", "event_type", "score")) <= set(r) for r in rows)
    assert rows == sorted(rows, key=lambda r: -r["score"])


def test_fund_raise_pipeline_report(session, pipeline) -> None:
    pipeline.process_batch(session, _raws())
    rows = fund_raise_pipeline(session, since=dt.date(2026, 9, 1), min_score=50)
    companies = {r["company"] for r in rows}
    assert "Vantage Infratech Limited" in companies
    assert "Trident Logistics Corporation Limited" not in companies
    assert any(r["stage"] == "Approved" for r in rows)


def test_sell_down_pipeline_report(session, pipeline) -> None:
    pipeline.process_batch(session, _raws())
    rows = sell_down_pipeline(session, since=dt.date(2026, 9, 1), min_score=50)
    companies = {r["company"] for r in rows}
    assert {"Meridian Healthcare Services Limited", "Solaris Digital Payments Limited"} <= companies
    lock_in_rows = [r for r in rows if r["trigger"].startswith("Lock-in expiry")]
    assert lock_in_rows


def test_formatting_helpers() -> None:
    assert format_inr(1200 * 1e7) == "Rs 1,200.0 Cr"
    assert format_inr(None) == "—"
    assert "no events" in markdown_table([], ["a"]).lower()
