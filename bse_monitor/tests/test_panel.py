"""Panel construction, and above all the point-in-time guarantees.

The leakage tests here are the load-bearing ones. A single feature that reads
the future produces an evaluation that looks excellent and is worthless, and it
does so silently — nothing crashes, the metrics just improve.
"""

from __future__ import annotations

import datetime as dt
import inspect

import pytest

from bse_monitor.database import repository as repo
from bse_monitor.model import features as feature_mod
from bse_monitor.model.features import (
    FEATURE_NAMES,
    build_features,
    calendar_features,
    holder_features,
    iter_week_ends,
    position_features,
    week_ending_friday,
)
from bse_monitor.model.panel import (
    BootstrapUniverse,
    build_panel,
    panel_stats,
    read_panel,
    write_panel,
)
from bse_monitor.triggers import refresh as refresh_mod

WEEK = dt.date(2026, 9, 11)


@pytest.fixture()
def seeded(session):
    """A company, a holder with history, quotes, and a derived calendar."""
    company = repo.upsert_company(session, "Solaris Digital Payments Limited", bse_code="SOLARIS")
    repo.upsert_investor(session, "Blackstone", "PE", is_marquee=True)

    def deal(day: dt.date, qty: float, pct: float | None = None, side: str = "SELL"):
        key = repo.disposal_dedupe_key(company.id, "blackstone", day, side, qty)
        repo.upsert_disposal(session, {
            "dedupe_key": key, "company_id": company.id, "source": "NSE_BLOCK",
            "holder_name": "Blackstone", "holder_key": "blackstone",
            "trade_date": day, "side": side, "quantity": qty,
            "price": 3000, "value_inr": qty * 3000, "percent_of_equity": pct,
        })

    deal(dt.date(2026, 3, 2), 1_000_000, 1.0)      # past
    deal(dt.date(2026, 6, 1), 500_000, 0.5)        # past
    for offset in range(40):
        repo.upsert_quote(session, {
            "company_id": company.id,
            "trade_date": dt.date(2026, 8, 20) + dt.timedelta(days=offset),
            "close_price": 3000, "volume": 100_000, "exchange": "BSE",
        })
    session.commit()
    return company


# -- week alignment --------------------------------------------------------
@pytest.mark.parametrize("day", ["2026-09-07", "2026-09-09", "2026-09-11"])
def test_every_weekday_maps_to_its_friday(day) -> None:
    assert week_ending_friday(dt.date.fromisoformat(day)) == dt.date(2026, 9, 11)


def test_week_ends_are_weekly_and_inclusive() -> None:
    weeks = iter_week_ends(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert weeks[0] == dt.date(2026, 9, 4) and weeks[-1] == dt.date(2026, 9, 25)
    assert all((b - a).days == 7 for a, b in zip(weeks, weeks[1:]))


# -- leakage ---------------------------------------------------------------
def test_no_feature_function_reads_the_wall_clock() -> None:
    """A stray date.today() in a feature trains the model on the future.

    Checked by AST rather than string search so prose about the rule does not
    trip the rule.
    """
    import ast

    tree = ast.parse(inspect.getsource(feature_mod))
    wall_clock = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"today", "now", "utcnow"}
    ]
    assert wall_clock == [], f"{len(wall_clock)} wall-clock call(s) in feature code"


def test_future_disposals_never_enter_features(session, seeded) -> None:
    company = seeded
    future = dt.date(2026, 10, 20)
    key = repo.disposal_dedupe_key(company.id, "blackstone", future, "SELL", 9_999_999)
    repo.upsert_disposal(session, {
        "dedupe_key": key, "company_id": company.id, "source": "NSE_BLOCK",
        "holder_name": "Blackstone", "holder_key": "blackstone",
        "trade_date": future, "side": "SELL", "quantity": 9_999_999,
        "percent_of_equity": 40.0,
    })
    session.flush()

    row = holder_features(session, company.id, "blackstone", WEEK)
    assert row["holder_prior_sells_here"] == 2          # the two past sales only
    position = position_features(session, company.id, "blackstone", WEEK)
    assert position["last_stake_pct"] == pytest.approx(0.5)   # not the future 40%


def test_a_trigger_is_invisible_before_it_was_knowable(session, calendar_filing) -> None:
    """known_from_date, not trigger_date, is the point-in-time guard.

    A March lock-in expiry only became knowable from the filing that disclosed
    the allotment; a model seeing it earlier than the market did is cheating.
    """
    company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    session.flush()

    before = calendar_features(session, company.id, filing.filing_date - dt.timedelta(days=1))
    after = calendar_features(session, company.id, filing.filing_date + dt.timedelta(days=1))
    assert before["triggers_within_90d"] == 0
    assert after["triggers_within_90d"] > 0


