"""Structured logging with a rotating file sink and a run-scoped correlation id."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import uuid
from pathlib import Path
from typing import Optional

_RUN_ID = os.getenv("BSE_RUN_ID") or uuid.uuid4().hex[:12]

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line so logs are greppable and ingestible."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": _RUN_ID,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def run_id() -> str:
    return _RUN_ID


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)-28s %(message)s")
    )
    root.addHandler(console)

    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            path, maxBytes=20 * 1024 * 1024, backupCount=7, encoding="utf-8"
        )
        rotating.setFormatter(JsonFormatter())
        root.addHandler(rotating)

    # These libraries are chatty at INFO and drown the pipeline's own output.
    for noisy in ("urllib3", "pdfminer", "PIL", "asyncio", "fitz"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
