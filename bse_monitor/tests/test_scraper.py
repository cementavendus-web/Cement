"""Scraper parsing, retry policy and rate limiting — no network access."""

from __future__ import annotations

import datetime as dt

import pytest
import requests

from bse_monitor.scraper.base import HttpClient, RateLimiter, RetryPolicy, ScraperError
from bse_monitor.scraper.bse import BseScraper, attachment_urls, parse_bse_datetime
from bse_monitor.scraper.models import RawFiling
from bse_monitor.scraper.nse import NseScraper

BSE_ROW = {
    "NEWSID": "abc-123",
    "SCRIP_CD": 500325,
    "SLONGNAME": "Vantage Infratech Limited",
    "NEWSSUB": "Board approves QIP of Rs. 1,200 crore",
    "MORE": "<p>The Board has <b>approved</b> the raising of funds.</p>",
    "NEWS_DT": "2026-09-09T16:12:00",
    "ATTACHMENTNAME": "0909abcd-ef01.pdf",
    "CATEGORYNAME": "Company Update",
    "SUBCATNAME": "Fund Raising",
}


@pytest.fixture()
def scraper(config) -> BseScraper:
    return BseScraper(config)


def test_parse_row(scraper: BseScraper) -> None:
    filing = scraper.parse_row(BSE_ROW)
    assert filing is not None
    assert filing.company_name == "Vantage Infratech Limited"
    assert filing.bse_code == "500325"
    assert filing.source_filing_id == "abc-123"
    assert filing.filing_date == dt.date(2026, 9, 9)
    assert "<b>" not in filing.body_text          # markup stripped
    assert filing.pdf_url.endswith("0909abcd-ef01.pdf")
    assert len(filing.raw_payload["_pdf_candidates"]) == 2   # live + historical


def test_parse_row_is_case_insensitive(scraper: BseScraper) -> None:
    lowered = {key.lower(): value for key, value in BSE_ROW.items()}
    assert scraper.parse_row(lowered).company_name == "Vantage Infratech Limited"


def test_parse_row_rejects_empty(scraper: BseScraper) -> None:
    assert scraper.parse_row({"FOO": "bar"}) is None


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"Table": [BSE_ROW]}, 1),
        ([BSE_ROW], 1),
        ({"Data": [BSE_ROW, BSE_ROW]}, 2),
        ({}, 0),
        (None, 0),
    ],
)
def test_envelope_shapes(payload, expected: int) -> None:
    """BSE has shipped several envelope shapes for the same endpoint."""
    assert len(BseScraper._rows_from_payload(payload)) == expected


def test_total_pages_from_rowcount() -> None:
    assert BseScraper._total_pages({"Table1": [{"ROWCNT": 120}]}) == 3
    assert BseScraper._total_pages({"Table1": [{}]}) is None


def test_fetch_announcements_paginates(config, monkeypatch) -> None:
    scraper = BseScraper(config)
    scraper._warmed = True
    pages = {1: {"Table": [BSE_ROW], "Table1": [{"ROWCNT": 60}]}, 2: {"Table": [dict(BSE_ROW, NEWSID="x-2")]}}
    monkeypatch.setattr(scraper, "_fetch_page", lambda cat, f, t, page: pages.get(page, {"Table": []}))

    filings = list(scraper.fetch_announcements(dt.date(2026, 9, 9), dt.date(2026, 9, 9), ["Company Update"]))
    assert {f.source_filing_id for f in filings} == {"abc-123", "x-2"}


def test_fetch_deduplicates_across_categories(config, monkeypatch) -> None:
    """The same announcement is listed under more than one BSE category."""
    scraper = BseScraper(config)
    scraper._warmed = True
    monkeypatch.setattr(scraper, "_fetch_page", lambda cat, f, t, page: {"Table": [BSE_ROW]} if page == 1 else {"Table": []})
    filings = list(scraper.fetch_announcements(dt.date(2026, 9, 9), dt.date(2026, 9, 9), ["A", "B"]))
    assert len(filings) == 1


