"""Interpretation of tables lifted out of filing PDFs.

Two table shapes carry most of the structured value in these documents:

* **Shareholding / disclosure tables** (Regulation 29/31 filings) — one row per
  holder with before/after share counts and percentages. These tell us who sold
  and how much.
* **Issue-detail tables** (QIP/preferential placement documents) — allottee,
  number of shares, price per share.

Anything that does not look like either is kept verbatim on the document record
but contributes nothing to the transaction extraction, which keeps noisy
layout artefacts out of the database.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

HOLDER_HEADERS = ("name", "holder", "shareholder", "allottee", "entity", "acquirer", "seller")
SHARE_HEADERS = ("no. of shares", "number of shares", "shares", "no of shares", "quantity", "qty")
PERCENT_HEADERS = ("%", "percentage", "% of", "holding", "shareholding")
PRICE_HEADERS = ("price", "rate", "per share", "issue price")


@dataclasses.dataclass
class TableRow:
    holder: Optional[str] = None
    shares: Optional[float] = None
    percent: Optional[float] = None
    price: Optional[float] = None
    raw: Sequence[str] = ()


def _to_number(cell: str) -> Optional[float]:
    if not cell:
        return None
    match = _NUMBER_RE.search(str(cell).replace("(", "-").replace(")", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _header_index(header: Sequence[str], candidates: Sequence[str]) -> Optional[int]:
    for index, cell in enumerate(header):
        low = str(cell or "").strip().lower()
        if any(candidate in low for candidate in candidates):
            return index
    return None


def parse_table(table: Dict[str, Any]) -> List[TableRow]:
    """Map one extracted table onto :class:`TableRow` records."""
    rows = table.get("rows") or []
    if len(rows) < 2:
        return []

    header = [str(cell or "") for cell in rows[0]]
    holder_idx = _header_index(header, HOLDER_HEADERS)
    shares_idx = _header_index(header, SHARE_HEADERS)
    percent_idx = _header_index(header, PERCENT_HEADERS)
    price_idx = _header_index(header, PRICE_HEADERS)

    # Without a holder column and at least one numeric column this is layout
    # noise, not data.
    if holder_idx is None or (shares_idx is None and percent_idx is None):
        return []

    parsed: List[TableRow] = []
    for raw_row in rows[1:]:
        cells = [str(cell or "").strip() for cell in raw_row]
        if holder_idx >= len(cells):
            continue
        holder = cells[holder_idx].strip()
        if not holder or holder.lower() in {"total", "grand total", "-"}:
            continue
        parsed.append(
            TableRow(
                holder=holder,
                shares=_to_number(cells[shares_idx]) if shares_idx is not None and shares_idx < len(cells) else None,
                percent=_to_number(cells[percent_idx]) if percent_idx is not None and percent_idx < len(cells) else None,
                price=_to_number(cells[price_idx]) if price_idx is not None and price_idx < len(cells) else None,
                raw=cells,
            )
        )
    return parsed


def parse_tables(tables: Sequence[Dict[str, Any]]) -> List[TableRow]:
    out: List[TableRow] = []
    for table in tables or []:
        try:
            out.extend(parse_table(table))
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Table parse failed", extra={"error": str(exc)})
    return out


def summarise(rows: Sequence[TableRow]) -> Dict[str, Any]:
    """Aggregate view used to enrich a filing when free text is uninformative."""
    with_shares = [r for r in rows if r.shares]
    with_percent = [r for r in rows if r.percent is not None]
    return {
        "row_count": len(rows),
        "holders": [r.holder for r in rows if r.holder][:25],
        "total_shares": sum(r.shares or 0 for r in with_shares) or None,
        "max_percent": max((r.percent for r in with_percent), default=None),
        "prices": sorted({r.price for r in rows if r.price}) or None,
    }
