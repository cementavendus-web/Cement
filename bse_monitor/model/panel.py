"""The company x holder x week panel.

The holder universe is bootstrapped from observed events: a holder enters the
panel the first week it is seen in any disposal, QIP allotment or exit filing,
and stays. That is cheap and strictly point-in-time, but it is *survivorship
shaped* — a holder sitting on 8% and doing nothing is invisible until its first
trade. :class:`HolderUniverse` is an interface precisely so a quarterly
shareholding-pattern source can replace the bootstrap later without touching the
feature or training code.

Storage is Parquet partitioned by year rather than SQLite rows: the operational
database stays small, and a walk-forward fold loads as one file read. Measured
here, 2.6M rows write to SQLite in ~11s and cost ~560MB, so either works — this
is a preference, not a necessity.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import Disposal, Investor
from .features import (
    FEATURE_NAMES,
    FEATURE_SET_VERSION,
    build_features,
    iter_week_ends,
)
from .labels import LabelSpec, label_row

log = logging.getLogger(__name__)

# Identity columns carried alongside the features, for joining and for the brief.
ID_COLUMNS = ("company_id", "company", "bse_code", "holder_key", "holder", "week_end", "year")
LABEL_COLUMNS = ("label", "label_pct", "label_value_cr", "lead_time_days")


@dataclasses.dataclass(frozen=True)
class HolderSlot:
    company_id: int
    company_name: str
    bse_code: Optional[str]
    holder_key: str
    holder_name: str
    first_seen: dt.date
    is_marquee: bool = False


class HolderUniverse(Protocol):
    """Which (company, holder) pairs exist in a given week."""

    def slots_as_of(self, session: Session, as_of: dt.date) -> Sequence[HolderSlot]:
        ...


class BootstrapUniverse:
    """Holders discovered from their own observed activity.

    Known limitation, stated rather than hidden: a holder is invisible until its
    first observed event, so the panel under-represents dormant positions. A
    Reg-31 shareholding-pattern source would fix this; that is the documented
    upgrade path and the reason this is an interface.
    """

    def __init__(self, min_first_seen: Optional[dt.date] = None) -> None:
        self.min_first_seen = min_first_seen
        self._cache: Optional[List[HolderSlot]] = None

    def _load(self, session: Session) -> List[HolderSlot]:
        if self._cache is not None:
            return self._cache

        marquee = {
            row.normalized_name
            for row in session.scalars(select(Investor).where(Investor.is_marquee.is_(True)))
        }

        slots: Dict[tuple, HolderSlot] = {}
        for row in session.scalars(select(Disposal).order_by(Disposal.trade_date)):
            if row.trade_date is None or row.company_id is None:
                continue
            key = (row.company_id, row.holder_key)
            if key in slots:
                continue
            company = row.company
            slots[key] = HolderSlot(
                company_id=row.company_id,
                company_name=getattr(company, "name", "") or "",
                bse_code=getattr(company, "bse_code", None),
                holder_key=row.holder_key,
                holder_name=row.holder_name,
                first_seen=row.trade_date,
                is_marquee=row.holder_key in marquee,
            )
        self._cache = list(slots.values())
        return self._cache

    def slots_as_of(self, session: Session, as_of: dt.date) -> Sequence[HolderSlot]:
        """Only holders already observed — never a look-ahead into the future."""
        return [slot for slot in self._load(session) if slot.first_seen <= as_of]


@dataclasses.dataclass
class PanelStats:
    rows: int = 0
    positives: int = 0
    weeks: int = 0
    companies: int = 0
    holders: int = 0

    @property
    def base_rate(self) -> float:
        return round(self.positives / self.rows, 6) if self.rows else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rows": self.rows,
            "positives": self.positives,
            "base_rate": self.base_rate,
            "weeks": self.weeks,
            "companies": self.companies,
            "holders": self.holders,
            "feature_set_version": FEATURE_SET_VERSION,
        }


def build_panel(
    session: Session,
    start: dt.date,
    end: dt.date,
    *,
    universe: Optional[HolderUniverse] = None,
    spec: Optional[LabelSpec] = None,
    label_rows: bool = True,
) -> Iterator[Dict[str, Any]]:
    """Yield one row per (company, holder, week) in ``[start, end]``.

    ``label_rows=False`` is the inference path: at scoring time the forward
    window has not happened yet, so there is no label to compute.
    """
    universe = universe or BootstrapUniverse()
    spec = spec or LabelSpec()

    for week_end in iter_week_ends(start, end):
        for slot in universe.slots_as_of(session, week_end):
            features = build_features(
                session,
                slot.company_id,
                slot.holder_key,
                week_end,
                is_marquee=slot.is_marquee,
            )
            row: Dict[str, Any] = {
                "company_id": slot.company_id,
                "company": slot.company_name,
                "bse_code": slot.bse_code or "",
                "holder_key": slot.holder_key,
                "holder": slot.holder_name,
                "week_end": week_end,
                "year": week_end.year,
            }
            row.update({name: features.get(name) for name in FEATURE_NAMES})
            # Underscore keys carry display context (nearest trigger type/date)
            # that the brief needs but the model must not train on.
            row.update({k: v for k, v in features.items() if k.startswith("_")})

            if label_rows:
                result = label_row(session, slot.company_id, slot.holder_key, week_end, spec)
                row.update(
                    {
                        "label": result.label,
                        "label_pct": result.total_pct,
                        "label_value_cr": round(result.total_value_inr / 1e7, 2),
                        "lead_time_days": result.lead_time_days,
                    }
                )
            yield row


def panel_stats(rows: Sequence[Dict[str, Any]]) -> PanelStats:
    return PanelStats(
        rows=len(rows),
        positives=sum(int(r.get("label") or 0) for r in rows),
        weeks=len({r["week_end"] for r in rows}),
        companies=len({r["company_id"] for r in rows}),
        holders=len({r["holder_key"] for r in rows}),
    )


def write_panel(rows: Iterable[Dict[str, Any]], out_dir: str | Path) -> List[str]:
    """Write the panel as Parquet partitioned by year, CSV if pyarrow is absent."""
    import collections

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    by_year: Dict[int, List[Dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_year[row["year"]].append(row)

    written: List[str] = []
    for year, year_rows in sorted(by_year.items()):
        try:
            import pandas as pd

            frame = pd.DataFrame(year_rows)
            try:
                path = target / f"panel_{year}.parquet"
                frame.to_parquet(path, index=False)
            except Exception:
                # pyarrow missing: CSV keeps the pipeline runnable.
                path = target / f"panel_{year}.csv"
                frame.to_csv(path, index=False)
        except ImportError:  # pragma: no cover - pandas is a hard dependency
            import csv

            path = target / f"panel_{year}.csv"
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(year_rows[0]))
                writer.writeheader()
                writer.writerows(year_rows)
        written.append(str(path))
        log.info("Panel partition written", extra={"year": year, "rows": len(year_rows)})
    return written


def read_panel(out_dir: str | Path, years: Optional[Sequence[int]] = None):
    """Load panel partitions into one DataFrame."""
    import pandas as pd

    target = Path(out_dir)
    frames = []
    for path in sorted(target.glob("panel_*")):
        year = int(path.stem.split("_")[1])
        if years is not None and year not in years:
            continue
        frames.append(
            pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        )
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
