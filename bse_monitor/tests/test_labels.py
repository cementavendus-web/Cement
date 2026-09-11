"""Deal ingestion, cross-source dedupe and label construction."""

from __future__ import annotations

import datetime as dt

import pytest

from bse_monitor.database import repository as repo
from bse_monitor.database.models import DailyQuote, Disposal
from bse_monitor.model.ingest import ingest_deals, ingest_quotes
from bse_monitor.model.labels import (
    LabelSpec,
    coverage_report,
    dedupe_disposals,
    label_for_window,
    label_row,
)
from bse_monitor.scraper.bhavcopy import parse_bhavcopy
from bse_monitor.scraper.deals import (
    normalize_side,
    parse_deal_csv,
    parse_deal_date,
    parse_deal_row,
)

WEEK_END = dt.date(2026, 9, 11)   # a Friday

DEAL_CSV = """Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price
10-Sep-2026,SOLARIS,Solaris Digital Payments Limited,BLACKSTONE CAPITAL PARTNERS,SELL,"1,80,00,000","3,000.00"
10-Sep-2026,SOLARIS,Solaris Digital Payments Limited,SBI MUTUAL FUND,BUY,"90,00,000","3,000.00"
"""


# -- archive parsing -------------------------------------------------------
def test_deal_csv_parsing() -> None:
    rows = parse_deal_csv(DEAL_CSV, "NSE_BLOCK")
    assert len(rows) == 2
    sale = rows[0]
    assert sale["holder_name"] == "BLACKSTONE CAPITAL PARTNERS"
    assert sale["quantity"] == 18_000_000
    # Deal value is computable from the record; no price feed needed for the label.
    assert sale["value_inr"] == pytest.approx(18_000_000 * 3000)


@pytest.mark.parametrize(
    "raw,expected",
    [("S", "SELL"), ("SELL", "SELL"), ("Sell", "SELL"), ("B", "BUY"), ("BUY", "BUY"), (None, "BUY")],
)
def test_side_synonyms(raw, expected) -> None:
    assert normalize_side(raw) == expected


@pytest.mark.parametrize(
    "raw", ["10-Sep-2026", "10-09-2026", "2026-09-10", "10/09/2026", "20260910"]
)
def test_date_formats_across_the_archive(raw) -> None:
    assert parse_deal_date(raw) == dt.date(2026, 9, 10)


def test_column_synonyms_are_handled() -> None:
    """Header spellings have changed across the decade of archive."""
    old_style = {"DATE": "10-Sep-2026", "SYMBOL": "X", "CLIENT NAME": "SOME FUND",
                 "BUY/SELL": "SELL", "QTY": "1000", "PRICE": "50"}
    assert parse_deal_row(old_style, "NSE_BULK")["quantity"] == 1000


def test_unusable_row_returns_none() -> None:
    assert parse_deal_row({"Date": "", "Client Name": ""}, "NSE_BULK") is None


def test_bhavcopy_excludes_non_equity_series() -> None:
    text = ("TckrSymb,SctySrs,ClsPric,TtlTradgVol\n"
            "SOLARIS,EQ,3010.50,1250000\nSOMEBOND,DB,101.00,500\n")
    rows = parse_bhavcopy(text, dt.date(2026, 9, 10))
    assert [r["code"] for r in rows] == ["SOLARIS"]


# -- ingestion and dedupe --------------------------------------------------
def test_ingest_creates_companies_and_disposals(session) -> None:
    stats = ingest_deals(session, parse_deal_csv(DEAL_CSV, "NSE_BLOCK"))
    assert stats.created == 2 and stats.skipped == 0
    assert session.query(Disposal).count() == 2


def test_same_trade_from_both_exchanges_counts_once(session) -> None:
    """NSE and BSE both publish the same block; SAST may file it a third time.

    Summing raw rows would treble one disposal and label the week three times over.
    """
    for source in ("NSE_BLOCK", "BSE_BLOCK", "SAST"):
        ingest_deals(session, parse_deal_csv(DEAL_CSV, source))
    sells = session.query(Disposal).filter_by(side="SELL").all()
    assert len(sells) == 1


def test_different_quantities_are_different_trades(session) -> None:
    ingest_deals(session, parse_deal_csv(DEAL_CSV, "NSE_BLOCK"))
    variant = DEAL_CSV.replace('"1,80,00,000"', '"90,00,000"')
    ingest_deals(session, parse_deal_csv(variant, "NSE_BLOCK"))
    assert session.query(Disposal).filter_by(side="SELL").count() == 2


def test_ingest_is_rerunnable(session) -> None:
    first = ingest_deals(session, parse_deal_csv(DEAL_CSV, "NSE_BLOCK"))
    second = ingest_deals(session, parse_deal_csv(DEAL_CSV, "NSE_BLOCK"))
    assert first.created == 2 and second.created == 0 and second.duplicate == 2


def test_quotes_for_untracked_scrips_are_skipped(session) -> None:
    """The bhavcopy covers the whole market; we only track filers."""
    repo.upsert_company(session, "Solaris Digital Payments Limited", bse_code="SOLARIS")
    text = ("TckrSymb,SctySrs,ClsPric,TtlTradgVol\n"
            "SOLARIS,EQ,3010.50,1250000\nUNKNOWNCO,EQ,10.00,100\n")
    stats = ingest_quotes(session, parse_bhavcopy(text, dt.date(2026, 9, 10)))
    assert stats.created == 1 and stats.skipped == 1
    assert session.query(DailyQuote).count() == 1


