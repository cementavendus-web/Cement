"""Batch API path, for backfill at half price.

Two things differ from the synchronous path and both are easy to get wrong:

* **There is no ``parse`` equivalent for batches.** Requests must carry the raw
  ``output_config`` JSON schema, which is why ``EVENT_JSON_SCHEMA`` exists as a
  standalone dict rather than being derived from the Pydantic model at call time.
* **Results arrive in arbitrary order.** They are looked up by ``custom_id``,
  never by position — the test for this feeds a deliberately shuffled list.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..database import repository as repo
from ..database.models import LlmExtraction
from .client import LlmCall, LlmClient, LlmUsage, estimate_cost
from .schema import EVENT_JSON_SCHEMA, coerce_event

log = logging.getLogger(__name__)


@dataclasses.dataclass
class BatchItem:
    filing_id: int
    announcement_id: str
    custom_id: str
    title: str
    page_text: str
    page_slice: str = ""


@dataclasses.dataclass
class BatchStats:
    submitted: int = 0
    succeeded: int = 0
    failed: int = 0
    retryable: int = 0
    cost_usd: float = 0.0
    batch_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def custom_id_for(filing_id: int) -> str:
    """Stable and order-independent."""
    return f"f{filing_id}"


def build_batch_requests(
    client: LlmClient, items: Sequence[BatchItem]
) -> List[Dict[str, Any]]:
    """Requests sharing a byte-identical system prefix, so caching applies
    inside the batch too — only the user message varies per request."""
    requests: List[Dict[str, Any]] = []
    for item in items:
        params = client.build_params(
            title=item.title, page_text=item.page_text, page_slice=item.page_slice
        )
        params["output_config"] = {
            "format": {"type": "json_schema", "schema": EVENT_JSON_SCHEMA}
        }
        requests.append({"custom_id": item.custom_id, "params": params})
    return requests


def submit_batch(
    client: LlmClient, session: Session, items: Sequence[BatchItem]
) -> Optional[str]:
    if not items or not client.available:
        return None

    requests = build_batch_requests(client, items)
    batch = client._client.messages.batches.create(requests=requests)
    batch_id = getattr(batch, "id", None)

    for item in items:
        row, _created = repo.claim_llm_extraction(
            session,
            filing_id=item.filing_id,
            announcement_id=item.announcement_id,
            source="BSE",
            model=client.model,
            prompt_version=client.prompt_version,
            mode="BATCH",
            custom_id=item.custom_id,
        )
        row.batch_id = batch_id
        row.status = "SUBMITTED"
        row.page_slice = item.page_slice
    session.flush()

    log.info("Batch submitted", extra={"batch_id": batch_id, "requests": len(requests)})
    return batch_id


def poll_batch(client: LlmClient, batch_id: str) -> str:
    return getattr(
        client._client.messages.batches.retrieve(batch_id), "processing_status", "unknown"
    )


def collect_batch(client: LlmClient, session: Session, batch_id: str) -> BatchStats:
    """Apply results, keyed by custom_id."""
    stats = BatchStats(batch_id=batch_id)
    rows = {row.custom_id: row for row in repo.llm_rows_for_batch(session, batch_id)}

    for result in client._client.messages.batches.results(batch_id):
        custom_id = getattr(result, "custom_id", None)
        row: Optional[LlmExtraction] = rows.get(custom_id)
        if row is None:
            log.warning("Batch result for unknown custom_id", extra={"custom_id": custom_id})
            continue

        outcome = getattr(result, "result", None)
        kind = getattr(outcome, "type", "errored")

        if kind == "succeeded":
            message = getattr(outcome, "message", None)
            verdict, usage = _read_message(message)
            call = LlmCall(
                ok=verdict is not None,
                verdict=verdict,
                usage=usage,
                cost_usd=estimate_cost(usage, client.pricing, batch=True),
                model=client.model,
            )
            stats.cost_usd += call.cost_usd
            if verdict is None:
                stats.failed += 1
                repo.record_llm_result(
                    session, row, call=call, status="FAILED", error="unparseable batch result"
                )
            else:
                stats.succeeded += 1
                repo.record_llm_result(session, row, call=call, verdict=verdict, status="OK")
            continue

        error_type = getattr(getattr(outcome, "error", None), "type", "")
        if kind == "errored" and error_type == "invalid_request":
            # Terminal: resubmitting an invalid request fails identically.
            stats.failed += 1
            repo.record_llm_result(
                session, row, status="FAILED", error=f"invalid_request: {error_type}"
            )
        else:
            # canceled, expired, or a transient error — let the next run retry.
            stats.retryable += 1
            repo.reset_llm_row(session, row, f"batch {kind}: {error_type or 'retryable'}")

    session.flush()
    log.info("Batch collected", extra=stats.as_dict())
    return stats


def _read_message(message: Any) -> tuple[Any, LlmUsage]:
    usage = LlmUsage.from_response(message)
    text = ""
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text = block.text
            break
    if not text:
        return None, usage
    try:
        return coerce_event(json.loads(text)), usage
    except json.JSONDecodeError:
        return None, usage


def run_backfill(
    client: LlmClient,
    session: Session,
    items: Sequence[BatchItem],
    *,
    poll_seconds: int = 60,
    max_wait_seconds: int = 86_400,
    wait: bool = True,
) -> BatchStats:
    batch_id = submit_batch(client, session, items)
    stats = BatchStats(submitted=len(items), batch_id=batch_id)
    if batch_id is None or not wait:
        return stats

    waited = 0
    while waited < max_wait_seconds:
        if poll_batch(client, batch_id) == "ended":
            collected = collect_batch(client, session, batch_id)
            collected.submitted = len(items)
            return collected
        time.sleep(poll_seconds)
        waited += poll_seconds

    log.warning("Batch did not finish inside the wait window", extra={"batch_id": batch_id})
    return stats
