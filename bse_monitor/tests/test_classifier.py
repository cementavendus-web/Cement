"""Classification and scoring behaviour."""

from __future__ import annotations

import datetime as dt

import pytest

from bse_monitor.classifier.ml import blend
from bse_monitor.classifier.rules import RuleClassifier
from bse_monitor.classifier.scoring import OpportunityScorer, ScoreInput

ALERTS = {
    "high_priority_min_score": 85,
    "medium_priority_min_score": 65,
    "always_high_categories": ["OFS", "PromoterSale", "InvestorExit", "QIP"],
}


@pytest.fixture(scope="module")
def clf() -> RuleClassifier:
    return RuleClassifier()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Board approves QIP of Rs 1,200 crore to Qualified Institutional Buyers", "QIP"),
        ("Intimation of Offer for Sale by the Promoter Selling Shareholder", "OFS"),
        ("Filing of Draft Red Herring Prospectus for the Initial Public Offering", "IPO"),
        ("Rights Issue of equity shares; letter of offer and rights entitlement", "RightsIssue"),
        ("Preferential allotment of convertible warrants on a preferential basis", "PreferentialAllotment"),
        ("Follow-on Public Offer of equity shares of the Company", "FPO"),
        ("Block deal executed; bulk deal disclosure under exchange rules", "BlockDeal"),
        ("Promoter intends to sell shares; promoter stake sale disclosure", "PromoterSale"),
    ],
)
def test_categories(clf: RuleClassifier, text: str, expected: str) -> None:
    assert clf.classify(text).category == expected


def test_routine_disclosure_is_other(clf: RuleClassifier) -> None:
    result = clf.classify(
        "Newspaper publication of unaudited financial results for the quarter ended June 2026"
    )
    assert result.category == "Other"


def test_negated_fund_raise_is_demoted(clf: RuleClassifier) -> None:
    result = clf.classify(
        "The Board did not approve the proposed fund raise via QIP. The proposal stands withdrawn."
    )
    assert result.category == "Other"
    assert result.certainty == "unknown"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The Board has approved the proposal for a QIP", "approved"),
        ("Allotment of shares under the QIP has been completed", "completed"),
        ("The Company intends to raise funds via a rights issue", "intended"),
        ("Disclosure of shareholding pattern for the quarter", "unknown"),
    ],
)
def test_certainty(clf: RuleClassifier, text: str, expected: str) -> None:
    assert clf.detect_certainty(text) == expected


def test_lock_in_expiry_is_not_classified_as_ipo(clf: RuleClassifier) -> None:
    """The notice recites the IPO it came from; the event is the lock-in release."""
    result = clf.classify(
        "Expiry of anchor investor lock-in period. The lock-in period of 90 days in respect "
        "of 1,10,00,000 equity shares allotted to anchor investors in the Initial Public "
        "Offering shall expire on 30 September, 2026."
    )
    assert result.category == "InvestorExit"
    assert "lock_in_expiry_is_not_an_ipo" in result.evidence["disambiguation_applied"]


def test_ofs_inside_a_drhp_stays_an_ipo(clf: RuleClassifier) -> None:
    result = clf.classify(
        "Filing of Draft Red Herring Prospectus for the Initial Public Offering comprising a "
        "fresh issue and an Offer for Sale of up to 3,00,00,000 equity shares"
    )
    assert result.category == "IPO"


def test_secondary_categories_are_kept(clf: RuleClassifier) -> None:
    result = clf.classify(
        "Blackstone to exit via block deal; investor exit and secondary sale of shares, "
        "complete exit from the company through a block trade"
    )
    assert result.category in {"BlockDeal", "InvestorExit"}
    assert result.secondary


def test_confidence_is_bounded(clf: RuleClassifier) -> None:
    for text in ["QIP", "", "random text with no signal at all", "OFS block deal QIP IPO rights issue"]:
        result = clf.classify(text)
        assert 0.0 <= result.confidence <= 1.0


# -- scoring ---------------------------------------------------------------
@pytest.fixture(scope="module")
def scorer() -> OpportunityScorer:
    return OpportunityScorer({}, ALERTS)


