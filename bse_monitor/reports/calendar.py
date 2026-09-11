"""Forward-calendar reporting views."""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..database import repository as repo

CALENDAR_COLUMNS = (
    "trigger_date",
    "days_to_trigger",
    "company",
    "bse_code",
    "trigger_type",
    "subject",
    "subject_type",
    "basis",
    "anchor_date",
    "stake_pct",
    "confidence",
    "status",
)

# Human labels for the brief; the raw enum is unreadable in an email.
TRIGGER_LABELS: Dict[str, str] = {
    "ANCHOR_LOCKIN_30D": "Anchor lock-in (30d tranche)",
    "ANCHOR_LOCKIN_90D": "Anchor lock-in (90d balance)",
    "PREIPO_LOCKIN_6M": "Pre-IPO shareholder lock-in",
    "PROMOTER_EXCESS_LOCKIN_6M": "Promoter excess-holding lock-in",
    "PROMOTER_MPC_LOCKIN_18M": "Promoter minimum-contribution lock-in",
    "CAPEX_OBJECTS_1Y": "Promoter lock-in (capex objects, 1y)",
    "CAPEX_OBJECTS_3Y": "Promoter lock-in (capex objects, 3y)",
    "MPS_COMPLIANCE_25PCT": "Minimum public shareholding 25%",
    "QIP_RESOLUTION_EXPIRY_365D": "QIP resolution expiry",
    "TRADING_WINDOW_REOPEN_48H": "Trading window reopens",
}


def label_for(trigger_type: str) -> str:
    return TRIGGER_LABELS.get(trigger_type, trigger_type)


def upcoming_triggers_report(
    session: Session,
    *,
    as_of: Optional[dt.date] = None,
    within_days: int = 90,
    min_confidence: float = 0.0,
    trigger_types: Optional[Sequence[str]] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for trigger, days in repo.triggers_due(
        session,
        as_of=as_of,
        within_days=within_days,
        min_confidence=min_confidence,
        trigger_types=trigger_types,
        limit=limit,
    ):
        company = trigger.company
        rows.append(
            {
                "trigger_date": trigger.trigger_date.isoformat(),
                "days_to_trigger": days,
                "company": getattr(company, "name", "") or "",
                "bse_code": getattr(company, "bse_code", "") or "",
                "trigger_type": label_for(trigger.trigger_type),
                "trigger_code": trigger.trigger_type,
                "subject": trigger.subject_name or "—",
                "subject_type": trigger.subject_type,
                "basis": trigger.anchor_basis,
                "anchor_date": trigger.anchor_date.isoformat() if trigger.anchor_date else "",
                "stake_pct": (
                    f"{trigger.percent_of_equity:.2f}%" if trigger.percent_of_equity else ""
                ),
                "confidence": round(trigger.confidence or 0.0, 2),
                "status": trigger.status,
                "trigger_id": trigger.id,
                "company_id": trigger.company_id,
            }
        )
    return rows


def calendar_digest(
    rows: Sequence[Dict[str, Any]], as_of: Optional[dt.date] = None
) -> str:
    """Group the calendar into lead-time buckets for a readable digest."""
    day = (as_of or dt.date.today()).isoformat()
    if not rows:
        return f"Trigger calendar — {day}: nothing inside the horizon."

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "This week (T-7)": [],
        "This month (T-30)": [],
        "This quarter (T-90)": [],
        "Later": [],
    }
    for row in rows:
        days = int(row["days_to_trigger"])
        if days <= 7:
            buckets["This week (T-7)"].append(row)
        elif days <= 30:
            buckets["This month (T-30)"].append(row)
        elif days <= 90:
            buckets["This quarter (T-90)"].append(row)
        else:
            buckets["Later"].append(row)

    lines = [f"*Trigger calendar — {day}* · {len(rows)} deadline(s)", ""]
    for name, items in buckets.items():
        if not items:
            continue
        lines.append(f"*{name}* ({len(items)})")
        for row in items[:15]:
            subject = f" · {row['subject']}" if row["subject"] != "—" else ""
            lines.append(
                f"  T-{row['days_to_trigger']:<3} {row['trigger_date']} "
                f"{row['company']} — {row['trigger_type']}{subject}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()
