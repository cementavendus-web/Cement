"""SEBI filings — optional enrichment for offer documents.

SEBI publishes DRHP/RHP and buyback/open-offer documents as HTML listing pages
rather than a JSON API, so this scraper is BeautifulSoup-based. It is disabled
by default (``scraper.enable_sebi``) because the listing markup changes more
often than the exchange APIs; treat it as a best-effort enrichment that must
never fail the main run.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Iterator, Optional
from urllib.parse import urljoin

from ..config import Config
from .base import HttpClient, ScraperError, client_from_config
from .bse import parse_bse_datetime
from .models import RawFiling

log = logging.getLogger(__name__)

SITE_ROOT = "https://www.sebi.gov.in"
# Public issue offer documents (DRHPs and RHPs filed with the regulator).
FILING_PAGES = {
    "DRHP": f"{SITE_ROOT}/filings/public-issues.html",
    "RHP": f"{SITE_ROOT}/filings/public-issues.html?doPagination=yes",
}


class SebiScraper:
    source = "SEBI"

    def __init__(self, config: Config, client: Optional[HttpClient] = None) -> None:
        self.config = config
        self.client = client or client_from_config(config, {"Referer": SITE_ROOT})

    def fetch_filings(
        self, from_date: dt.date, to_date: dt.date
    ) -> Iterator[RawFiling]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover - optional dependency
            log.warning("SEBI scraper needs beautifulsoup4; skipping")
            return

        for hint, url in FILING_PAGES.items():
            try:
                response = self.client.request("GET", url)
            except ScraperError as exc:
                log.error("SEBI page fetch failed", extra={"url": url, "error": str(exc)})
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            for row in soup.select("table tr"):
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                if len(cells) < 2:
                    continue
                filed_on = parse_bse_datetime(cells[0])
                if filed_on and not (from_date <= filed_on.date() <= to_date):
                    continue
                link = row.find("a", href=True)
                title = cells[1] if len(cells) > 1 else (link.get_text(strip=True) if link else "")
                if not title:
                    continue
                yield RawFiling(
                    source=self.source,
                    source_filing_id=None,
                    company_name=re.sub(r"\s*-\s*(DRHP|RHP).*$", "", title, flags=re.I).strip(),
                    headline=title,
                    body_text=" ".join(cells[2:])[:2000],
                    category_hint=hint,
                    filing_datetime=filed_on,
                    pdf_url=urljoin(SITE_ROOT, link["href"]) if link else None,
                    exchange_url=url,
                    raw_payload={"cells": cells},
                )

    def close(self) -> None:
        self.client.close()