def test_score_bounds(scorer: OpportunityScorer) -> None:
    today = dt.date(2026, 9, 11)
    extreme = ScoreInput(
        "PromoterSale", 1.0, "approved", 99_000 * 1e7, 90.0, True, True,
        dt.date(2026, 9, 20), today, today,
    )
    assert 0 <= scorer.score(extreme).score <= 100
    empty = ScoreInput("Other", 0.0, "unknown", filing_date=dt.date(2020, 1, 1), as_of=today)
    assert scorer.score(empty).score == 0


def test_bigger_deal_scores_higher(scorer: OpportunityScorer) -> None:
    today = dt.date(2026, 9, 11)
    small = scorer.score(ScoreInput("QIP", 0.8, "approved", 60 * 1e7, filing_date=today, as_of=today))
    large = scorer.score(ScoreInput("QIP", 0.8, "approved", 6000 * 1e7, filing_date=today, as_of=today))
    assert large.score > small.score


def test_scores_do_not_saturate_across_categories(scorer: OpportunityScorer) -> None:
    """Headroom projection must keep high-base categories distinguishable."""
    today = dt.date(2026, 9, 11)
    bare = scorer.score(ScoreInput("PromoterSale", 0.9, "unknown", filing_date=today, as_of=today))
    loaded = scorer.score(
        ScoreInput("PromoterSale", 0.9, "approved", 6000 * 1e7, 12.0, True, True, filing_date=today, as_of=today)
    )
    assert bare.score < loaded.score <= 100


def test_marquee_and_promoter_add_score(scorer: OpportunityScorer) -> None:
    today = dt.date(2026, 9, 11)
    plain = scorer.score(ScoreInput("InvestorExit", 0.8, "completed", 500 * 1e7, filing_date=today, as_of=today))
    marquee = scorer.score(
        ScoreInput("InvestorExit", 0.8, "completed", 500 * 1e7, marquee_investor=True, filing_date=today, as_of=today)
    )
    assert marquee.score > plain.score


def test_recency_decay(scorer: OpportunityScorer) -> None:
    today = dt.date(2026, 9, 11)
    fresh = scorer.score(ScoreInput("QIP", 0.8, "approved", filing_date=today, as_of=today))
    stale = scorer.score(ScoreInput("QIP", 0.8, "approved", filing_date=dt.date(2026, 8, 1), as_of=today))
    assert stale.score < fresh.score


def test_priority_mapping(scorer: OpportunityScorer) -> None:
    assert scorer.priority_for(95, "QIP") == "HIGH"
    assert scorer.priority_for(70, "OFS") == "HIGH"       # always-high category
    assert scorer.priority_for(70, "RightsIssue") == "MEDIUM"
    assert scorer.priority_for(30, "RightsIssue") == "LOW"


def test_breakdown_is_explainable(scorer: OpportunityScorer) -> None:
    today = dt.date(2026, 9, 11)
    result = scorer.score(
        ScoreInput("OFS", 0.9, "approved", 900 * 1e7, 7.5, True, True, filing_date=today, as_of=today)
    )
    names = {c["component"] for c in result.breakdown["components"]}
    assert {"base", "certainty", "deal_size", "marquee_investor", "promoter_involved", "large_stake"} <= names
    assert result.breakdown["raw_total"] == pytest.approx(
        result.breakdown["base"] + result.breakdown["bonus_scaled"] + result.breakdown["penalties"],
        abs=0.01,
    )


# -- blending --------------------------------------------------------------
def test_blend_prefers_confident_rules() -> None:
    assert blend("QIP", 0.8, ("OFS", 0.99)) == ("QIP", 0.8, "rules")


def test_blend_allows_ml_to_rescue_other() -> None:
    category, _confidence, method = blend("Other", 0.1, ("OFS", 0.9))
    assert (category, method) == ("OFS", "ml")


def test_blend_without_model_is_passthrough() -> None:
    assert blend("QIP", 0.7, None) == ("QIP", 0.7, "rules")
