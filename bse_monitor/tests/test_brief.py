"""The daily ranked brief: composition, degradation and delivery."""

from __future__ import annotations

import csv
import datetime as dt
from email import message_from_bytes
from pathlib import Path

import pytest

from bse_monitor.alerts.channels import EmailChannel
from bse_monitor.database import repository as repo
from bse_monitor.reports.brief import (
    BRIEF_COLUMNS,
    brief_body,
    build_brief,
    last_completed_friday,
    write_brief_csv,
)
from bse_monitor.triggers import refresh as refresh_mod

FRIDAY = dt.date(2026, 9, 11)


@pytest.fixture()
def populated(session, calendar_filing):
    """A company with a derived calendar, a marquee holder and quotes."""
    company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    repo.upsert_investor(session, "Warburg Pincus", "PE", is_marquee=True)

    key = repo.disposal_dedupe_key(company.id, "warburg pincus", dt.date(2026, 6, 1), "SELL", 500_000)
    repo.upsert_disposal(session, {
        "dedupe_key": key, "company_id": company.id, "source": "NSE_BLOCK",
        "holder_name": "Warburg Pincus", "holder_key": "warburg pincus",
        "trade_date": dt.date(2026, 6, 1), "side": "SELL",
        "quantity": 500_000, "price": 600, "percent_of_equity": 1.5,
    })
    for offset in range(35):
        repo.upsert_quote(session, {
            "company_id": company.id, "trade_date": dt.date(2026, 8, 15) + dt.timedelta(days=offset),
            "close_price": 650, "volume": 100_000, "exchange": "BSE",
        })
    session.commit()
    return company


# -- week alignment --------------------------------------------------------
@pytest.mark.parametrize(
    "day,expected",
    [("2026-09-11", "2026-09-11"), ("2026-09-14", "2026-09-11"),
     ("2026-09-13", "2026-09-11"), ("2026-09-10", "2026-09-04")],
)
def test_brief_uses_the_last_completed_week(day, expected) -> None:
    """On a Monday the *coming* Friday has not traded yet."""
    assert last_completed_friday(dt.date.fromisoformat(day)) == dt.date.fromisoformat(expected)


# -- composition -----------------------------------------------------------
def test_brief_ranks_and_explains(session, populated) -> None:
    result = build_brief(session, as_of=FRIDAY)
    assert result.rows
    top = result.rows[0]
    assert top["rank"] == 1
    assert top["company"] and top["holder"]
    assert top["reason_1"]                       # always a "why"


def test_brief_surfaces_the_calendar_trigger(session, populated) -> None:
    """The whole point: a Layer 1 deadline reaching the Layer 3 ranking."""
    result = build_brief(session, as_of=FRIDAY)
    top = result.rows[0]
    assert top["days_to_trigger"] != ""
    assert "lock-in" in top["trigger_type"].lower()


def test_brief_reports_stake_value(session, populated) -> None:
    top = build_brief(session, as_of=FRIDAY).rows[0]
    assert top["stake_cr"]                       # needs bhavcopy to exist


def test_one_line_per_position(session, populated) -> None:
    """Listing the same holder once per week is noise, not signal."""
    rows = build_brief(session, as_of=FRIDAY).rows
    seen = [(r["company"], r["holder"]) for r in rows]
    assert len(seen) == len(set(seen))


def test_brief_degrades_to_the_baseline_without_a_model(session, populated) -> None:
    result = build_brief(session, as_of=FRIDAY, model_path=None)
    assert result.scored_by == "baseline" and result.rows


def test_missing_model_artefact_does_not_break_the_brief(session, populated, tmp_path) -> None:
    result = build_brief(session, as_of=FRIDAY, model_path=str(tmp_path / "absent.joblib"))
    assert result.scored_by == "baseline" and result.rows


def test_top_n_caps_the_sheet(session, populated) -> None:
    assert len(build_brief(session, as_of=FRIDAY, top_n=1).rows) <= 1


def test_empty_universe_is_not_an_error(session) -> None:
    result = build_brief(session, as_of=FRIDAY)
    assert result.rows == [] and result.candidates == 0


def test_brief_carries_no_labels(session, populated) -> None:
    """The 60-day forward window has not happened yet; a label would be leakage."""
    result = build_brief(session, as_of=FRIDAY)
    assert all("label" not in row for row in result.rows)


# -- output ----------------------------------------------------------------
def test_csv_has_the_declared_columns(session, populated, tmp_path) -> None:
    result = build_brief(session, as_of=FRIDAY)
    path = write_brief_csv(result, tmp_path)
    with open(path, encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames) == BRIEF_COLUMNS
        assert len(list(reader)) == len(result.rows)


def test_csv_columns_survive_the_shared_channel_trap(session, populated, tmp_path) -> None:
    """CsvChannel's fixed COLUMNS with extrasaction='ignore' would drop these."""
    from bse_monitor.alerts.channels import CsvChannel

    assert set(BRIEF_COLUMNS) - set(CsvChannel.COLUMNS)
    path = write_brief_csv(build_brief(session, as_of=FRIDAY), tmp_path)
    header = Path(path).read_text(encoding="utf-8").splitlines()[0]
    assert "days_to_trigger" in header and "reason_1" in header


def test_body_summarises_and_points_at_the_attachment(session, populated) -> None:
    result = build_brief(session, as_of=FRIDAY)
    body = brief_body(result, limit=1)
    assert result.as_of.isoformat() in body
    assert "scored by" in body


def test_empty_brief_body_says_so(session) -> None:
    assert "Nothing above threshold" in brief_body(build_brief(session, as_of=FRIDAY))


# -- delivery --------------------------------------------------------------
class _CapturingSMTP:
    sent: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, *args):
        pass

    def send_message(self, message):
        type(self).sent.append(message)


def test_email_attaches_the_csv(session, populated, tmp_path, monkeypatch) -> None:
    """A 40-row table pasted into a body is unreadable; an attachment opens."""
    import smtplib

    _CapturingSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)

    result = build_brief(session, as_of=FRIDAY)
    path = write_brief_csv(result, tmp_path)
    channel = EmailChannel({
        "enabled": True, "recipients": ["ram.narayan@avendusspark.com"],
        "sender": "bse-monitor@example.com", "smtp_host": "localhost", "smtp_port": 25,
        "use_tls": False,
    })
    ok, error = channel.send("Brief", brief_body(result), "HIGH", attachments=[path])

    assert ok is True and error is None
    message = _CapturingSMTP.sent[0]
    attachments = [p for p in message.iter_attachments()]
    assert len(attachments) == 1
    assert attachments[0].get_filename().endswith(".csv")
    assert message["To"] == "ram.narayan@avendusspark.com"


def test_email_without_attachments_still_sends(session, monkeypatch) -> None:
    import smtplib

    _CapturingSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)
    channel = EmailChannel({
        "enabled": True, "recipients": ["x@example.com"], "smtp_host": "h",
        "smtp_port": 25, "use_tls": False,
    })
    assert channel.send("t", "b", "LOW") == (True, None)


def test_missing_attachment_is_skipped_not_fatal(session, monkeypatch, tmp_path) -> None:
    import smtplib

    _CapturingSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)
    channel = EmailChannel({
        "enabled": True, "recipients": ["x@example.com"], "smtp_host": "h",
        "smtp_port": 25, "use_tls": False,
    })
    ok, _error = channel.send("t", "b", "LOW", attachments=[tmp_path / "gone.csv"])
    assert ok is True


def test_no_recipients_is_reported(session) -> None:
    ok, error = EmailChannel({"enabled": True, "recipients": []}).send("t", "b")
    assert ok is False and "recipients" in error
