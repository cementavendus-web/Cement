"""NSE corporate announcements — optional enrichment source.

NSE's API refuses any request that does not carry cookies minted by loading the
site root first, so the client warms up before every run. Output is the same
:class:`RawFiling` shape as BSE, which lets the dedupe layer collapse the same
announcement filed with both exchanges into one row.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, Iterator, Optional

from ..config import Config
from .base import HttpClient, ScraperError, client_from_config
from .bse import parse_bse_datetime
from .models import RawFiling

log = logging.getLogger(__name__)

SITE_ROOT = "https://www.nseindia.com"
ANNOUNCEMENTS_ENDPOINT = f"{SITE_ROOT}/api/corporate-announcements"

NSE_HEADERS = {
    "Referer": f"{SITE_ROOT}/companies-listing/corporate-filings-announcements",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
}


class NseScraper:
    source = "NSE"

    def __init__(self, config: Config, client: Optional[HttpClient] = None) -> None:
        self.config = config
        self.cfg = config.section("scraper")
        self.client = client or client_from_config(config, NSE_HEADERS)
        self._warmed = False

    def _ensure_warm(self) -> None:
        if not self._warmed:
            self.client.warm_up(SITE_ROOT)
            self.client.warm_up(NSE_HEADERS["Referer"])
            self._warmed = True

    def fetch_announcements(
        self, from_date: dt.date, to_date: dt.date
    ) -> Iterator[RawFiling]:
        self._ensure_warm()
        params = {
            "index": "equities",
            "from_date": from_date.strftime("%d-%m-%Y"),
            "to_date": to_date.strftime("%d-%m-%Y"),
        }
        try:
            payload = self.client.get_json(
                ANNOUNCEMENTS_ENDPOINT, params=params, headers=NSE_HEADERS
            )
        except ScraperError as exc:
            log.error("NSE fetch failed", extra={"error": str(exc)})
            return

        rows = payload if isinstance(payload, list) else payload.get("data", [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            filing = self.parse_row(row)
            if filing is not None:
                yield filing
        log.info("NSE announcements scraped", extra={"rows": len(rows)})

    def parse_row(self, row: Dict[str, Any]) -> Optional[RawFiling]:
        symbol = row.get("symbol") or row.get("Symbol")
        company = row.get("sm_name") or row.get("comp") or symbol
        headline = row.get("desc") or row.get("subject") or ""
        if not company:
            return None
        body = (row.get("attchmntText") or row.get("smIndustry") or "").strip()
        return RawFiling(
            source=self.source,
            source_filing_id=str(row.get("seqId") or row.get("sr_no") or "") or None,
            company_name=str(company).strip(),
            nse_symbol=str(symbol).strip() if symbol else None,
            isin=row.get("sm_isin") or row.get("isin"),
            headline=str(headline).strip(),
            body_text=body,
            category_hint=row.get("desc"),
            filing_datetime=parse_bse_datetime(row.get("an_dt") or row.get("exchdisstime")),
            dissemination_datetime=parse_bse_datetime(row.get("exchdisstime")),
            pdf_url=row.get("attchmntFile") or None,
            exchange_url=NSE_HEADERS["Referer"],
            raw_payload=dict(row),
        )

    def close(self) -> None:
        self.client.close()
