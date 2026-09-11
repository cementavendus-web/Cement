"""PDF download with content-addressed storage.

Attachments are stored under ``<download_dir>/<yyyy>/<mm>/<sha256>.pdf``. Naming
by digest rather than by the exchange's filename means the same document linked
from two filings (common when a company files with both exchanges) is fetched
and parsed exactly once.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path
from typing import Iterable, Optional, Sequence

from ..scraper.base import HttpClient, ScraperError

log = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"


class DownloadResult:
    __slots__ = ("path", "sha256", "byte_size", "url", "status", "error", "from_cache")

    def __init__(
        self,
        status: str,
        url: str,
        path: Optional[Path] = None,
        sha256: Optional[str] = None,
        byte_size: int = 0,
        error: Optional[str] = None,
        from_cache: bool = False,
    ) -> None:
        self.status = status          # OK | FAILED | SKIPPED
        self.url = url
        self.path = path
        self.sha256 = sha256
        self.byte_size = byte_size
        self.error = error
        self.from_cache = from_cache

    @property
    def ok(self) -> bool:
        return self.status == "OK"

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<DownloadResult {self.status} {self.url} size={self.byte_size}>"


class PdfDownloader:
    def __init__(
        self,
        client: HttpClient,
        download_dir: str | Path,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.client = client
        self.root = Path(download_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def _target_path(self, digest: str, when: Optional[dt.date] = None) -> Path:
        day = when or dt.date.today()
        folder = self.root / f"{day.year:04d}" / f"{day.month:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{digest}.pdf"

    def download(
        self, urls: Sequence[str] | str, when: Optional[dt.date] = None
    ) -> DownloadResult:
        """Try each candidate URL in order (live location, then historical)."""
        candidates: Iterable[str] = [urls] if isinstance(urls, str) else list(urls)
        last_error = "no url supplied"
        for url in candidates:
            if not url:
                continue
            try:
                response = self.client.request("GET", url, stream=True)
            except ScraperError as exc:
                last_error = str(exc)
                log.warning("PDF download failed", extra={"url": url, "error": last_error})
                continue

            chunks: list[bytes] = []
            size = 0
            oversized = False
            for chunk in response.iter_content(chunk_size=65_536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > self.max_bytes:
                    oversized = True
                    break
                chunks.append(chunk)
            response.close()

            if oversized:
                log.warning("PDF exceeds size cap", extra={"url": url, "cap": self.max_bytes})
                return DownloadResult("SKIPPED", url, error=f"larger than {self.max_bytes} bytes")

            body = b"".join(chunks)
            if not body:
                last_error = "empty response body"
                continue
            if not body.lstrip()[:4].startswith(_PDF_MAGIC):
                # BSE serves an HTML error page with a 200 when a filename has
                # aged out of AttachLive; fall through to the next candidate.
                last_error = "response is not a PDF"
                log.info("Non-PDF payload, trying next candidate", extra={"url": url})
                continue

            digest = hashlib.sha256(body).hexdigest()
            path = self._target_path(digest, when)
            if path.exists() and path.stat().st_size == len(body):
                return DownloadResult("OK", url, path, digest, len(body), from_cache=True)
            path.write_bytes(body)
            log.info("PDF downloaded", extra={"url": url, "bytes": len(body), "sha": digest[:12]})
            return DownloadResult("OK", url, path, digest, len(body))

        return DownloadResult("FAILED", str(candidates and list(candidates)[0] or ""), error=last_error)