def test_only_forward_triggers_count(session, calendar_filing) -> None:
    company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    session.flush()
    # Long after every derived deadline has passed.
    assert calendar_features(session, company.id, dt.date(2030, 1, 1))["days_to_nearest_trigger"] is None


def test_adv_and_price_respect_as_of(session, seeded) -> None:
    company = seeded
    repo.upsert_quote(session, {
        "company_id": company.id, "trade_date": dt.date(2026, 12, 1),
        "close_price": 99999, "volume": 1, "exchange": "BSE",
    })
    session.flush()
    position = position_features(session, company.id, "blackstone", WEEK)
    # The December price must not reach a September row.
    assert position["stake_value_cr"] == pytest.approx(500_000 * 3000 / 1e7)


def test_holder_universe_excludes_the_not_yet_seen(session, seeded) -> None:
    universe = BootstrapUniverse()
    assert universe.slots_as_of(session, dt.date(2026, 1, 1)) == []
    assert len(universe.slots_as_of(session, WEEK)) == 1


# -- feature construction --------------------------------------------------
def test_build_features_emits_the_declared_columns(session, seeded) -> None:
    row = build_features(session, seeded.id, "blackstone", WEEK, is_marquee=True)
    assert set(FEATURE_NAMES) <= set(row)
    assert row["is_marquee"] == 1


def test_days_of_adv_measures_liquidation_difficulty(session, seeded) -> None:
    """500k shares against 100k/day ADV is five days of volume."""
    row = position_features(session, seeded.id, "blackstone", WEEK)
    assert row["days_of_adv_to_liquidate"] == pytest.approx(5.0)


def test_holder_sell_rate_is_as_of_not_lifetime(session, seeded) -> None:
    early = holder_features(session, seeded.id, "blackstone", dt.date(2026, 4, 1))
    later = holder_features(session, seeded.id, "blackstone", WEEK)
    assert early["holder_prior_sells_anywhere"] == 1
    assert later["holder_prior_sells_anywhere"] == 2


def test_company_with_no_calendar_gets_null_not_zero(session, seeded) -> None:
    """Absent is not the same as imminent; the model must be able to tell."""
    assert calendar_features(session, seeded.id, WEEK)["days_to_nearest_trigger"] is None


# -- panel -----------------------------------------------------------------
def test_panel_rows_carry_ids_features_and_labels(session, seeded) -> None:
    rows = list(build_panel(session, dt.date(2026, 8, 1), dt.date(2026, 9, 30)))
    assert rows
    row = rows[0]
    assert {"company_id", "holder_key", "week_end", "label"} <= set(row)
    assert set(FEATURE_NAMES) <= set(row)


def test_panel_labels_a_forward_sale(session, seeded) -> None:
    company = seeded
    sale = dt.date(2026, 9, 25)
    key = repo.disposal_dedupe_key(company.id, "blackstone", sale, "SELL", 2_000_000)
    repo.upsert_disposal(session, {
        "dedupe_key": key, "company_id": company.id, "source": "NSE_BLOCK",
        "holder_name": "Blackstone", "holder_key": "blackstone",
        "trade_date": sale, "side": "SELL", "quantity": 2_000_000,
        "percent_of_equity": 2.0, "value_inr": 2_000_000 * 3000,
    })
    session.flush()

    rows = {r["week_end"]: r for r in build_panel(session, dt.date(2026, 9, 1), dt.date(2026, 9, 18))}
    assert rows[dt.date(2026, 9, 11)]["label"] == 1
    assert rows[dt.date(2026, 9, 11)]["lead_time_days"] == 14


def test_inference_path_produces_no_labels(session, seeded) -> None:
    rows = list(build_panel(session, dt.date(2026, 9, 1), dt.date(2026, 9, 18), label_rows=False))
    assert rows and all("label" not in r for r in rows)


def test_panel_stats_report_the_base_rate(session, seeded) -> None:
    rows = list(build_panel(session, dt.date(2026, 8, 1), dt.date(2026, 9, 30)))
    stats = panel_stats(rows)
    assert stats.rows == len(rows) and 0.0 <= stats.base_rate <= 1.0
    assert stats.as_dict()["feature_set_version"] == "v1"


def test_panel_round_trips_through_disk(session, seeded, tmp_path) -> None:
    rows = list(build_panel(session, dt.date(2026, 8, 1), dt.date(2026, 9, 30)))
    written = write_panel(rows, tmp_path)
    assert written
    frame = read_panel(tmp_path)
    assert len(frame) == len(rows)


def test_panel_partitions_by_year(session, seeded, tmp_path) -> None:
    rows = list(build_panel(session, dt.date(2026, 12, 1), dt.date(2027, 1, 31)))
    written = write_panel(rows, tmp_path)
    assert len(written) == 2
    assert len(read_panel(tmp_path, years=[2026])) < len(rows)
