"""Daily equity bhavcopy — close price and traded volume.

Added because two things the deal archives cannot supply are needed downstream:

* the rupee value of a *holding* (shares held x current price) for the brief's
  ``stake_cr`` column — deal records only give the value of a trade;
* average daily volume, which drives **days-of-ADV to liquidate**, the single
  most informative liquidity feature for block-deal prediction (a 6% stake in a
  thinly traded name cannot leave through the market, so it goes as a block).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import zipfile
from typing import Any, Dict, Iterator, List, Optional

from ..config import Config
from .base import HttpClient, ScraperError, client_from_config

log = logging.getLogger(__name__)

# BSE publishes a dated zip; NSE a dated CSV. Both change shape occasionally,
# so parsing goes through the same synonym approach as the deal archives.
BSE_BHAVCOPY = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{date}_F_0000.CSV"
NSE_BHAVCOPY = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"

_SYNONYMS: Dict[str, tuple] = {
    "code": ("TckrSymb", "SC_CODE", "SYMBOL", "Scrip Code", "SC_NAME"),
    "close": ("ClsPric", "CLOSE", "CLOSE_PRICE", "Close", "LAST_PRICE"),
    "volume": ("TtlTradgVol", "NO_OF_SHRS", "TOTTRDQTY", "Volume"),
    "turnover": ("TtlTrfVal", "NET_TURNOV", "TOTTRDVAL", "Turnover"),
    "series": ("SctySrs", "SERIES", "Series"),
}


def _pick(row: Dict[str, Any], field: str) -> Optional[Any]:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in _SYNONYMS[field]:
        value = lowered.get(name.lower())
        if value not in (None, "", "-"):
            return value
    return None


def _to_number(raw: Any) -> Optional[float]:
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_bhavcopy(text: str, trade_date: dt.date, exchange: str = "BSE") -> List[Dict[str, Any]]:
    """Map a bhavcopy CSV onto quote rows, equity series only."""
    out: List[Dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(text)):
        series = str(_pick(row, "series") or "EQ").strip().upper()
        # Derivatives and debt rows share the file and are not tradeable equity.
        if series not in {"EQ", "BE", "A", "B", "T", ""}:
            continue
        code = _pick(row, "code")
        close = _to_number(_pick(row, "close"))
        if not code or close is None:
            continue
        out.append(
            {
                "code": str(code).strip(),
                "trade_date": trade_date,
                "close_price": close,
                "volume": _to_number(_pick(row, "volume")),
                "turnover_inr": _to_number(_pick(row, "turnover")),
                "exchange": exchange,
            }
        )
    return out


class BhavcopyScraper:
    def __init__(self, config: Config, client: Optional[HttpClient] = None) -> None:
        self.config = config
        self.client = client or client_from_config(
            config, {"Referer": "https://www.bseindia.com/"}
        )

    def fetch(self, trade_date: dt.date, exchange: str = "BSE") -> List[Dict[str, Any]]:
        """One day's quotes. Returns [] on a holiday or a fetch failure."""
        stamp = trade_date.strftime("%Y%m%d")
        url = (BSE_BHAVCOPY if exchange == "BSE" else NSE_BHAVCOPY).format(date=stamp)
        try:
            response = self.client.request("GET", url)
        except ScraperError as exc:
            # Weekends and exchange holidays 404 — expected, not an error.
            log.info("Bhavcopy unavailable", extra={"date": stamp, "error": str(exc)})
            return []

        body = response.content
        if body[:2] == b"PK":  # zipped (NSE)
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                name = archive.namelist()[0]
                text = archive.read(name).decode("utf-8", errors="replace")
        else:
            text = body.decode("utf-8", errors="replace")
        return parse_bhavcopy(text, trade_date, exchange)

    def fetch_range(
        self, from_date: dt.date, to_date: dt.date, exchange: str = "BSE"
    ) -> Iterator[Dict[str, Any]]:
        day = from_date
        while day <= to_date:
            if day.weekday() < 5:          # skip weekends without a request
                yield from self.fetch(day, exchange)
            day += dt.timedelta(days=1)

    def close(self) -> None:
        self.client.close()
