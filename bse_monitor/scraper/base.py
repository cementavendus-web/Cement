"""Shared HTTP plumbing: rate limiting, retries with jittered backoff, sessions.

BSE and NSE both sit behind bot protection that rejects naked ``requests`` calls
(missing Referer, no warm-up cookie). The client here mirrors a browser closely
enough for the JSON endpoints, and the concrete scrapers escalate to Playwright
when a cookie wall is detected.
"""

from __future__ import annotations

import dataclasses
import logging
import random
import threading
import time
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)


class ScraperError(RuntimeError):
    """Raised when a request exhausts its retry budget."""


class RateLimiter:
    """Token-less leaky bucket: enforces a minimum gap between requests."""

    def __init__(self, per_second: float) -> None:
        self.min_interval = 1.0 / per_second if per_second > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._last + self.min_interval - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


@dataclasses.dataclass
class RetryPolicy:
    max_retries: int = 5
    base_seconds: float = 2.0
    max_seconds: float = 60.0
    # 403/401 usually mean the anti-bot cookie went stale, which a retry with a
    # refreshed session can fix; 404 never will.
    retry_statuses: tuple[int, ...] = (403, 408, 429, 500, 502, 503, 504)

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        raw = min(self.base_seconds * (2 ** max(0, attempt - 1)), self.max_seconds)
        return random.uniform(raw / 2, raw)


class HttpClient:
    """Retrying HTTP client with a persistent cookie jar."""

    def __init__(
        self,
        user_agent: str,
        timeout: float = 30.0,
        rate_limit_per_second: float = 2.0,
        retry: Optional[RetryPolicy] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.timeout = timeout
        self.retry = retry or RetryPolicy()
        self.limiter = RateLimiter(rate_limit_per_second)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                **(default_headers or {}),
            }
        )

    def warm_up(self, url: str) -> bool:
        """Fetch a page purely to collect anti-bot cookies. Never raises."""
        try:
            self.limiter.wait()
            response = self.session.get(
                url, timeout=self.timeout, headers={"Accept": "text/html,*/*"}
            )
            log.debug("Warm-up", extra={"url": url, "status": response.status_code})
            return response.ok
        except requests.RequestException as exc:
            log.warning("Warm-up failed", extra={"url": url, "error": str(exc)})
            return False

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
    ) -> requests.Response:
        last_error: Optional[str] = None
        for attempt in range(1, self.retry.max_retries + 1):
            self.limiter.wait()
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                    stream=stream,
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "Request error, retrying",
                    extra={"url": url, "attempt": attempt, "error": last_error},
                )
            else:
                if response.status_code in self.retry.retry_statuses:
                    last_error = f"HTTP {response.status_code}"
                    log.warning(
                        "Retryable status",
                        extra={"url": url, "attempt": attempt, "status": response.status_code},
                    )
                else:
                    response.raise_for_status()
                    return response

            if attempt < self.retry.max_retries:
                time.sleep(self.retry.delay_for(attempt))

        raise ScraperError(f"GET {url} failed after {self.retry.max_retries} attempts: {last_error}")

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        response = self.request("GET", url, params=params, headers=headers)
        try:
            return response.json()
        except ValueError as exc:
            snippet = response.text[:200].replace("\n", " ")
            raise ScraperError(f"Non-JSON response from {url}: {snippet}") from exc

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def client_from_config(cfg: Any, extra_headers: Optional[Dict[str, str]] = None) -> HttpClient:
    scraper_cfg = cfg.section("scraper")
    return HttpClient(
        user_agent=scraper_cfg.get("user_agent", "Mozilla/5.0"),
        timeout=float(scraper_cfg.get("request_timeout_seconds", 30)),
        rate_limit_per_second=float(scraper_cfg.get("rate_limit_per_second", 2.0)),
        retry=RetryPolicy(
            max_retries=int(scraper_cfg.get("max_retries", 5)),
            base_seconds=float(scraper_cfg.get("backoff_base_seconds", 2.0)),
            max_seconds=float(scraper_cfg.get("backoff_max_seconds", 60.0)),
        ),
        default_headers=extra_headers,
    )
