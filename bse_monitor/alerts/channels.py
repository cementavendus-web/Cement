"""Alert delivery channels.

Each channel exposes ``send(title, body, priority) -> (ok, error)`` and never
raises: a broken webhook must degrade the run to PARTIAL, not lose the filings
that were already persisted. Delivery state lives in ``daily_alerts``, so a
failed send stays PENDING and is retried on the next run.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import requests

from .formatting import markdown_table

log = logging.getLogger(__name__)

PRIORITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


class BaseChannel:
    name = "base"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", False))
        self.min_priority = str(self.config.get("min_priority", "LOW")).upper()

    def accepts(self, priority: str) -> bool:
        return PRIORITY_ORDER.get(priority.upper(), 0) >= PRIORITY_ORDER.get(self.min_priority, 0)

    def send(self, title: str, body: str, priority: str = "LOW") -> Tuple[bool, Optional[str]]:
        raise NotImplementedError


class EmailChannel(BaseChannel):
    name = "email"

    def send(self, title: str, body: str, priority: str = "LOW") -> Tuple[bool, Optional[str]]:
        recipients = self.config.get("recipients") or []
        if not recipients:
            return False, "no recipients configured"
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self.config.get("sender", "bse-monitor@localhost")
        message["To"] = ", ".join(recipients)
        message.set_content(body)
        try:
            host = self.config.get("smtp_host", "localhost")
            port = int(self.config.get("smtp_port", 25))
            with smtplib.SMTP(host, port, timeout=30) as server:
                if self.config.get("use_tls", True):
                    server.starttls()
                username = self.config.get("username")
                password = self.config.get("password")
                if username and password:
                    server.login(username, password)
                server.send_message(message)
            return True, None
        except Exception as exc:
            log.error("Email send failed", extra={"error": str(exc)})
            return False, str(exc)


class SlackChannel(BaseChannel):
    name = "slack"

    def send(self, title: str, body: str, priority: str = "LOW") -> Tuple[bool, Optional[str]]:
        webhook = self.config.get("webhook_url")
        if not webhook:
            return False, "no webhook_url configured"
        payload = {
            "text": title,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"```{body[:2800]}```"}},
            ],
        }
        try:
            response = requests.post(webhook, json=payload, timeout=20)
            if response.status_code >= 300:
                return False, f"HTTP {response.status_code}: {response.text[:200]}"
            return True, None
        except requests.RequestException as exc:
            log.error("Slack send failed", extra={"error": str(exc)})
            return False, str(exc)


class TeamsChannel(BaseChannel):
    name = "teams"

    def send(self, title: str, body: str, priority: str = "LOW") -> Tuple[bool, Optional[str]]:
        webhook = self.config.get("webhook_url")
        if not webhook:
            return False, "no webhook_url configured"
        colour = {"HIGH": "D93025", "MEDIUM": "F29900", "LOW": "1A73E8"}.get(priority, "1A73E8")
        payload = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": colour,
            "summary": title[:120],
            "title": title[:150],
            "text": body.replace("\n", "\n\n")[:9000],
        }
        try:
            response = requests.post(
                webhook,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            if response.status_code >= 300:
                return False, f"HTTP {response.status_code}: {response.text[:200]}"
            return True, None
        except requests.RequestException as exc:
            log.error("Teams send failed", extra={"error": str(exc)})
            return False, str(exc)


class CsvChannel(BaseChannel):
    """Appends to a per-day CSV. Always available, and the audit copy of record."""

    name = "csv"

    COLUMNS = (
        "date",
        "company",
        "bse_code",
        "event_type",
        "investor",
        "value",
        "score",
        "priority",
        "certainty",
        "headline",
        "pdf_url",
    )

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.output_dir = Path(self.config.get("output_dir", "./data/exports"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, day: Optional[dt.date] = None) -> Path:
        target = day or dt.date.today()
        return self.output_dir / f"bse_events_{target.isoformat()}.csv"

    def write_rows(
        self, rows: Sequence[Dict[str, Any]], day: Optional[dt.date] = None
    ) -> Path:
        path = self.path_for(day)
        exists = path.exists()
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.COLUMNS), extrasaction="ignore")
            if not exists:
                writer.writeheader()
            for row in rows:
                writer.writerow(row)
        log.info("CSV export written", extra={"path": str(path), "rows": len(rows)})
        return path

    def send(self, title: str, body: str, priority: str = "LOW") -> Tuple[bool, Optional[str]]:
        # Row-level export is handled by write_rows; send() records the digest.
        try:
            path = self.output_dir / f"digest_{dt.date.today().isoformat()}.md"
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"## {title}\n\n```\n{body}\n```\n\n")
            return True, None
        except OSError as exc:
            return False, str(exc)


def build_channels(alerts_config: Dict[str, Any]) -> Dict[str, BaseChannel]:
    """Instantiate only the channels that are enabled in config."""
    specs = (alerts_config or {}).get("channels", {}) or {}
    registry = {
        "email": EmailChannel,
        "slack": SlackChannel,
        "teams": TeamsChannel,
        "csv": CsvChannel,
    }
    channels: Dict[str, BaseChannel] = {}
    for name, cls in registry.items():
        spec = specs.get(name) or {}
        channel = cls(spec)
        if channel.enabled:
            channels[name] = channel
    return channels


__all__ = [
    "BaseChannel",
    "EmailChannel",
    "SlackChannel",
    "TeamsChannel",
    "CsvChannel",
    "build_channels",
    "markdown_table",
]
