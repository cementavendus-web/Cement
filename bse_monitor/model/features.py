"""Point-in-time features for the company x holder x week panel.

One rule governs this module: **every feature function takes ``as_of`` and reads
only rows strictly before it.** No function here may call ``date.today()``, and
a test asserts that by source inspection — because a single accidental
``today()`` in a feature silently trains the model on the future and produces an
evaluation that looks excellent and is worthless.

Calendar features additionally filter on ``UpcomingTrigger.known_from_date``,
not ``trigger_date``: a lock-in expiry that will happen in March was only
*knowable* from the filing that disclosed the allotment, and a model that sees
it earlier than the market did is cheating.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import repository as repo
from ..database.models import Disposal, EventFiling, UpcomingTrigger

CRORE = 1e7

# Trigger types that release stock, in the order the brief should prefer them.
SUPPLY_TRIGGERS = (
    "ANCHOR_LOCKIN_30D",
    "ANCHOR_LOCKIN_90D",
    "PREIPO_LOCKIN_6M",
    "PROMOTER_EXCESS_LOCKIN_6M",
    "PROMOTER_MPC_LOCKIN_18M",
    "CAPEX_OBJECTS_1Y",
    "CAPEX_OBJECTS_3Y",
    "MPS_COMPLIANCE_25PCT",
)

SELL_CATEGORIES = ("OFS", "BlockDeal", "InvestorExit", "PromoterSale")

# The v1 feature set. LLM-derived columns are deliberately absent: the LLM layer
# only produces rows from go-live, so including them would make the historical
# panel mostly null and the model would learn "llm_present" as a proxy for
# "recent". They enter as a separate block in a later retrain.
FEATURE_NAMES: tuple[str, ...] = (
    "days_to_nearest_trigger",
    "triggers_within_30d",
    "triggers_within_60d",
    "triggers_within_90d",
    "nearest_trigger_is_anchor",
    "nearest_trigger_is_promoter",
    "days_since_ipo",
    "holder_prior_sells_here",
    "holder_prior_sells_anywhere",
    "days_since_holder_last_sell",
    "holder_weeks_observed",
    "holder_sell_rate",
    "last_stake_pct",
    "stake_value_cr",
    "days_of_adv_to_liquidate",
    "filings_4w",
    "filings_12w",
    "max_score_12w",
    "sell_filings_12w",
    "is_marquee",
)

FEATURE_SET_VERSION = "v1"


@dataclasses.dataclass
class PanelKey:
    company_id: int
    holder_key: str
    week_end: dt.date


def week_ending_friday(day: dt.date) -> dt.date:
    """The Friday of ``day``'s week. Panel rows are stamped at Friday close."""
    return day + dt.timedelta(days=(4 - day.weekday()) % 7)


def iter_week_ends(start: dt.date, end: dt.date) -> List[dt.date]:
    weeks: List[dt.date] = []
    cursor = week_ending_friday(start)
    while cursor <= end:
        weeks.append(cursor)
        cursor += dt.timedelta(days=7)
    return weeks


# --------------------------------------------------------------------------
# Calendar features
# --------------------------------------------------------------------------
def calendar_features(
    session: Session, company_id: int, as_of: dt.date
) -> Dict[str, Any]:
    """Deadlines that were *knowable* at ``as_of`` and still lie ahead."""
    rows = list(
        session.scalars(
            select(UpcomingTrigger).where(
                UpcomingTrigger.company_id == company_id,
                UpcomingTrigger.trigger_type.in_(SUPPLY_TRIGGERS),
                UpcomingTrigger.status.in_(("PENDING", "FIRED")),
                # Knowable, not merely scheduled.
                UpcomingTrigger.known_from_date.isnot(None),
                UpcomingTrigger.known_from_date <= as_of,
                UpcomingTrigger.trigger_date > as_of,
            )
        )
    )
    if not rows:
        return {
            "days_to_nearest_trigger": None,
            "triggers_within_30d": 0,
            "triggers_within_60d": 0,
            "triggers_within_90d": 0,
            "nearest_trigger_is_anchor": 0,
            "nearest_trigger_is_promoter": 0,
        }

    nearest = min(rows, key=lambda r: r.trigger_date)
    days = (nearest.trigger_date - as_of).days
    return {
        "days_to_nearest_trigger": days,
        "triggers_within_30d": sum(1 for r in rows if (r.trigger_date - as_of).days <= 30),
        "triggers_within_60d": sum(1 for r in rows if (r.trigger_date - as_of).days <= 60),
        "triggers_within_90d": sum(1 for r in rows if (r.trigger_date - as_of).days <= 90),
        "nearest_trigger_is_anchor": int(nearest.trigger_type.startswith("ANCHOR")),
        "nearest_trigger_is_promoter": int("PROMOTER" in nearest.trigger_type),
        "_nearest_type": nearest.trigger_type,
        "_nearest_date": nearest.trigger_date,
        "_nearest_subject": nearest.subject_name,
    }


