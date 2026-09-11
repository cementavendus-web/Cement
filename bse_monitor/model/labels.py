"""Label construction for the sell-down model.

    label(company, holder, week_end) = 1 if, within (week_end, week_end + 60d],
        that holder's disposals in that company sum to >= 0.5% of equity
        OR >= Rs 100 crore.

Two properties matter more than the thresholds:

* **Strictly forward.** The window opens *after* ``week_end``, never on it. A
  trade on the boundary day belongs to the features, not the label.
* **Deduplicated before summing.** The same sale is reported by both exchanges
  and often again in a SAST filing; summing raw rows would treble a single
  disposal and label weeks that saw one trade as if they saw three.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import Disposal

log = logging.getLogger(__name__)

CRORE = 1e7

DEFAULT_HORIZON_DAYS = 60
DEFAULT_PCT_THRESHOLD = 0.5
DEFAULT_VALUE_THRESHOLD_INR = 100 * CRORE


@dataclasses.dataclass
class LabelSpec:
    horizon_days: int = DEFAULT_HORIZON_DAYS
    pct_threshold: float = DEFAULT_PCT_THRESHOLD
    value_threshold_inr: float = DEFAULT_VALUE_THRESHOLD_INR

    def window(self, week_end: dt.date) -> tuple[dt.date, dt.date]:
        """Half-open and strictly forward: ``(week_end, week_end + horizon]``."""
        return week_end, week_end + dt.timedelta(days=self.horizon_days)


@dataclasses.dataclass
class LabelResult:
    label: int
    total_pct: float
    total_value_inr: float
    first_sale_date: Optional[dt.date]
    lead_time_days: Optional[int]
    n_disposals: int

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def dedupe_disposals(rows: Iterable[Disposal]) -> List[Disposal]:
    """Collapse the same economic trade reported by several sources."""
    seen: set[tuple] = set()
    out: List[Disposal] = []
    for row in rows:
        quantity = float(row.quantity) if row.quantity else None
        key = (
            row.company_id,
            row.holder_key,
            row.trade_date,
            row.side,
            round(quantity) if quantity else None,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def label_for_window(
    disposals: Sequence[Disposal], week_end: dt.date, spec: Optional[LabelSpec] = None
) -> LabelResult:
    """Apply the rule to one holder-week's forward disposals."""
    spec = spec or LabelSpec()
    start, end = spec.window(week_end)
    in_window = [
        row for row in dedupe_disposals(disposals)
        if row.side == "SELL" and row.trade_date is not None and start < row.trade_date <= end
    ]

    total_pct = sum(float(r.percent_of_equity or 0.0) for r in in_window)
    total_value = sum(float(r.value_inr or 0.0) for r in in_window)
    first_sale = min((r.trade_date for r in in_window), default=None)

    hit = int(total_pct >= spec.pct_threshold or total_value >= spec.value_threshold_inr)
    return LabelResult(
        label=hit,
        total_pct=round(total_pct, 4),
        total_value_inr=round(total_value, 2),
        first_sale_date=first_sale,
        lead_time_days=(first_sale - week_end).days if first_sale else None,
        n_disposals=len(in_window),
    )


def load_forward_disposals(
    session: Session,
    company_id: int,
    holder_key: str,
    week_end: dt.date,
    spec: Optional[LabelSpec] = None,
) -> List[Disposal]:
    spec = spec or LabelSpec()
    start, end = spec.window(week_end)
    return list(
        session.scalars(
            select(Disposal).where(
                Disposal.company_id == company_id,
                Disposal.holder_key == holder_key,
                Disposal.side == "SELL",
                Disposal.trade_date > start,
                Disposal.trade_date <= end,
            )
        )
    )


def label_row(
    session: Session,
    company_id: int,
    holder_key: str,
    week_end: dt.date,
    spec: Optional[LabelSpec] = None,
) -> LabelResult:
    return label_for_window(
        load_forward_disposals(session, company_id, holder_key, week_end, spec),
        week_end,
        spec,
    )


def coverage_report(session: Session) -> List[Dict[str, Any]]:
    """Per-year label coverage by source.

    SAST 29(2) has no bulk archive, so its contribution is best-effort by
    design. This report is how that shows up as a number rather than an
    assumption: a year where SAST contributes nothing is a year whose labels
    miss every off-market sale.
    """
    rows = list(session.scalars(select(Disposal).where(Disposal.side == "SELL")))
    by_year: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        if row.trade_date is None:
            continue
        entry = by_year.setdefault(
            row.trade_date.year,
            {"year": row.trade_date.year, "total": 0, "sources": {}, "with_value": 0, "with_pct": 0},
        )
        entry["total"] += 1
        entry["sources"][row.source] = entry["sources"].get(row.source, 0) + 1
        entry["with_value"] += int(bool(row.value_inr))
        entry["with_pct"] += int(bool(row.percent_of_equity))

    out: List[Dict[str, Any]] = []
    for year in sorted(by_year):
        entry = by_year[year]
        total = entry["total"] or 1
        entry["sast_share"] = round(entry["sources"].get("SAST", 0) / total, 4)
        entry["value_coverage"] = round(entry["with_value"] / total, 4)
        entry["pct_coverage"] = round(entry["with_pct"] / total, 4)
        entry["sources"] = dict(sorted(entry["sources"].items()))
        out.append(entry)
    return out
