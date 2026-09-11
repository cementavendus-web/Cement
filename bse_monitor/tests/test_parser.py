"""Entity extraction: money, shares, prices, dates, investors, promoters."""

from __future__ import annotations

import datetime as dt

import pytest

from bse_monitor.config import load_investors
from bse_monitor.parser.entities import (
    InvestorMatcher,
    best_deal_amount,
    extract_all,
    extract_amounts,
    extract_board_meeting_date,
    extract_lock_in_expiry,
    extract_price_per_share,
    extract_share_count,
    extract_stake_percent,
    extract_promoters,
    promoter_is_involved,
)
from bse_monitor.parser.text import clean_text, normalize_for_match, strip_boilerplate

CRORE = 1e7


@pytest.fixture(scope="module")
def matcher() -> InvestorMatcher:
    return InvestorMatcher(load_investors())


@pytest.mark.parametrize(
    "text,expected_crore",
    [
        ("aggregating up to Rs. 1,200 crore", 1200),
        ("raising INR 2,500 Cr through a QIP", 2500),
        ("issue size of ₹ 45.5 crores", 45.5),
        ("fund raise aggregating Rs. 250 lakh", 2.5),
        ("offer size of Rs 12,00,00,000", 12.0),
    ],
)
def test_amount_parsing(text: str, expected_crore: float) -> None:
    amount = best_deal_amount(text)
    assert amount is not None
    assert amount.crore == pytest.approx(expected_crore, rel=1e-3)


def test_authorised_capital_is_not_the_deal_size() -> None:
    """The largest figure in a filing is frequently not the transaction."""
    text = (
        "The Board approved raising funds aggregating up to Rs. 1,200 crore via QIP. "
        "The authorised share capital of the Company stands at Rs. 5,000 crore."
    )
    assert best_deal_amount(text).crore == pytest.approx(1200)


def test_per_share_price_is_not_a_deal_amount() -> None:
    text = "The floor price for the offer has been fixed at Rs. 745 per equity share."
    assert best_deal_amount(text) is None


def test_aggregate_survives_alongside_a_unit_price() -> None:
    text = "50,00,000 warrants at an issue price of Rs. 210 per warrant, aggregating Rs. 105 crore"
    assert best_deal_amount(text).crore == pytest.approx(105)


def test_usd_is_converted_to_rupees() -> None:
    amounts = extract_amounts("fund raise of USD 100 million")
    assert amounts and amounts[0].value_inr > 100 * 1e6


def test_share_count_and_price() -> None:
    text = "sell 2,50,00,000 equity shares at a price of Rs. 745 per equity share"
    assert extract_share_count(text) == pytest.approx(25_000_000)
    assert extract_price_per_share(text) == pytest.approx(745)


def test_entitlement_ratio_is_not_a_share_count() -> None:
    assert extract_share_count("1 equity share for every 4 shares held") is None


def test_stake_percentage() -> None:
    assert extract_stake_percent("representing 6.20% of the paid-up equity") == pytest.approx(6.2)
    assert extract_stake_percent("no percentage mentioned here") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("the lock-in expires on 30 September, 2026", dt.date(2026, 9, 30)),
        ("lock-in period shall expire on 15-10-2026", dt.date(2026, 10, 15)),
    ],
)
def test_lock_in_expiry(text: str, expected: dt.date) -> None:
    assert extract_lock_in_expiry(text) == expected


def test_board_meeting_date() -> None:
    text = "a meeting of the Board of Directors will be held on 22 September, 2026 to consider"
    assert extract_board_meeting_date(text) == dt.date(2026, 9, 22)


def test_unrelated_date_is_not_a_lock_in_date() -> None:
    assert extract_lock_in_expiry("results were published on 01-01-2026") is None


def test_known_investors_are_matched(matcher: InvestorMatcher) -> None:
    names = {m.name for m in matcher.match_known("Blackstone Capital Partners and GIC sold shares")}
    assert {"Blackstone", "GIC"} <= names


def test_marquee_flag(matcher: InvestorMatcher) -> None:
    assert all(m.is_marquee for m in matcher.match_known("Warburg Pincus exits"))


def test_known_investor_not_duplicated_by_discovery(matcher: InvestorMatcher) -> None:
    result = extract_all("Blackstone Capital Partners has sold shares", matcher=matcher)
    assert [m.name for m in result.investors] == ["Blackstone"]


def test_exchanges_are_not_harvested_as_investors(matcher: InvestorMatcher) -> None:
    discovered = matcher.discover("Filed with BSE Limited and the National Stock Exchange")
    assert discovered == []


def test_promoter_extraction() -> None:
    names = {m.name for m in extract_promoters("The Promoter, Mr. Anand Krishnan, intends to sell")}
    assert "Anand Krishnan" in names


def test_promoter_involvement_needs_an_action() -> None:
    assert promoter_is_involved("The promoter intends to sell 2% of the equity")
    assert not promoter_is_involved("The promoter attended the analyst meet")


def test_extract_all_derives_value_from_shares_and_price(matcher: InvestorMatcher) -> None:
    text = (
        "Promoter intends to sell up to 2,50,00,000 equity shares representing 6.20% "
        "through an Offer for Sale at a floor price of Rs. 745 per equity share."
    )
    result = extract_all(text, matcher=matcher)
    assert result.amount_derived is True
    assert result.amount_inr == pytest.approx(25_000_000 * 745)
    assert result.percent_of_equity == pytest.approx(6.2)
    assert result.promoter_involved is True


def test_text_helpers() -> None:
    assert clean_text("<p>Board &amp; QIP</p>") == "Board & QIP"
    assert normalize_for_match("Sell-Down") == "sell down"
    kept = strip_boilerplate(
        "Board approves QIP. Kindly take the same on record.", ["kindly take the same on record"]
    )
    assert "Kindly take" not in kept