def days_since_ipo(session: Session, company_id: int, as_of: dt.date) -> Optional[int]:
    row = session.scalar(
        select(EventFiling)
        .where(
            EventFiling.company_id == company_id,
            EventFiling.category == "IPO",
            EventFiling.filing_date <= as_of,
        )
        .order_by(EventFiling.filing_date)
        .limit(1)
    )
    return (as_of - row.filing_date).days if row and row.filing_date else None


# --------------------------------------------------------------------------
# Holder history
# --------------------------------------------------------------------------
def holder_features(
    session: Session, company_id: int, holder_key: str, as_of: dt.date
) -> Dict[str, Any]:
    here = list(
        session.scalars(
            select(Disposal).where(
                Disposal.company_id == company_id,
                Disposal.holder_key == holder_key,
                Disposal.side == "SELL",
                Disposal.trade_date <= as_of,
            )
        )
    )
    anywhere = list(
        session.scalars(
            select(Disposal).where(
                Disposal.holder_key == holder_key,
                Disposal.side == "SELL",
                Disposal.trade_date <= as_of,
            )
        )
    )
    all_rows = list(
        session.scalars(
            select(Disposal).where(
                Disposal.holder_key == holder_key, Disposal.trade_date <= as_of
            )
        )
    )
    first_seen = min((r.trade_date for r in all_rows), default=None)
    last_sell = max((r.trade_date for r in anywhere), default=None)
    weeks_observed = ((as_of - first_seen).days // 7) if first_seen else 0

    return {
        "holder_prior_sells_here": len(here),
        "holder_prior_sells_anywhere": len(anywhere),
        "days_since_holder_last_sell": (as_of - last_sell).days if last_sell else None,
        "holder_weeks_observed": weeks_observed,
        "holder_sell_rate": round(len(anywhere) / weeks_observed, 4) if weeks_observed else 0.0,
    }


def position_features(
    session: Session, company_id: int, holder_key: str, as_of: dt.date
) -> Dict[str, Any]:
    """Last observed stake, its rupee value, and how long it takes to sell."""
    latest = session.scalar(
        select(Disposal)
        .where(
            Disposal.company_id == company_id,
            Disposal.holder_key == holder_key,
            Disposal.trade_date <= as_of,
            Disposal.percent_of_equity.isnot(None),
        )
        .order_by(Disposal.trade_date.desc())
        .limit(1)
    )
    stake_pct = float(latest.percent_of_equity) if latest else None

    quote = repo.latest_quote(session, company_id, as_of)
    adv = repo.average_daily_volume(session, company_id, as_of)

    shares = None
    last_qty = session.scalar(
        select(Disposal)
        .where(
            Disposal.company_id == company_id,
            Disposal.holder_key == holder_key,
            Disposal.trade_date <= as_of,
            Disposal.quantity.isnot(None),
        )
        .order_by(Disposal.trade_date.desc())
        .limit(1)
    )
    if last_qty is not None:
        shares = float(last_qty.quantity)

    stake_value_cr = None
    if shares and quote and quote.close_price:
        stake_value_cr = round(shares * float(quote.close_price) / CRORE, 2)

    days_of_adv = None
    if shares and adv:
        days_of_adv = round(shares / adv, 2)

    return {
        "last_stake_pct": stake_pct,
        "stake_value_cr": stake_value_cr,
        "days_of_adv_to_liquidate": days_of_adv,
        "_shares": shares,
    }


def filing_flow_features(
    session: Session, company_id: int, as_of: dt.date
) -> Dict[str, Any]:
    since_12w = as_of - dt.timedelta(weeks=12)
    since_4w = as_of - dt.timedelta(weeks=4)
    rows = list(
        session.scalars(
            select(EventFiling).where(
                EventFiling.company_id == company_id,
                EventFiling.is_duplicate.is_(False),
                EventFiling.filing_date > since_12w,
                EventFiling.filing_date <= as_of,
            )
        )
    )
    return {
        "filings_4w": sum(1 for r in rows if r.filing_date and r.filing_date > since_4w),
        "filings_12w": len(rows),
        "max_score_12w": max((r.opportunity_score or 0 for r in rows), default=0),
        "sell_filings_12w": sum(1 for r in rows if r.category in SELL_CATEGORIES),
    }


def build_features(
    session: Session,
    company_id: int,
    holder_key: str,
    as_of: dt.date,
    *,
    is_marquee: bool = False,
) -> Dict[str, Any]:
    """All v1 features for one panel cell, as of ``as_of``."""
    row: Dict[str, Any] = {}
    row.update(calendar_features(session, company_id, as_of))
    row.update(holder_features(session, company_id, holder_key, as_of))
    row.update(position_features(session, company_id, holder_key, as_of))
    row.update(filing_flow_features(session, company_id, as_of))
    row["days_since_ipo"] = days_since_ipo(session, company_id, as_of)
    row["is_marquee"] = int(is_marquee)
    return row