def test_average_daily_volume_respects_as_of(session) -> None:
    company = repo.upsert_company(session, "X Limited", bse_code="X")
    for offset, volume in enumerate([100, 200, 300]):
        repo.upsert_quote(session, {
            "company_id": company.id, "trade_date": dt.date(2026, 9, 1) + dt.timedelta(days=offset),
            "close_price": 10, "volume": volume, "exchange": "BSE",
        })
    # The third day must not leak into an as_of that precedes it.
    assert repo.average_daily_volume(session, company.id, dt.date(2026, 9, 2)) == 150
    assert repo.average_daily_volume(session, company.id, dt.date(2026, 9, 3)) == 200


def test_latest_quote_never_looks_forward(session) -> None:
    company = repo.upsert_company(session, "X Limited", bse_code="X")
    repo.upsert_quote(session, {"company_id": company.id, "trade_date": dt.date(2026, 9, 10),
                                "close_price": 100, "exchange": "BSE"})
    repo.upsert_quote(session, {"company_id": company.id, "trade_date": dt.date(2026, 9, 20),
                                "close_price": 200, "exchange": "BSE"})
    assert float(repo.latest_quote(session, company.id, dt.date(2026, 9, 15)).close_price) == 100


# -- labels ----------------------------------------------------------------
def _disposal(days_after: int, *, pct=None, value=None, side="SELL") -> Disposal:
    return Disposal(
        company_id=1, holder_key="blackstone", holder_name="Blackstone",
        trade_date=WEEK_END + dt.timedelta(days=days_after),
        side=side, percent_of_equity=pct, value_inr=value, quantity=1000,
    )


def test_label_fires_on_the_percentage_threshold() -> None:
    result = label_for_window([_disposal(10, pct=0.8)], WEEK_END)
    assert result.label == 1 and result.lead_time_days == 10


def test_label_fires_on_the_value_threshold() -> None:
    assert label_for_window([_disposal(5, value=150e7)], WEEK_END).label == 1


def test_below_both_thresholds_is_negative() -> None:
    assert label_for_window([_disposal(5, pct=0.2, value=10e7)], WEEK_END).label == 0


def test_thresholds_accumulate_across_the_window() -> None:
    """Three 0.2% sales inside 60 days are a 0.6% exit."""
    rows = [_disposal(5, pct=0.2), _disposal(20, pct=0.2), _disposal(40, pct=0.2)]
    result = label_for_window(rows, WEEK_END)
    assert result.label == 1 and result.n_disposals == 3


def test_window_is_strictly_forward() -> None:
    """A trade on week_end itself belongs to the features, not the label."""
    assert label_for_window([_disposal(0, pct=5.0)], WEEK_END).label == 0
    assert label_for_window([_disposal(1, pct=5.0)], WEEK_END).label == 1


def test_window_closes_at_the_horizon() -> None:
    assert label_for_window([_disposal(60, pct=5.0)], WEEK_END).label == 1
    assert label_for_window([_disposal(61, pct=5.0)], WEEK_END).label == 0


def test_buys_never_label() -> None:
    assert label_for_window([_disposal(10, pct=9.0, side="BUY")], WEEK_END).label == 0


def test_duplicates_do_not_inflate_a_label() -> None:
    """Three reports of one 0.2% sale must not sum to 0.6%."""
    one = _disposal(10, pct=0.2)
    rows = [one, _disposal(10, pct=0.2), _disposal(10, pct=0.2)]
    result = label_for_window(rows, WEEK_END)
    assert result.n_disposals == 1 and result.label == 0


def test_dedupe_keeps_genuinely_distinct_trades() -> None:
    rows = [_disposal(10, pct=0.2), _disposal(20, pct=0.2)]
    assert len(dedupe_disposals(rows)) == 2


def test_custom_spec_changes_the_answer() -> None:
    strict = LabelSpec(horizon_days=7, pct_threshold=2.0)
    assert label_for_window([_disposal(10, pct=3.0)], WEEK_END, strict).label == 0


def test_label_row_queries_only_the_forward_window(session) -> None:
    company = repo.upsert_company(session, "Solaris Digital Payments Limited", bse_code="S")
    for offset, pct in ((-10, 9.0), (10, 0.8)):
        key = repo.disposal_dedupe_key(
            company.id, "blackstone", WEEK_END + dt.timedelta(days=offset), "SELL", 1000 + offset
        )
        repo.upsert_disposal(session, {
            "dedupe_key": key, "company_id": company.id, "source": "NSE_BLOCK",
            "holder_name": "Blackstone", "holder_key": "blackstone",
            "trade_date": WEEK_END + dt.timedelta(days=offset), "side": "SELL",
            "percent_of_equity": pct, "quantity": 1000 + offset,
        })
    result = label_row(session, company.id, "blackstone", WEEK_END)
    # The past 9% sale must not contribute; only the forward 0.8% counts.
    assert result.total_pct == pytest.approx(0.8) and result.label == 1


# -- coverage --------------------------------------------------------------
def test_coverage_report_exposes_sast_share(session) -> None:
    """SAST has no bulk archive, so its contribution must be a number, not a hope."""
    ingest_deals(session, parse_deal_csv(DEAL_CSV, "NSE_BLOCK"))
    variant = DEAL_CSV.replace('"1,80,00,000"', '"55,00,000"')
    ingest_deals(session, parse_deal_csv(variant, "SAST"))

    report = coverage_report(session)
    assert len(report) == 1
    year = report[0]
    assert year["year"] == 2026
    assert year["sources"]["SAST"] == 1 and year["sources"]["NSE_BLOCK"] == 1
    assert year["sast_share"] == pytest.approx(0.5)
    assert year["value_coverage"] == pytest.approx(1.0)
