"""The daily 08:00 IST ranked brief.

One idempotent command produces the whole thing: refresh the calendar, build
today's panel with no labels (the forward window has not happened yet), score it
with the model if one is trained and the baseline otherwise, attach the top-3
reasons, write the CSV and send it.

Ranking always works. If LightGBM or the model artefact is missing, the baseline
ranks and its own reasons fill the explanation column — the brief degrades in
quality, never in availability.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..model.baseline import baseline_scores
from ..model.panel import BootstrapUniverse, build_panel
from ..model.train import load_model, shap_reasons

log = logging.getLogger(__name__)

BRIEF_COLUMNS = (
    "rank",
    "company",
    "bse_code",
    "holder",
    "holder_type",
    "stake_cr",
    "days_to_trigger",
    "trigger_type",
    "score",
    "reason_1",
    "reason_2",
    "reason_3",
)

# Readable labels, shared with the calendar report.
from .calendar import label_for  # noqa: E402  (kept local to avoid a cycle at import)


def last_completed_friday(day: dt.date) -> dt.date:
    """The most recent Friday on or before ``day``.

    Not ``week_ending_friday``: on a Monday that returns the *coming* Friday,
    which has not happened, so its features would be built from a week the
    market has not finished trading.
    """
    return day - dt.timedelta(days=(day.weekday() - 4) % 7)


@dataclasses.dataclass
class BriefResult:
    as_of: dt.date
    rows: List[Dict[str, Any]]
    csv_path: Optional[str] = None
    scored_by: str = "baseline"
    candidates: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "rows": len(self.rows),
            "candidates": self.candidates,
            "scored_by": self.scored_by,
            "csv_path": self.csv_path,
        }


def build_brief(
    session: Session,
    *,
    as_of: Optional[dt.date] = None,
    top_n: int = 40,
    model_path: Optional[str] = None,
    min_score: float = 0.0,
) -> BriefResult:
    """Rank every live (company, holder) pair for one date."""
    day = as_of or dt.date.today()

    # The most recent completed week. Spanning several week-ends would list the
    # same position once per week, which is noise, not signal.
    week_end = last_completed_friday(day)

    # No labels: the 60-day forward window starts now.
    candidates = list(
        build_panel(
            session,
            week_end,
            week_end,
            universe=BootstrapUniverse(),
            label_rows=False,
        )
    )
    # One line per (company, holder) even if the panel widens later.
    deduped: Dict[tuple, Dict[str, Any]] = {}
    for row in candidates:
        deduped.setdefault((row["company_id"], row["holder_key"]), row)
    candidates = list(deduped.values())

    if not candidates:
        return BriefResult(as_of=day, rows=[], candidates=0)

    model = load_model(model_path) if model_path else None
    if model is not None:
        try:
            from ..model.train import _frame

            scores = model.predict(_frame(candidates))
            scored_by = "model"
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Model scoring failed; using baseline", extra={"error": str(exc)})
            scores, scored_by = baseline_scores(candidates), "baseline"
    else:
        scores, scored_by = baseline_scores(candidates), "baseline"

    ranked = sorted(zip(candidates, scores), key=lambda pair: -pair[1])[:top_n]
    ranked = [(row, score) for row, score in ranked if score >= min_score]
    reasons = shap_reasons(model, [row for row, _ in ranked], top_n=3)

    rows: List[Dict[str, Any]] = []
    for position, ((candidate, score), why) in enumerate(zip(ranked, reasons), start=1):
        why = list(why) + ["", "", ""]
        days = candidate.get("days_to_nearest_trigger")
        trigger_code = candidate.get("_nearest_type")
        rows.append(
            {
                "rank": position,
                "company": candidate.get("company", ""),
                "bse_code": candidate.get("bse_code", ""),
                "holder": candidate.get("holder", ""),
                "holder_type": "Marquee" if candidate.get("is_marquee") else "Other",
                "stake_cr": (
                    f"{candidate['stake_value_cr']:,.1f}"
                    if candidate.get("stake_value_cr") is not None
                    else ""
                ),
                "days_to_trigger": days if days is not None else "",
                "trigger_type": label_for(trigger_code) if trigger_code else "",
                "score": round(float(score), 4),
                "reason_1": why[0],
                "reason_2": why[1],
                "reason_3": why[2],
            }
        )

    return BriefResult(
        as_of=day, rows=rows, scored_by=scored_by, candidates=len(candidates)
    )


def write_brief_csv(result: BriefResult, out_dir: str | Path) -> str:
    """Own writer, not CsvChannel: its fixed COLUMNS tuple with
    ``extrasaction='ignore'`` would silently drop every brief-specific field."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"sell_down_brief_{result.as_of.isoformat()}.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(BRIEF_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.rows)
    result.csv_path = str(path)
    log.info("Brief CSV written", extra={"path": str(path), "rows": len(result.rows)})
    return str(path)


def brief_body(result: BriefResult, limit: int = 15) -> str:
    """Plain-text email body. The full sheet rides as the attachment."""
    header = (
        f"BSE sell-down brief — {result.as_of.isoformat()}\n"
        f"{len(result.rows)} ranked of {result.candidates} live holder positions "
        f"(scored by {result.scored_by})\n"
    )
    if not result.rows:
        return header + "\nNothing above threshold today."

    lines = [header, ""]
    for row in result.rows[:limit]:
        trigger = f" · {row['trigger_type']}" if row["trigger_type"] else ""
        days = f" · T-{row['days_to_trigger']}" if row["days_to_trigger"] != "" else ""
        stake = f" · Rs {row['stake_cr']} Cr" if row["stake_cr"] else ""
        lines.append(f"{row['rank']:>2}. {row['company']} — {row['holder']}{stake}{days}{trigger}")
        why = [row[key] for key in ("reason_1", "reason_2", "reason_3") if row[key]]
        if why:
            lines.append(f"     {'; '.join(why)}")
    if len(result.rows) > limit:
        lines.append(f"\n… {len(result.rows) - limit} more in the attached CSV.")
    return "\n".join(lines)
