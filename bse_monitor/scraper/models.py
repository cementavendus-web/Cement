"""Source-agnostic announcement record produced by every scraper."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Dict, Optional


@dataclasses.dataclass
class RawFiling:
    """One corporate announcement, before classification.

    Scrapers are responsible only for filling this in faithfully; all cleaning,
    classification and scoring happens downstream so that adding a source never
    means re-implementing the analysis.
    """

    source: str
    source_filing_id: Optional[str]
    company_name: str
    bse_code: Optional[str] = None
    nse_symbol: Optional[str] = None
    isin: Optional[str] = None
    headline: str = ""
    body_text: str = ""
    category_hint: Optional[str] = None
    subcategory_hint: Optional[str] = None
    filing_datetime: Optional[dt.datetime] = None
    dissemination_datetime: Optional[dt.datetime] = None
    pdf_url: Optional[str] = None
    exchange_url: Optional[str] = None
    raw_payload: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def filing_date(self) -> Optional[dt.date]:
        return self.filing_datetime.date() if self.filing_datetime else None

    def combined_text(self) -> str:
        return "\n".join(part for part in (self.headline, self.body_text) if part).strip()

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)
