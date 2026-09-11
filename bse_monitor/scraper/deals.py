"""Bulk/block deal and SAST ingestion — the label spine for Layer 3.

Source reality, which shapes the design:

* **NSE and BSE bulk/block deals** are cleanly archived as dated CSV/JSON, so a
  10-year backfill is mechanical.
* **SAST Regulation 29(2)** disclosures are per-company PDFs with no bulk
  archive. They matter because an off-market promoter or PE sale never prints as
  a block deal — precisely the trades most worth predicting. They are therefore
  ingested best-effort and the resulting label coverage is *reported per year*
  rather than assumed.

Column names differ between exchanges and have changed over the decade, so every
parser maps through a synonym table rather than fixed indices.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from ..config import Config
from .base import HttpClient, ScraperError, client_from_config

log = logging.getLogger(__name__)

NSE_ROOT = "https://www.nseindia.com"
NSE_ARCHIVES = "https://nsearchives.nseindia.com"
NSE_BULK_HISTORICAL = f"{NSE_ROOT}/api/historicalOR/bulk-block-short-deals"
BSE_DEALS_API = "https://api.bseindia.com/BseIndiaAPI/api/BulknBlockDeals/w"

NSE_HEADERS = {
    "Referer": f"{NSE_ROOT}/report-detail/display-bulk-and-block-deals",
    "Sec-Fetch-Site": "same-origin",
}
BSE_HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}

# Header synonyms. NSE has used at least three spellings for the client name
# column alone across the archive.
_FIELD_SYNONYMS: Dict[str, Sequence[str]] = {
    "symbol": ("symbol", "SYMBOL", "Symbol", "scrip_cd", "SCRIP_CD", "Scrip Code"),
    "company": ("security name", "SECURITY NAME", "Security Name", "scrip_name",
                "SCRIP_NAME", "Security_Name", "company"),
    "date": ("date", "DATE", "Date", "trade_date", "TRADE_DATE", "Deal Date", "dealDate"),
    "holder": ("client name", "CLIENT NAME", "Client Name", "clientName",
               "client_name", "ClientName", "Client_Name", "name"),
    "side": ("buy/sell", "BUY/SELL", "Buy/Sell", "buySell", "buy_sell", "DealType",
             "Deal Type", "dealType"),
    "quantity": ("quantity traded", "QUANTITY TRADED", "Quantity Traded", "quantity",
                 "QTY", "qty", "Qty", "dealQty", "No. of Shares"),
    "price": ("trade price / wght. avg. price", "TRADE PRICE", "Trade Price",
              "price", "PRICE", "dealPrice", "Trade Price / Wght. Avg. Price"),
}


def _pick(row: Dict[str, Any], field: str) -> Optional[Any]:
    """Case-insensitive, synonym-aware column lookup."""
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in _FIELD_SYNONYMS.get(field, ()):
        value = lowered.get(name.strip().lower())
        if value not in (None, "", "-"):
            return value
    return None


_NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")


def _to_number(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    match = _NUM_RE.search(str(raw))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


_DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%y", "%Y%m%d")


def parse_deal_date(raw: Any) -> Optional[dt.date]:
    if not raw:
        return None
    if isinstance(raw, dt.date):
        return raw
    text = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_side(raw: Any) -> str:
    """Map every spelling of buy/sell onto the two values the schema allows."""
    text = str(raw or "").strip().upper()
    if text.startswith("S") or "SELL" in text:
        return "SELL"
    return "BUY"


class DealRecord(dict):
    """A parsed deal, shaped for :func:`repository.upsert_disposal`."""


def parse_deal_row(row: Dict[str, Any], source: str) -> Optional[DealRecord]:
    """Map one archive row onto a :class:`DealRecord`, or None if unusable."""
    trade_date = parse_deal_date(_pick(row, "date"))
    holder = _pick(row, "holder")
    company = _pick(row, "company")
    symbol = _pick(row, "symbol")
    if not trade_date or not holder or not (company or symbol):
        return None

    quantity = _to_number(_pick(row, "quantity"))
    price = _to_number(_pick(row, "price"))
    return DealRecord(
        source=source,
        company_name=str(company or symbol).strip(),
        symbol=str(symbol).strip() if symbol else None,
        holder_name=str(holder).strip(),
        trade_date=trade_date,
        side=normalize_side(_pick(row, "side")),
        quantity=quantity,
        price=price,
        # Deal value is computable from the record itself — no price feed needed
        # for the >= Rs 100 crore half of the label.
        value_inr=(quantity * price) if (quantity and price) else None,
        exchange="NSE" if source.startswith("NSE") else "BSE",
        raw=dict(row),
    )


def parse_deal_csv(text: str, source: str) -> List[DealRecord]:
    reader = csv.DictReader(io.StringIO(text))
    out: List[DealRecord] = []
    for row in reader:
        record = parse_deal_row(row, source)
        if record is not None:
            out.append(record)
    return out


class DealScraper:
    """Reads bulk and block deals from both exchanges."""

    def __init__(self, config: Config, client: Optional[HttpClient] = None) -> None:
        self.config = config
        self.client = client or client_from_config(config, NSE_HEADERS)
        self._warmed = False

    def _warm(self) -> None:
        if not self._warmed:
            self.client.warm_up(NSE_ROOT)
            self._warmed = True

    def fetch_nse(
        self, from_date: dt.date, to_date: dt.date, deal_type: str = "bulk"
    ) -> Iterator[DealRecord]:
        self._warm()
        params = {
            "optionType": deal_type,
            "from": from_date.strftime("%d-%m-%Y"),
            "to": to_date.strftime("%d-%m-%Y"),
        }
        source = "NSE_BULK" if deal_type == "bulk" else "NSE_BLOCK"
        try:
            payload = self.client.get_json(
                NSE_BULK_HISTORICAL, params=params, headers=NSE_HEADERS
            )
        except ScraperError as exc:
            log.error("NSE deals fetch failed", extra={"error": str(exc), "type": deal_type})
            return
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        for row in rows:
            if isinstance(row, dict):
                record = parse_deal_row(row, source)
                if record is not None:
                    yield record

    def fetch_bse(
        self, from_date: dt.date, to_date: dt.date, deal_type: str = "B"
    ) -> Iterator[DealRecord]:
        """``deal_type``: ``B`` bulk, ``K`` block (BSE's own codes)."""
        params = {
            "pageno": 1,
            "flag": deal_type,
            "strFromDate": from_date.strftime("%Y%m%d"),
            "strToDate": to_date.strftime("%Y%m%d"),
            "strScrip": "",
        }
        source = "BSE_BULK" if deal_type == "B" else "BSE_BLOCK"
        try:
            payload = self.client.get_json(BSE_DEALS_API, params=params, headers=BSE_HEADERS)
        except ScraperError as exc:
            log.error("BSE deals fetch failed", extra={"error": str(exc), "type": deal_type})
            return
        rows = payload.get("Table", []) if isinstance(payload, dict) else payload
        for row in rows or []:
            if isinstance(row, dict):
                record = parse_deal_row(row, source)
                if record is not None:
                    yield record

    def fetch_all(self, from_date: dt.date, to_date: dt.date) -> Iterator[DealRecord]:
        for deal_type in ("bulk", "block"):
            yield from self.fetch_nse(from_date, to_date, deal_type)
        for flag in ("B", "K"):
            yield from self.fetch_bse(from_date, to_date, flag)

    def close(self) -> None:
        self.client.close()


def disposals_from_filings(filings: Iterable[Any]) -> Iterator[DealRecord]:
    """Derive sell records from already-classified OFS/exit/promoter filings.

    This is the SAST-and-offer-document half of the spine: off-market sales that
    never print as an exchange deal still appear here, because the filing that
    disclosed them was already parsed by Layers 1-2.
    """
    sell_categories = {"OFS", "BlockDeal", "InvestorExit", "PromoterSale"}
    for filing in filings:
        if filing.category not in sell_categories or filing.is_duplicate:
            continue
        transaction = next(iter(getattr(filing, "transactions", []) or []), None)
        holders = [
            link.investor.name
            for link in getattr(filing, "investor_links", []) or []
            if getattr(link, "investor", None)
        ] or [
            link.promoter.name
            for link in getattr(filing, "promoter_links", []) or []
            if getattr(link, "promoter", None)
        ]
        if not holders:
            holders = ["Promoter" if filing.category == "PromoterSale" else "Unknown"]

        for holder in holders:
            yield DealRecord(
                source="SAST",
                company_name=getattr(filing.company, "name", "") or "",
                symbol=getattr(filing.company, "bse_code", None),
                holder_name=holder,
                trade_date=filing.filing_date,
                side="SELL",
                quantity=float(transaction.num_shares) if transaction and transaction.num_shares else None,
                price=float(transaction.price_per_share) if transaction and transaction.price_per_share else None,
                value_inr=float(transaction.amount_inr) if transaction and transaction.amount_inr else None,
                percent_of_equity=(
                    transaction.percent_of_equity if transaction else None
                ),
                exchange="BSE",
                raw={"filing_id": filing.id, "category": filing.category},
                source_filing_id=filing.id,
            )
