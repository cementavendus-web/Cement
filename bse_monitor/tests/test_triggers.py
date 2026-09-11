"""Forward calendar: date arithmetic, derivation, idempotent persistence."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.exc import IntegrityError

from bse_monitor.database import repository as repo
from bse_monitor.database.models import UpcomingTrigger
from bse_monitor.triggers import refresh as refresh_mod
from bse_monitor.triggers.dates import add_days, add_hours, add_months, add_years
from bse_monitor.triggers.rules import (
    DEFAULT_OFFSETS,
    AnchorSet,
    Subject,
    dedupe_key,
    derive_triggers,
)

IPO_TEXT = "Objects of the issue include capital expenditure for setting up of a new plant."
MPS_TEXT = "The Company is not in compliance with the minimum public shareholding requirement."


# -- date arithmetic -------------------------------------------------------
@pytest.mark.parametrize(
    "base,months,expected",
    [
        ((2025, 8, 31), 6, (2026, 2, 28)),
        ((2027, 8, 31), 6, (2028, 2, 29)),   # leap year
        ((2025, 3, 31), 1, (2025, 4, 30)),
        ((2025, 1, 31), 1, (2025, 2, 28)),
        ((2025, 12, 31), 1, (2026, 1, 31)),
        ((2025, 7, 15), 18, (2027, 1, 15)),
    ],
)
def test_add_months_clamps_to_month_end(base, months, expected) -> None:
    assert add_months(dt.date(*base), months) == dt.date(*expected)


def test_add_years_handles_leap_day() -> None:
    assert add_years(dt.date(2028, 2, 29), 1) == dt.date(2029, 2, 28)


def test_add_months_is_not_a_timedelta_approximation() -> None:
    """Six calendar months is not 182 days, and a statutory deadline needs the former."""
    base = dt.date(2025, 8, 31)
    assert add_months(base, 6) == dt.date(2026, 2, 28)
    assert add_months(base, 6) != add_days(base, 182)


def test_add_hours_crosses_midnight() -> None:
    assert add_hours(dt.datetime(2026, 3, 13, 18, 30), 48) == dt.datetime(2026, 3, 15, 18, 30)


# -- derivation ------------------------------------------------------------
@pytest.fixture()
def ipo_anchors() -> AnchorSet:
    return AnchorSet(
        allotment_date=dt.date(2026, 3, 10),
        listing_date=dt.date(2026, 3, 13),
        confidence=0.9,
    )


def _by_type(rows):
    return {row.trigger_type: row for row in rows}


def test_anchor_lockin_emits_two_separate_rows(ipo_anchors) -> None:
    rows = _by_type(derive_triggers(category="IPO", text=IPO_TEXT, anchors=ipo_anchors))
    assert rows["ANCHOR_LOCKIN_30D"].trigger_date == dt.date(2026, 4, 9)
    assert rows["ANCHOR_LOCKIN_90D"].trigger_date == dt.date(2026, 6, 8)


def test_preipo_lockin_six_months_from_allotment(ipo_anchors) -> None:
    rows = _by_type(derive_triggers(category="IPO", text=IPO_TEXT, anchors=ipo_anchors))
    assert rows["PREIPO_LOCKIN_6M"].trigger_date == dt.date(2026, 9, 10)
    assert rows["PREIPO_LOCKIN_6M"].anchor_basis == "ALLOTMENT"


def test_preipo_lockin_falls_back_to_listing() -> None:
    anchors = AnchorSet(listing_date=dt.date(2026, 3, 13))
    rows = _by_type(derive_triggers(category="IPO", text=IPO_TEXT, anchors=anchors))
    assert rows["PREIPO_LOCKIN_6M"].anchor_basis == "LISTING"
    assert rows["PREIPO_LOCKIN_6M"].evidence["fallback_used"] is True


def test_promoter_lockins_six_and_eighteen_months(ipo_anchors) -> None:
    rows = _by_type(derive_triggers(category="IPO", text=IPO_TEXT, anchors=ipo_anchors))
    assert rows["PROMOTER_EXCESS_LOCKIN_6M"].trigger_date == dt.date(2026, 9, 10)
    assert rows["PROMOTER_MPC_LOCKIN_18M"].trigger_date == dt.date(2027, 9, 10)


def test_capex_rules_require_the_capex_cue(ipo_anchors) -> None:
    """A capex deadline invented for every IPO would flood the calendar."""
    with_cue = _by_type(derive_triggers(category="IPO", text=IPO_TEXT, anchors=ipo_anchors))
    without = _by_type(derive_triggers(category="IPO", text="plain ipo", anchors=ipo_anchors))
    assert "CAPEX_OBJECTS_1Y" in with_cue and "CAPEX_OBJECTS_3Y" in with_cue
    assert "CAPEX_OBJECTS_1Y" not in without


def test_mps_requires_the_shortfall_cue(ipo_anchors) -> None:
    rows = _by_type(derive_triggers(category="IPO", text=MPS_TEXT, anchors=ipo_anchors))
    assert rows["MPS_COMPLIANCE_25PCT"].trigger_date == dt.date(2029, 3, 13)
    assert rows["MPS_COMPLIANCE_25PCT"].anchor_basis == "LISTING"


def test_qip_resolution_expires_after_365_days() -> None:
    anchors = AnchorSet(resolution_date=dt.date(2026, 2, 2))
    rows = _by_type(derive_triggers(category="QIP", anchors=anchors))
    assert rows["QIP_RESOLUTION_EXPIRY_365D"].trigger_date == dt.date(2027, 2, 2)


def test_trading_window_reopens_48h_after_results() -> None:
    anchors = AnchorSet(results_datetime=dt.datetime(2026, 5, 14, 16, 30))
    rows = _by_type(
        derive_triggers(category="Other", text="audited results for the quarter", anchors=anchors)
    )
    row = rows["TRADING_WINDOW_REOPEN_48H"]
    assert row.trigger_datetime == dt.datetime(2026, 5, 16, 16, 30)
    assert row.trigger_date == dt.date(2026, 5, 16)


def test_trading_window_rolls_to_the_next_day() -> None:
    """A late-evening declaration reopens two calendar days later, not one."""
    anchors = AnchorSet(results_datetime=dt.datetime(2026, 5, 14, 23, 30))
    rows = _by_type(
        derive_triggers(category="Other", text="quarterly results", anchors=anchors)
    )
    assert rows["TRADING_WINDOW_REOPEN_48H"].trigger_date == dt.date(2026, 5, 16)


def test_no_anchor_yields_no_triggers() -> None:
    """The calendar never invents a deadline from a filing that stated none."""
    assert derive_triggers(category="IPO", text=IPO_TEXT, anchors=AnchorSet()) == []


def test_offsets_override_changes_derived_dates(ipo_anchors) -> None:
    rows = _by_type(
        derive_triggers(
            category="IPO", text=IPO_TEXT, anchors=ipo_anchors,
            offsets={**DEFAULT_OFFSETS, "anchor_lockin_long_days": 180},
        )
    )
    assert rows["ANCHOR_LOCKIN_90D"].trigger_date == dt.date(2026, 9, 6)


def test_derivation_is_pure_and_deterministic(ipo_anchors) -> None:
    first = derive_triggers(category="IPO", text=IPO_TEXT, anchors=ipo_anchors)
    second = derive_triggers(category="IPO", text=IPO_TEXT, anchors=ipo_anchors)
    assert [c.dedupe_key(1) for c in first] == [c.dedupe_key(1) for c in second]


def test_subjects_produce_distinct_dedupe_keys(ipo_anchors) -> None:
    rows = derive_triggers(
        category="IPO", text=IPO_TEXT, anchors=ipo_anchors,
        subjects=[Subject("Blackstone", "ANCHOR_INVESTOR"), Subject("GIC", "ANCHOR_INVESTOR")],
    )
    anchor_30 = [r for r in rows if r.trigger_type == "ANCHOR_LOCKIN_30D"]
    assert len({r.dedupe_key(1) for r in anchor_30}) == 2


def test_company_wide_row_when_no_subject_identified(ipo_anchors) -> None:
    rows = _by_type(derive_triggers(category="IPO", text=IPO_TEXT, anchors=ipo_anchors))
    assert rows["ANCHOR_LOCKIN_30D"].subject_key == ""


def test_dedupe_key_is_stable_across_calls() -> None:
    args = (7, "ANCHOR_LOCKIN_30D", dt.date(2026, 3, 10), "blackstone", "v1")
    assert dedupe_key(*args) == dedupe_key(*args)
    assert dedupe_key(*args) != dedupe_key(8, *args[1:])


# -- persistence and idempotency ------------------------------------------
def test_refresh_is_idempotent(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    first = refresh_mod.refresh_for_filing(session, filing)
    ids_first = {t.id for t in session.query(UpcomingTrigger).all()}
    second = refresh_mod.refresh_for_filing(session, filing)
    ids_second = {t.id for t in session.query(UpcomingTrigger).all()}

    assert first.created > 0
    assert second.created == 0 and second.updated == first.created
    assert ids_first == ids_second


def test_triggers_survive_a_transaction_rewrite(session, calendar_filing) -> None:
    """The exact failure the calendar must not inherit from `replace_transactions`."""
    _company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    before = {t.id for t in session.query(UpcomingTrigger).all()}

    repo.replace_transactions(session, filing.id, [{"transaction_type": "PRIMARY_ISSUE"}])
    repo.replace_transactions(session, filing.id, [{"transaction_type": "PRIMARY_ISSUE"}])
    refresh_mod.refresh_for_filing(session, filing)

    assert {t.id for t in session.query(UpcomingTrigger).all()} == before


def test_rows_that_no_longer_derive_are_cancelled_not_deleted(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    total = session.query(UpcomingTrigger).count()

    filing.search_text = "Routine disclosure with no dates."
    session.flush()
    refresh_mod.refresh_for_filing(session, filing)

    assert session.query(UpcomingTrigger).count() == total          # nothing deleted
    assert session.query(UpcomingTrigger).filter_by(status="CANCELLED").count() > 0


def test_rule_version_bump_supersedes(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing, rule_version="v1")
    stats = refresh_mod.refresh_for_filing(session, filing, rule_version="v2")

    assert stats.superseded > 0
    old = session.query(UpcomingTrigger).filter_by(rule_version="v1", status="SUPERSEDED").first()
    assert old is not None and old.superseded_by_id is not None


def test_upsert_skips_none_so_sparse_passes_do_not_null_data(session, calendar_filing) -> None:
    company, filing = calendar_filing
    key = dedupe_key(company.id, "ANCHOR_LOCKIN_30D", dt.date(2026, 3, 10), "", "v1")
    base = {
        "dedupe_key": key, "company_id": company.id, "trigger_type": "ANCHOR_LOCKIN_30D",
        "anchor_date": dt.date(2026, 3, 10), "anchor_basis": "ALLOTMENT",
        "trigger_date": dt.date(2026, 4, 9), "notes": "original", "confidence": 0.9,
    }
    repo.upsert_trigger(session, base)
    repo.upsert_trigger(session, {**base, "notes": None, "confidence": None})
    row = session.query(UpcomingTrigger).filter_by(dedupe_key=key).one()
    assert row.notes == "original" and row.confidence == 0.9


def test_known_from_date_is_the_filing_date(session, calendar_filing) -> None:
    """The point-in-time guard the ML panel filters on."""
    _company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    assert all(
        t.known_from_date == filing.filing_date for t in session.query(UpcomingTrigger).all()
    )


def test_natural_key_uniqueness_is_enforced(session, calendar_filing) -> None:
    company, _filing = calendar_filing
    payload = {
        "dedupe_key": "a" * 64, "company_id": company.id, "trigger_type": "ANCHOR_LOCKIN_30D",
        "anchor_date": dt.date(2026, 3, 10), "anchor_basis": "ALLOTMENT",
        "subject_key": "", "rule_version": "v1", "trigger_date": dt.date(2026, 4, 9),
    }
    repo.upsert_trigger(session, payload)
    with pytest.raises(IntegrityError):
        repo.upsert_trigger(session, {**payload, "dedupe_key": "b" * 64})
        session.flush()


@pytest.mark.parametrize(
    "field,value",
    [("status", "NOPE"), ("trigger_type", "MADE_UP"), ("anchor_basis", "GUESS")],
)
def test_check_constraints_reject_unknown_values(session, calendar_filing, field, value) -> None:
    company, _filing = calendar_filing
    payload = {
        "dedupe_key": f"c{field}".ljust(64, "0"), "company_id": company.id,
        "trigger_type": "ANCHOR_LOCKIN_30D", "anchor_date": dt.date(2026, 3, 10),
        "anchor_basis": "ALLOTMENT", "trigger_date": dt.date(2026, 4, 9), field: value,
    }
    with pytest.raises(IntegrityError):
        repo.upsert_trigger(session, payload)
        session.flush()


def test_backwards_deadline_is_rejected(session, calendar_filing) -> None:
    company, _filing = calendar_filing
    with pytest.raises(IntegrityError):
        repo.upsert_trigger(session, {
            "dedupe_key": "d" * 64, "company_id": company.id,
            "trigger_type": "ANCHOR_LOCKIN_30D", "anchor_date": dt.date(2026, 3, 10),
            "anchor_basis": "ALLOTMENT", "trigger_date": dt.date(2026, 1, 1),
        })
        session.flush()


# -- queries ---------------------------------------------------------------
def test_days_to_trigger_is_never_stored() -> None:
    assert "days_to_trigger" not in UpcomingTrigger.__table__.columns


def test_triggers_due_computes_days_from_injected_as_of(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    rows = repo.triggers_due(session, as_of=dt.date(2026, 4, 1), within_days=30)
    assert rows
    for trigger, days in rows:
        assert days == (trigger.trigger_date - dt.date(2026, 4, 1)).days


def test_triggers_due_filters_by_horizon_and_confidence(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    near = repo.triggers_due(session, as_of=dt.date(2026, 4, 1), within_days=10)
    far = repo.triggers_due(session, as_of=dt.date(2026, 4, 1), within_days=400)
    assert len(near) < len(far)
    assert repo.triggers_due(session, as_of=dt.date(2026, 4, 1), min_confidence=0.99) == []


def test_expire_moves_past_pending_to_fired(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    fired = repo.expire_triggers(session, as_of=dt.date(2027, 1, 1))
    assert fired > 0
    row = session.query(UpcomingTrigger).filter_by(status="FIRED").first()
    assert row is not None and row.fired_at is not None


def test_trigger_alerts_separate_by_horizon(session, calendar_filing) -> None:
    """T-30 and T-7 for the same deadline must both fire; a re-run must not."""
    _company, filing = calendar_filing
    refresh_mod.refresh_for_filing(session, filing)
    trigger = session.query(UpcomingTrigger).first()

    _, first = repo.queue_alert(
        session, trigger_id=trigger.id, horizon=30, alert_date=dt.date(2026, 3, 10),
        priority="MEDIUM", channel="csv", title="t", body="b",
    )
    _, second = repo.queue_alert(
        session, trigger_id=trigger.id, horizon=7, alert_date=dt.date(2026, 4, 2),
        priority="HIGH", channel="csv", title="t", body="b",
    )
    _, repeat = repo.queue_alert(
        session, trigger_id=trigger.id, horizon=30, alert_date=dt.date(2026, 3, 10),
        priority="MEDIUM", channel="csv", title="t", body="b",
    )
    assert first is True and second is True and repeat is False