def test_fetch_survives_a_failing_category(config, monkeypatch) -> None:
    scraper = BseScraper(config)
    scraper._warmed = True

    def flaky(category, _f, _t, page):
        if category == "bad":
            raise ScraperError("boom")
        return {"Table": [BSE_ROW]} if page == 1 else {"Table": []}

    monkeypatch.setattr(scraper, "_fetch_page", flaky)
    filings = list(scraper.fetch_announcements(dt.date(2026, 9, 9), dt.date(2026, 9, 9), ["bad", "good"]))
    assert len(filings) == 1


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-09-09T16:12:00.123", dt.datetime(2026, 9, 9, 16, 12, 0, 123000)),
        ("09-09-2026 16:12:00", dt.datetime(2026, 9, 9, 16, 12)),
        ("2026-09-09", dt.datetime(2026, 9, 9)),
        ("", None),
        (None, None),
        ("not a date", None),
    ],
)
def test_date_parsing(value, expected) -> None:
    assert parse_bse_datetime(value) == expected


def test_attachment_urls() -> None:
    assert attachment_urls("") == []
    assert attachment_urls("https://x/y.pdf") == ["https://x/y.pdf"]
    assert len(attachment_urls("a.pdf")) == 2


# -- HTTP plumbing ---------------------------------------------------------
def test_retry_backoff_grows_and_is_capped() -> None:
    policy = RetryPolicy(base_seconds=2.0, max_seconds=10.0)
    assert policy.delay_for(1) <= 2.0
    assert policy.delay_for(10) <= 10.0
    assert all(policy.delay_for(n) > 0 for n in range(1, 6))


def test_rate_limiter_spaces_requests() -> None:
    import time

    limiter = RateLimiter(per_second=50)
    started = time.monotonic()
    for _ in range(3):
        limiter.wait()
    assert time.monotonic() - started >= 0.02


def test_client_retries_then_raises(monkeypatch) -> None:
    client = HttpClient("ua", timeout=1, rate_limit_per_second=0, retry=RetryPolicy(max_retries=3, base_seconds=0))
    attempts = {"n": 0}

    def always_fail(*_args, **_kwargs):
        attempts["n"] += 1
        raise requests.ConnectionError("down")

    monkeypatch.setattr(client.session, "request", always_fail)
    with pytest.raises(ScraperError):
        client.request("GET", "https://example.test")
    assert attempts["n"] == 3


def test_client_retries_then_succeeds(monkeypatch) -> None:
    client = HttpClient("ua", timeout=1, rate_limit_per_second=0, retry=RetryPolicy(max_retries=3, base_seconds=0))
    calls = {"n": 0}

    class Response:
        def __init__(self, status: int) -> None:
            self.status_code = status

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

    def flaky(*_args, **_kwargs):
        calls["n"] += 1
        return Response(503 if calls["n"] == 1 else 200)

    monkeypatch.setattr(client.session, "request", flaky)
    assert client.request("GET", "https://example.test").status_code == 200
    assert calls["n"] == 2


def test_warm_up_never_raises(monkeypatch) -> None:
    client = HttpClient("ua", rate_limit_per_second=0)
    monkeypatch.setattr(
        client.session, "get", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("t"))
    )
    assert client.warm_up("https://example.test") is False


# -- NSE -------------------------------------------------------------------
def test_nse_row_parsing(config) -> None:
    filing = NseScraper(config).parse_row(
        {
            "symbol": "VANTAGE",
            "sm_name": "Vantage Infratech Limited",
            "desc": "Fund Raising",
            "an_dt": "2026-09-09 16:12:00",
            "attchmntFile": "https://nse/x.pdf",
            "seqId": "77",
        }
    )
    assert filing.nse_symbol == "VANTAGE"
    assert filing.source == "NSE"
    assert filing.filing_date == dt.date(2026, 9, 9)


def test_raw_filing_combined_text() -> None:
    raw = RawFiling(source="BSE", source_filing_id="1", company_name="X", headline="H", body_text="B")
    assert raw.combined_text() == "H\nB"
    assert raw.filing_date is None
