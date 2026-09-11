"""Queue-then-send alert dispatch.

Alerts are queued in ``daily_alerts`` during the pipeline run and delivered in a
separate pass. The split matters for resilience: a webhook outage leaves rows
PENDING and the next run retries them, rather than silently dropping a ₹5,000 Cr
promoter sale because Slack returned a 503.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..database import repository as repo
from .channels import BaseChannel, CsvChannel, build_channels
from .formatting import alert_body, alert_title, digest_text, format_row

log = logging.getLogger(__name__)


class AlertDispatcher:
    def __init__(self, alerts_config: Dict[str, Any]) -> None:
        self.config = alerts_config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.channels: Dict[str, BaseChannel] = build_channels(self.config)
        self.high_min = int(self.config.get("high_priority_min_score", 85))
        self.medium_min = int(self.config.get("medium_priority_min_score", 65))

    def should_alert(self, filing: Any) -> bool:
        """Low-priority noise is stored but never pushed to a human channel."""
        if filing.is_duplicate or filing.category == "Other":
            return False
        return filing.opportunity_score >= self.medium_min or filing.priority in {"HIGH", "MEDIUM"}

    def queue(self, session: Session, filings: Sequence[Any]) -> int:
        """Create PENDING alert rows for every qualifying filing × channel."""
        if not self.enabled or not self.channels:
            return 0
        queued = 0
        today = dt.date.today()
        for filing in filings:
            if not self.should_alert(filing):
                continue
            title = alert_title(filing)
            body = alert_body(filing)
            for name, channel in self.channels.items():
                if not channel.accepts(filing.priority):
                    continue
                _, created = repo.queue_alert(
                    session,
                    filing_id=filing.id,
                    alert_date=filing.filing_date or today,
                    priority=filing.priority,
                    channel=name,
                    title=title,
                    body=body,
                )
                queued += int(created)
        log.info("Alerts queued", extra={"count": queued})
        return queued

    def flush(self, session: Session, limit: int = 200) -> Dict[str, int]:
        """Deliver PENDING alerts; returns per-status counts."""
        stats = {"sent": 0, "failed": 0, "skipped": 0}
        if not self.enabled:
            return stats
        for alert in repo.pending_alerts(session)[:limit]:
            channel = self.channels.get(alert.channel)
            if channel is None:
                repo.mark_alert(session, alert, "SKIPPED", "channel disabled")
                stats["skipped"] += 1
                continue
            ok, error = channel.send(alert.title or "", alert.body or "", alert.priority)
            if ok:
                repo.mark_alert(session, alert, "SENT")
                stats["sent"] += 1
            else:
                # Left PENDING deliberately — the next run retries it.
                alert.error = error
                stats["failed"] += 1
                log.warning(
                    "Alert delivery failed, will retry",
                    extra={"alert_id": alert.id, "channel": alert.channel, "error": error},
                )
        session.flush()
        log.info("Alerts flushed", extra=stats)
        return stats

    def export_csv(
        self, filings: Sequence[Any], day: Optional[dt.date] = None
    ) -> Optional[str]:
        """Write the day's events to CSV regardless of per-filing alert status."""
        channel = self.channels.get("csv")
        if not isinstance(channel, CsvChannel):
            return None
        rows: List[Dict[str, Any]] = [
            format_row(filing) for filing in filings if not filing.is_duplicate
        ]
        if not rows:
            return None
        return str(channel.write_rows(rows, day))

    def send_digest(self, filings: Sequence[Any], day: Optional[dt.date] = None) -> None:
        """One summary message per run, on top of the per-filing alerts."""
        rows = [format_row(f) for f in filings if self.should_alert(f)]
        if not rows:
            return
        text = digest_text(rows, day)
        title = f"BSE Monitor digest — {(day or dt.date.today()).isoformat()} ({len(rows)} events)"
        for name, channel in self.channels.items():
            if name == "csv":
                continue
            ok, error = channel.send(title, text, "MEDIUM")
            if not ok:
                log.warning("Digest failed", extra={"channel": name, "error": error})


def dispatcher_from_config(config: Any) -> AlertDispatcher:
    return AlertDispatcher(config.section("alerts"))
