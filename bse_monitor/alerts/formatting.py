"""Shared alert rendering so every channel says the same thing."""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

CRORE = 1e7

PRIORITY_EMOJI = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🔵"}


def format_inr(amount: Optional[float]) -> str:
    if not amount:
        return "—"
    crore = amount / CRORE
    if crore >= 1:
        return f"Rs {crore:,.1f} Cr"
    return f"Rs {amount:,.0f}"


def format_row(filing: Any) -> Dict[str, Any]:
    """Flatten an ``EventFiling`` (plus relations) into a display row."""
    company = getattr(filing, "company", None)
    investors = [
        link.investor.name
        for link in getattr(filing, "investor_links", []) or []
        if getattr(link, "investor", None)
    ]
    transactions = getattr(filing, "transactions", []) or []
    amount = next(
        (float(t.amount_inr) for t in transactions if t.amount_inr is not None), None
    )
    return {
        "date": filing.filing_date.isoformat() if filing.filing_date else "",
        "company": getattr(company, "name", "") or "",
        "bse_code": getattr(company, "bse_code", "") or "",
        "event_type": filing.category,
        "investor": ", ".join(investors[:3]) or "—",
        "value": format_inr(amount),
        "score": filing.opportunity_score,
        "priority": filing.priority,
        "certainty": filing.certainty,
        "headline": (filing.headline or "")[:300],
        "pdf_url": filing.pdf_url or "",
    }


def alert_title(filing: Any) -> str:
    company = getattr(getattr(filing, "company", None), "name", "Unknown")
    return f"[{filing.priority}] {filing.category} · {company} · score {filing.opportunity_score}"


def alert_body(filing: Any) -> str:
    row = format_row(filing)
    reasons = _reason_lines(filing)
    lines = [
        f"Company      : {row['company']} ({row['bse_code'] or 'n/a'})",
        f"Event        : {row['event_type']} ({filing.certainty})",
        f"Filed        : {row['date']}",
        f"Investor(s)  : {row['investor']}",
        f"Value        : {row['value']}",
        f"Score        : {row['score']}/100  [{row['priority']}]",
        f"Confidence   : {filing.classification_confidence:.2f} ({filing.classification_method})",
        "",
        f"Headline     : {row['headline']}",
    ]
    if reasons:
        lines += ["", "Why it scored:", *(f"  · {line}" for line in reasons)]
    if row["pdf_url"]:
        lines += ["", f"Filing PDF   : {row['pdf_url']}"]
    return "\n".join(lines)


def _reason_lines(filing: Any) -> List[str]:
    breakdown = filing.score_breakdown or {}
    out: List[str] = []
    for component in breakdown.get("components", []):
        name = str(component.get("component", "")).replace("_", " ")
        detail = component.get("detail")
        value = component.get("value")
        suffix = f" ({detail})" if detail else ""
        out.append(f"{name}{suffix}: {value:+g}")
    return out


def markdown_table(rows: Sequence[Dict[str, Any]], columns: Sequence[str]) -> str:
    """Pipe table used by the Slack/Teams digests and the README examples."""
    if not rows:
        return "_No events._"
    header = "| " + " | ".join(columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(str(row.get(col, "")) for col in columns) + " |" for row in rows
    ]
    return "\n".join([header, divider, *body])


def digest_text(rows: Sequence[Dict[str, Any]], for_date: Optional[dt.date] = None) -> str:
    day = (for_date or dt.date.today()).isoformat()
    if not rows:
        return f"BSE Monitor — {day}: no qualifying events."
    lines = [f"*BSE Monitor — {day}* · {len(rows)} event(s)", ""]
    for row in rows:
        emoji = PRIORITY_EMOJI.get(row.get("priority", "LOW"), "")
        lines.append(
            f"{emoji} *{row['company']}* — {row['event_type']} · {row['value']} · "
            f"score {row['score']}"
        )
        if row.get("investor") and row["investor"] != "—":
            lines.append(f"    investor: {row['investor']}")
    return "\n".join(lines)
