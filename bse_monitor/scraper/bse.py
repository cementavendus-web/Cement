"""BSE Corporate Announcements scraper.

BSE serves its announcements list from a JSON endpoint that the public site
calls via XHR:

    https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w

It rejects requests without a ``Referer`` of ``https://www.bseindia.com/`` and,
intermittently, without a session cookie minted by loading the HTML page first.
The scraper therefore (1) warms up the session, (2) pages through the JSON API
per category, and (3) falls back to Playwright — which executes the page's own
XHR inside a real browser context — when the API keeps refusing.

Attachments live under ``/xml-data/corpfiling/AttachLive/<file>``; historical
ones move to ``AttachHis``. Both URL shapes are emitted so the downloader can
try live-then-historical.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ..config import Config
from .base import HttpClient, ScraperError, client_from_config
from .models import RawFiling

log = logging.getLogger(__name__)

API_BASE = "https://api.bseindia.com/BseIndiaAPI/api"
ANNOUNCEMENTS_ENDPOINT = f"{API_BASE}/AnnSubCategoryGetData/w"
CORP_ACTION_ENDPOINT = f"{API_BASE}/DefaultData/w"
SITE_ROOT = "https://www.bseindia.com"
ANNOUNCEMENTS_PAGE = f"{SITE_ROOT}/corporates/ann.html"
ATTACH_LIVE = f"{SITE_ROOT}/xml-data/corpfiling/AttachLive/"
ATTACH_HIST = f"{SITE_ROOT}/xml-data/corpfiling/AttachHis/"

BSE_HEADERS = {
    "Referer": f"{SITE_ROOT}/",
    "Origin": SITE_ROOT,
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
}

# BSE returns dates in several shapes depending on the field and the year.
_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%d %b %Y %H:%M:%S",
    "%Y-%m-%d",
    "%d-%m-%Y",
)


def parse_bse_datetime(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    # Last resort: pull an ISO-ish prefix out of a longer string.
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        try:
            return dt.datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            return None
    log.debug("Unparsed BSE date", extra={"value": text})
    return None


def attachment_urls(filename: Optional[str]) -> List[str]:
    """Candidate URLs for an attachment, live location first."""
    if not filename:
        return []
    name = str(filename).strip()
    if not name:
        return []
    if name.lower().startswith("http"):
        return [name]
    return [ATTACH_LIVE + name, ATTACH_HIST + name]


class BseScraper:
    """Paginated reader over BSE corporate announcements."""

    source = "BSE"

    def __init__(self, config: Config, client: Optional[HttpClient] = None) -> None:
        self.config = config
        self.cfg = config.section("scraper")
        self.client = client or client_from_config(config, BSE_HEADERS)
        self._warmed = False

    # -- internals ---------------------------------------------------------
    def _ensure_warm(self) -> None:
        if not self._warmed:
            self.client.warm_up(ANNOUNCEMENTS_PAGE)
            self._warmed = True

    @staticmethod
    def _rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
        """BSE has shipped at least three envelope shapes for this endpoint."""
        if payload is None:
            return []
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("Table", "table", "Data", "data", "Result"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _total_pages(payload: Any) -> Optional[int]:
        if isinstance(payload, dict):
            for key in ("Table1", "table1"):
                meta = payload.get(key)
                if isinstance(meta, list) and meta and isinstance(meta[0], dict):
                    for count_key in ("ROWCNT", "Rowcnt", "TotalRows"):
                        if count_key in meta[0]:
                            try:
                                total = int(meta[0][count_key])
                            except (TypeError, ValueError):
                                return None
                            # The API serves 50 rows per page.
                            return max(1, -(-total // 50))
        return None

    def _fetch_page(
        self, category: str, from_date: dt.date, to_date: dt.date, page: int
    ) -> Any:
        params = {
            "pageno": page,
            "strCat": category or "-1",
            "strPrevDate": from_date.strftime("%Y%m%d"),
            "strScrip": "",
            "strSearch": "P",
            "strToDate": to_date.strftime("%Y%m%d"),
            "strType": "C",
            "subcategory": "-1",
        }
        return self.client.get_json(ANNOUNCEMENTS_ENDPOINT, params=params, headers=BSE_HEADERS)

    # -- public API --------------------------------------------------------
    def fetch_announcements(
        self,
        from_date: dt.date,
        to_date: dt.date,
        categories: Optional[Iterable[str]] = None,
    ) -> Iterator[RawFiling]:
        """Yield every announcement in ``[from_date, to_date]`` for each category."""
        self._ensure_warm()
        cats = list(categories or self.cfg.get("bse_categories") or ["-1"])
        max_pages = int(self.cfg.get("max_pages_per_category", 25))
        seen_ids: set[str] = set()

        for category in cats:
            page = 1
            page_cap = max_pages
            while page <= page_cap:
                try:
                    payload = self._fetch_page(category, from_date, to_date, page)
                except ScraperError as exc:
                    log.error(
                        "BSE page fetch failed",
                        extra={"category": category, "page": page, "error": str(exc)},
                    )
                    if page == 1 and self.cfg.get("use_playwright_fallback", True):
                        payload = self._playwright_fetch(category, from_date, to_date, page)
                        if payload is None:
                            break
                    else:
                        break

                rows = self._rows_from_payload(payload)
                if page == 1:
                    total = self._total_pages(payload)
                    if total:
                        page_cap = min(max_pages, total)
                if not rows:
                    break

                for row in rows:
                    filing = self.parse_row(row)
                    if filing is None:
                        continue
                    key = filing.source_filing_id or filing.headline
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    yield filing

                log.info(
                    "BSE page scraped",
                    extra={"category": category, "page": page, "rows": len(rows)},
                )
                page += 1

    def parse_row(self, row: Dict[str, Any]) -> Optional[RawFiling]:
        """Map one API row onto :class:`RawFiling`.

        Field names vary in case between BSE deployments, so every lookup goes
        through a case-insensitive helper rather than a fixed key.
        """
        def pick(*names: str) -> Optional[Any]:
            lowered = {str(k).lower(): v for k, v in row.items()}
            for name in names:
                value = lowered.get(name.lower())
                if value not in (None, "", "NULL"):
                    return value
            return None

        company = pick("SLONGNAME", "SNAME", "COMPANYNAME", "Company_Name")
        code = pick("SCRIP_CD", "SCRIPCODE", "Scrip_Cd")
        headline = pick("NEWSSUB", "HEADLINE", "NEWS_SUBJECT", "SUBJECT") or ""
        if not company and not headline:
            return None

        body = pick("MORE", "NEWSBODY", "ANNOUNCEMENT_TEXT", "DESCRIPTOR") or ""
        body = re.sub(r"<[^>]+>", " ", str(body))
        body = re.sub(r"\s+", " ", body).strip()

        attachment = pick("ATTACHMENTNAME", "ATTACHMENT", "PDFFLAG_NAME")
        pdf_candidates = attachment_urls(attachment)
        news_id = pick("NEWSID", "NEWS_ID", "SCRIP_CD_NEWSID")

        return RawFiling(
            source=self.source,
            source_filing_id=str(news_id) if news_id else None,
            company_name=str(company or "").strip(),
            bse_code=str(code).strip() if code else None,
            headline=str(headline).strip(),
            body_text=body,
            category_hint=pick("CATEGORYNAME", "CATEGORY", "NEWS_CATEGORY"),
            subcategory_hint=pick("SUBCATNAME", "SUBCATEGORY", "SUB_CATEGORY"),
            filing_datetime=parse_bse_datetime(
                pick("NEWS_DT", "NEWSDATE", "News_submission_dt", "DT_TM")
            ),
            dissemination_datetime=parse_bse_datetime(
                pick("DissemDT", "DISSEMINATION_DT", "News_submission_dt")
            ),
            pdf_url=pdf_candidates[0] if pdf_candidates else None,
            exchange_url=f"{SITE_ROOT}/corporates/ann.html",
            raw_payload={
                **{str(k): v for k, v in row.items()},
                "_pdf_candidates": pdf_candidates,
            },
        )

    # -- Playwright fallback ----------------------------------------------
    def _playwright_fetch(
        self, category: str, from_date: dt.date, to_date: dt.date, page: int
    ) -> Optional[Any]:
        """Run the same XHR from inside a real browser to clear the cookie wall.

        Playwright is an optional dependency: if it is not installed the caller
        simply loses the fallback rather than the whole run.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("Playwright fallback unavailable (package not installed)")
            return None

        url = (
            f"{ANNOUNCEMENTS_ENDPOINT}?pageno={page}&strCat={category or '-1'}"
            f"&strPrevDate={from_date.strftime('%Y%m%d')}&strScrip=&strSearch=P"
            f"&strToDate={to_date.strftime('%Y%m%d')}&strType=C&subcategory=-1"
        )
        headless = bool(self.cfg.get("playwright_headless", True))
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=headless)
                context = browser.new_context(user_agent=self.cfg.get("user_agent"))
                browser_page = context.new_page()
                browser_page.goto(ANNOUNCEMENTS_PAGE, wait_until="domcontentloaded", timeout=60_000)
                raw = browser_page.evaluate(
                    """async (target) => {
                        const res = await fetch(target, {credentials: 'include'});
                        return await res.text();
                    }""",
                    url,
                )
                browser.close()
            log.info("Playwright fallback succeeded", extra={"category": category, "page": page})
            return json.loads(raw)
        except Exception as exc:  # pragma: no cover - browser-dependent
            log.error("Playwright fallback failed", extra={"error": str(exc)})
            return None

    def close(self) -> None:
        self.client.close()
