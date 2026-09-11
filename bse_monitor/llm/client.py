"""Anthropic client wrapper for filing event extraction.

``anthropic`` is an optional import, mirroring ``classifier/ml.py``'s
``SKLEARN_AVAILABLE`` pattern: without it, ``available`` is False and the
pipeline runs rules-only rather than failing.

``extract_event`` never raises. It returns ``LlmCall(ok=False, error=...)``,
matching the ``BaseChannel.send -> (ok, error)`` contract that already governs
every fallible boundary in this codebase — which is what makes "degrade to
rules-only" automatic rather than a special case at each call site.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import random
import time
from typing import Any, Dict, Mapping, Optional

from .prompts import PROMPT_VERSION, build_user_content, system_blocks
from .schema import EVENT_JSON_SCHEMA, LlmVerdict, coerce_event

log = logging.getLogger(__name__)

try:  # pragma: no cover - exercised only where the SDK is installed
    import anthropic

    ANTHROPIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]
    ANTHROPIC_AVAILABLE = False

DEFAULT_MODEL = "claude-haiku-4-5"

# Haiku 4.5 list prices, overridable from config so a price change is not a
# code change.
DEFAULT_PRICING: Dict[str, float] = {
    "input_per_mtok": 1.0,
    "output_per_mtok": 5.0,
    "cache_write_multiplier": 1.25,
    "cache_read_multiplier": 0.1,
    "batch_multiplier": 0.5,
}


@dataclasses.dataclass
class LlmUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_response(cls, response: Any) -> "LlmUsage":
        usage = getattr(response, "usage", None)
        if usage is None:
            return cls()
        return cls(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        )


@dataclasses.dataclass
class LlmCall:
    ok: bool
    verdict: Optional[LlmVerdict] = None
    raw_text: str = ""
    response_json: Dict[str, Any] = dataclasses.field(default_factory=dict)
    usage: LlmUsage = dataclasses.field(default_factory=LlmUsage)
    cost_usd: float = 0.0
    model: str = ""
    latency_ms: int = 0
    attempts: int = 0
    error: Optional[str] = None


def estimate_cost(
    usage: LlmUsage, pricing: Optional[Mapping[str, float]] = None, batch: bool = False
) -> float:
    rates = {**DEFAULT_PRICING, **(pricing or {})}
    million = 1_000_000.0
    cost = (
        usage.input_tokens * rates["input_per_mtok"]
        + usage.output_tokens * rates["output_per_mtok"]
        + usage.cache_creation_input_tokens
        * rates["input_per_mtok"]
        * rates["cache_write_multiplier"]
        + usage.cache_read_input_tokens
        * rates["input_per_mtok"]
        * rates["cache_read_multiplier"]
    ) / million
    if batch:
        cost *= rates["batch_multiplier"]
    return round(cost, 8)


class LlmClient:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        backoff_max: float = 30.0,
        pricing: Optional[Mapping[str, float]] = None,
        cache_enabled: bool = True,
        prompt_version: str = PROMPT_VERSION,
        client: Any = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.pricing = {**DEFAULT_PRICING, **(pricing or {})}
        self.cache_enabled = cache_enabled
        self.prompt_version = prompt_version
        # Injecting a client is the seam that keeps the tests offline: with a
        # fake here no HTTP is possible, whether or not the SDK is installed.
        self._client = client
        self._warned_no_cache = False

        if self._client is None and ANTHROPIC_AVAILABLE:
            kwargs: Dict[str, Any] = {"timeout": timeout, "max_retries": 0}
            if api_key:
                kwargs["api_key"] = api_key
            try:
                self._client = anthropic.Anthropic(**kwargs)
            except Exception as exc:  # pragma: no cover - construction is rare
                log.warning("Anthropic client construction failed", extra={"error": str(exc)})
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    # -- request construction ---------------------------------------------
    def build_params(self, *, title: str, page_text: str, page_slice: str = "") -> Dict[str, Any]:
        """The exact request body. Shared with the batch path and the CLI's
        ``--dry-run``, so prompt-cache stability can be eyeballed without
        spending anything."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system_blocks(cache=self.cache_enabled),
            "messages": [
                {"role": "user", "content": build_user_content(title, page_text, page_slice)}
            ],
        }

    # -- retry policy ------------------------------------------------------
    def _should_retry(self, exc: Exception) -> bool:
        """Retry only what can succeed on a second attempt.

        A 400 is a bug in our request and will fail identically forever; a 404
        means the model id is wrong. Retrying either just burns time.
        """
        if not ANTHROPIC_AVAILABLE:
            return False
        if isinstance(exc, anthropic.NotFoundError):
            return False
        if isinstance(exc, anthropic.RateLimitError):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            return int(getattr(exc, "status_code", 0)) >= 500
        if isinstance(exc, anthropic.APIConnectionError):
            return True
        return False

    def _delay(self, attempt: int, exc: Optional[Exception] = None) -> float:
        retry_after = getattr(getattr(exc, "response", None), "headers", {}) or {}
        try:
            if "retry-after" in retry_after:
                return min(float(retry_after["retry-after"]), self.backoff_max)
        except (TypeError, ValueError):
            pass
        raw = min(self.backoff_base * (2 ** max(0, attempt - 1)), self.backoff_max)
        return random.uniform(raw / 2, raw)

    # -- inference ---------------------------------------------------------
    def _parse_response(self, response: Any) -> tuple[Optional[LlmVerdict], str]:
        """Read a verdict out of either response shape."""
        parsed = getattr(response, "parsed_output", None)
        if parsed is not None:
            payload = parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)
            return coerce_event(payload), json.dumps(payload, default=str)

        text = ""
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        if not text:
            return None, ""
        try:
            return coerce_event(json.loads(text)), text
        except json.JSONDecodeError:
            return None, text

    def extract_event(
        self, *, title: str, page_text: str, page_slice: str = ""
    ) -> LlmCall:
        if not self.available:
            return LlmCall(ok=False, error="anthropic client unavailable", model=self.model)

        params = self.build_params(title=title, page_text=page_text, page_slice=page_slice)
        started = time.monotonic()
        last_error = "unknown"

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._call_once(params)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if not self._should_retry(exc) or attempt == self.max_retries:
                    log.warning(
                        "LLM call failed", extra={"error": last_error, "attempt": attempt}
                    )
                    return LlmCall(
                        ok=False,
                        error=last_error,
                        model=self.model,
                        attempts=attempt,
                        latency_ms=int((time.monotonic() - started) * 1000),
                    )
                time.sleep(self._delay(attempt, exc))
                continue

            usage = LlmUsage.from_response(response)
            self._check_cache(usage)
            verdict, raw_text = self._parse_response(response)
            latency = int((time.monotonic() - started) * 1000)

            if verdict is None:
                return LlmCall(
                    ok=False,
                    error="response was not parseable JSON",
                    raw_text=raw_text,
                    usage=usage,
                    cost_usd=estimate_cost(usage, self.pricing),
                    model=self.model,
                    attempts=attempt,
                    latency_ms=latency,
                )

            return LlmCall(
                ok=True,
                verdict=verdict,
                raw_text=raw_text,
                response_json={"usage": dataclasses.asdict(usage), "text": raw_text},
                usage=usage,
                cost_usd=estimate_cost(usage, self.pricing),
                model=self.model,
                attempts=attempt,
                latency_ms=latency,
            )

        return LlmCall(ok=False, error=last_error, model=self.model, attempts=self.max_retries)

    def _call_once(self, params: Dict[str, Any]) -> Any:
        """Prefer ``messages.parse``; fall back to ``create`` + json_schema.

        The two surfaces differ: ``parse`` takes a Pydantic ``output_format`` and
        returns ``.parsed_output``, while ``create`` takes a raw schema in
        ``output_config``. Older SDKs have only the latter.
        """
        messages = self._client.messages
        parse = getattr(messages, "parse", None)
        if parse is not None:
            from .pydantic_model import llm_event_model

            model_cls = llm_event_model()
            if model_cls is not None:
                return parse(**params, output_format=model_cls)

        return messages.create(
            **params,
            output_config={"format": {"type": "json_schema", "schema": EVENT_JSON_SCHEMA}},
        )

    def _check_cache(self, usage: LlmUsage) -> None:
        """Surface a dead cache once rather than silently paying for it."""
        if not self.cache_enabled or self._warned_no_cache:
            return
        if usage.cache_read_input_tokens == 0 and usage.cache_creation_input_tokens == 0:
            self._warned_no_cache = True
            log.warning(
                "Prompt cache is not engaging; the system prefix is likely below "
                "this model's minimum cacheable length (4096 tokens on Haiku 4.5). "
                "Calls still succeed, at full input price.",
                extra={"model": self.model, "input_tokens": usage.input_tokens},
            )


def client_from_config(config: Any, client: Any = None) -> Optional[LlmClient]:
    """Build a client from config, or None when the layer is switched off."""
    if not config.get("llm.enabled", False):
        return None
    candidate = LlmClient(
        api_key=config.get("llm.api_key") or None,
        model=config.get("llm.model", DEFAULT_MODEL),
        max_tokens=int(config.get("llm.max_tokens", 1024)),
        timeout=float(config.get("llm.timeout_seconds", 60)),
        max_retries=int(config.get("llm.max_retries", 3)),
        backoff_base=float(config.get("llm.backoff_base_seconds", 2.0)),
        backoff_max=float(config.get("llm.backoff_max_seconds", 30.0)),
        pricing=config.section("llm").get("pricing"),
        cache_enabled=bool(config.get("llm.cache_enabled", True)),
        prompt_version=str(config.get("llm.prompt_version", PROMPT_VERSION)),
        client=client,
    )
    return candidate
