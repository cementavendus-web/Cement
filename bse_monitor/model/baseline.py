"""The transparent baseline the learned model has to beat.

Without this number, a PR-AUC is uninterpretable: a model that scores 0.31 on a
2% base rate could be excellent or could be worse than sorting by days-to-
lock-in. The baseline makes the comparison explicit, and it is deliberately the
heuristic a competent analyst would use unaided — nearest hard deadline first,
then marquee holder, then size of position.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

# Deadline proximity dominates: a lock-in expiring in 9 days is a far stronger
# supply signal than a large but unconstrained holding.
_HORIZON_SCORES = ((14, 60.0), (30, 45.0), (60, 30.0), (90, 18.0), (180, 8.0))


def baseline_score(row: Mapping[str, Any]) -> float:
    """A 0-100 score from three readable components."""
    score = 0.0

    days = row.get("days_to_nearest_trigger")
    if days is not None:
        for horizon, points in _HORIZON_SCORES:
            if days <= horizon:
                score += points
                break

    if row.get("is_marquee"):
        score += 12.0

    stake_cr = row.get("stake_value_cr") or 0.0
    if stake_cr >= 1000:
        score += 18.0
    elif stake_cr >= 250:
        score += 12.0
    elif stake_cr >= 50:
        score += 6.0

    # A holder who has sold here before is likelier to sell again.
    if (row.get("holder_prior_sells_here") or 0) > 0:
        score += 6.0

    # Illiquid positions have to leave as blocks.
    adv_days = row.get("days_of_adv_to_liquidate")
    if adv_days is not None and adv_days >= 5:
        score += 4.0

    return min(100.0, score)


def baseline_scores(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    return [baseline_score(row) for row in rows]


def baseline_reasons(row: Mapping[str, Any]) -> list[str]:
    """Readable justification, so the baseline is auditable like the model."""
    out: list[str] = []
    days = row.get("days_to_nearest_trigger")
    if days is not None:
        out.append(f"nearest trigger in {days}d")
    if row.get("is_marquee"):
        out.append("marquee holder")
    stake = row.get("stake_value_cr")
    if stake:
        out.append(f"stake ~Rs {stake:,.0f} Cr")
    return out[:3]
