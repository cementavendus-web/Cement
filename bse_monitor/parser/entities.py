"""Entity extraction from filing text.

Everything the spec asks for is pulled out here: monetary amounts, share
counts, price per share, stake percentages, lock-in expiry and board-meeting
dates, investor names and promoter names.

The approach is regex-first, spaCy-second:

* Indian financial filings express money in a small, highly regular set of
  shapes (``Rs. 1,200 crore``, ``INR 1,200 Cr``, ``₹ 12,00,00,000``), and a
  tuned regex beats a general NER model on both precision and cost.
* spaCy is used only for the open-ended part — discovering institutional names
  that are not already in the tracked registry — and degrades gracefully to a
  suffix heuristic when the model is not installed.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from .text import clean_text, sentence_at, sentence_spans, split_sentences, window

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------
# Indian scale words -> multiplier in rupees.
SCALE_MULTIPLIERS: Dict[str, float] = {
    "thousand": 1e3,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "million": 1e6,
    "mn": 1e6,
    "crore": 1e7,
    "crores": 1e7,
    "cr": 1e7,
    "billion": 1e9,
    "bn": 1e9,
    "trillion": 1e12,
}
CRORE = 1e7

_CURRENCY = r"(?:rs\.?|inr|₹|usd|us\$|\$|eur|€)"
_NUMBER = r"\d{1,3}(?:[,\d]*\d)?(?:\.\d+)?"
_SCALE = r"(?:thousand|lakhs?|lacs?|millions?|mn|crores?|cr|billions?|bn|trillion)"

# "Rs. 1,200 crore" / "₹1200 Cr" / "INR 1,200.50 crores"
_MONEY_PREFIX_RE = re.compile(
    rf"(?P<cur>{_CURRENCY})\s*(?P<num>{_NUMBER})\s*(?P<scale>{_SCALE})?",
    re.IGNORECASE,
)
# "1,200 crore rupees" / "1200 crore" where the currency trails or is implied
_MONEY_SUFFIX_RE = re.compile(
    rf"(?P<num>{_NUMBER})\s*(?P<scale>{_SCALE})\s*(?:rupees|rs\.?|inr)?",
    re.IGNORECASE,
)

_FX_TO_INR = {"usd": 83.0, "us$": 83.0, "$": 83.0, "eur": 90.0, "€": 90.0}

# Matches the text right after a figure when the figure is a per-unit price.
_UNIT_PRICE_TRAIL_RE = re.compile(
    r"^(?:/-)?\s*(?:per|each|a\s+piece)\b|^(?:/-)?\s*\(?\s*(?:per|each)\b",
    re.IGNORECASE,
)
# How much text after a figure is inspected for the per-unit cue.
_TRAIL_CHARS = 28


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None


@dataclasses.dataclass
class MoneyAmount:
    value_inr: float
    raw: str
    currency: str = "INR"
    scale: Optional[str] = None
    context: str = ""
    # The sentence the amount appears in. Cue matching is scoped to this rather
    # than a character window: filings pack several figures into one paragraph,
    # and a fixed-radius window bleeds the neighbouring sentence's cues in.
    sentence: str = ""
    # Text immediately after the figure, used to spot a per-unit price.
    trailing: str = ""

    @property
    def crore(self) -> float:
        return self.value_inr / CRORE

    @property
    def is_unit_price(self) -> bool:
        """``Rs 745 per equity share`` is a price, never a deal size.

        The check is deliberately local to the figure rather than scoped to the
        sentence: a preferential-issue filing says "at an issue price of Rs. 210
        per warrant, aggregating Rs. 105 crore" in one breath, and a
        sentence-wide rule would throw away the aggregate too.
        """
        return bool(_UNIT_PRICE_TRAIL_RE.match(self.trailing.strip()))


def extract_amounts(text: str, fx_rates: Optional[Dict[str, float]] = None) -> List[MoneyAmount]:
    """Find every monetary amount, normalised to rupees.

    Foreign-currency amounts are converted with a static table: filings quote
    USD rarely and the score only needs the right order of magnitude, so a live
    FX feed would add a failure mode for no analytic gain.
    """
    if not text:
        return []
    rates = {**_FX_TO_INR, **(fx_rates or {})}
    sentences = sentence_spans(text)
    found: List[MoneyAmount] = []
    spans: List[tuple[int, int]] = []

    for match in _MONEY_PREFIX_RE.finditer(text):
        number = _to_float(match.group("num"))
        if number is None:
            continue
        scale = (match.group("scale") or "").lower()
        multiplier = SCALE_MULTIPLIERS.get(scale, 1.0)
        currency = match.group("cur").lower().rstrip(".")
        value = number * multiplier * rates.get(currency, 1.0)
        found.append(
            MoneyAmount(
                value_inr=value,
                raw=match.group(0).strip(),
                currency="INR" if currency in {"rs", "inr", "₹"} else currency.upper(),
                scale=scale or None,
                context=window(text, match.start(), match.end()),
                sentence=sentence_at(sentences, match.start()),
                trailing=text[match.end() : match.end() + _TRAIL_CHARS],
            )
        )
        spans.append(match.span())

    for match in _MONEY_SUFFIX_RE.finditer(text):
        # Skip anything already captured by the currency-prefixed pattern.
        if any(start <= match.start() < end for start, end in spans):
            continue
        number = _to_float(match.group("num"))
        if number is None:
            continue
        scale = match.group("scale").lower()
        multiplier = SCALE_MULTIPLIERS.get(scale)
        if multiplier is None:
            continue
        found.append(
            MoneyAmount(
                value_inr=number * multiplier,
                raw=match.group(0).strip(),
                scale=scale,
                context=window(text, match.start(), match.end()),
                sentence=sentence_at(sentences, match.start()),
                trailing=text[match.end() : match.end() + _TRAIL_CHARS],
            )
        )

    return found


# Amounts adjacent to these words describe the deal size we care about; a
# figure next to "authorised share capital" or "net worth" does not.
_DEAL_CUES = (
    "aggregating",
    "aggregate",
    "up to",
    "upto",
    "issue size",
    "raise",
    "raising",
    "fund raise",
    "consideration",
    "offer size",
    "total size",
    "amount of",
    "value of",
    "worth",
)
_AMOUNT_ANTI_CUES = (
    "authorised share capital",
    "authorized share capital",
    "net worth",
    "turnover",
    "revenue from operations",
    "profit after tax",
    "market capitalisation",
    "paid-up capital",
    "paid up capital",
)


def best_deal_amount(text: str) -> Optional[MoneyAmount]:
    """Pick the amount most likely to be the deal size.

    Preference order: an amount sitting next to a deal cue, then the largest
    amount that is not next to an anti-cue. Filings routinely mention several
    numbers and the largest one is often the authorised capital, so the cue
    check has to come first.
    """
    amounts = extract_amounts(text)
    if not amounts:
        return None

    # If every figure is a per-unit price there is no deal size to report; the
    # caller derives one from shares x price instead of quoting the price as a
    # transaction value.
    amounts = [a for a in amounts if not a.is_unit_price]
    if not amounts:
        return None

    def scope(amount: MoneyAmount) -> str:
        return (amount.sentence or amount.context).lower()

    def anti(amount: MoneyAmount) -> bool:
        return any(cue in scope(amount) for cue in _AMOUNT_ANTI_CUES)

    cued = [a for a in amounts if not anti(a) and any(c in scope(a) for c in _DEAL_CUES)]
    if cued:
        return max(cued, key=lambda a: a.value_inr)
    clean = [a for a in amounts if not anti(a)]
    return max(clean or amounts, key=lambda a: a.value_inr)


# --------------------------------------------------------------------------
# Shares, prices, percentages
# --------------------------------------------------------------------------
_SHARES_RE = re.compile(
    rf"(?P<num>{_NUMBER})\s*(?P<scale>{_SCALE})?\s*"
    r"(?:equity\s+)?(?:shares|share|warrants|securities|units)\b",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    rf"(?:at\s+(?:a\s+)?(?:price|issue price|floor price)\s*(?:of)?\s*|"
    rf"price\s+(?:of|per\s+share)\s*|@\s*)"
    rf"(?:{_CURRENCY})?\s*(?P<num>{_NUMBER})\s*(?:per\s+(?:equity\s+)?share)?",
    re.IGNORECASE,
)
_PER_SHARE_RE = re.compile(
    rf"(?:{_CURRENCY})\s*(?P<num>{_NUMBER})\s*(?:/-)?\s*per\s+(?:equity\s+)?share",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(
    r"(?P<num>\d{1,3}(?:\.\d+)?)\s*%\s*(?:of\s+(?:the\s+)?"
    r"(?:total\s+)?(?:paid[\s-]?up\s+)?(?:equity|share\s+capital|shareholding|shares))?",
    re.IGNORECASE,
)


def extract_share_count(text: str) -> Optional[float]:
    """Largest plausible share count in the text."""
    best: Optional[float] = None
    for match in _SHARES_RE.finditer(text or ""):
        number = _to_float(match.group("num"))
        if number is None:
            continue
        scale = (match.group("scale") or "").lower()
        value = number * SCALE_MULTIPLIERS.get(scale, 1.0)
        # A "share count" under 100 with no scale word is almost always a ratio
        # ("1 share for every 4 held"), not a transaction size.
        if value < 100 and not scale:
            continue
        best = value if best is None else max(best, value)
    return best


def extract_price_per_share(text: str) -> Optional[float]:
    for pattern in (_PER_SHARE_RE, _PRICE_RE):
        match = pattern.search(text or "")
        if match:
            value = _to_float(match.group("num"))
            if value is not None and 0 < value < 1_000_000:
                return value
    return None


def extract_stake_percent(text: str) -> Optional[float]:
    """Highest stake percentage mentioned, ignoring implausible values."""
    best: Optional[float] = None
    for match in _PERCENT_RE.finditer(text or ""):
        value = _to_float(match.group("num"))
        if value is None or value <= 0 or value > 100:
            continue
        best = value if best is None else max(best, value)
    return best


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
_DATE_PATTERNS = (
    (re.compile(r"\b(\d{1,2})[\-/\s](\d{1,2})[\-/\s](\d{4})\b"), "dmy"),
    (
        re.compile(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
            r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "dmy_name",
    ),
    (
        re.compile(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
            r"(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "mdy_name",
    ),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
)
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def parse_dates(text: str) -> List[tuple[dt.date, int, int]]:
    """All parseable dates as ``(date, start, end)`` spans."""
    results: List[tuple[dt.date, int, int]] = []
    for pattern, kind in _DATE_PATTERNS:
        for match in pattern.finditer(text or ""):
            try:
                if kind == "dmy":
                    day, month, year = (int(match.group(i)) for i in (1, 2, 3))
                elif kind == "ymd":
                    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
                elif kind == "dmy_name":
                    day = int(match.group(1))
                    month = _MONTHS[match.group(2).lower()[:3]]
                    year = int(match.group(3))
                else:
                    month = _MONTHS[match.group(1).lower()[:3]]
                    day = int(match.group(2))
                    year = int(match.group(3))
                results.append((dt.date(year, month, day), match.start(), match.end()))
            except (ValueError, KeyError):
                continue
    return results


# A date appearing *before* its cue is usually part of the previous clause
# ("...listed on 13 March. A special resolution was passed on 02 February"), so
# backward distance is penalised rather than treated as equivalent.
_BACKWARD_PENALTY = 4


def _date_near(text: str, cues: Sequence[str], radius: int = 200) -> Optional[dt.date]:
    """The date a cue phrase refers to.

    Resolution order: a date in the same sentence as the cue wins outright;
    otherwise the nearest date within ``radius``, with backward distance
    penalised. Sentence scoping is what stops the tail of the preceding sentence
    from capturing the cue — the same failure the money extractor has.
    """
    low = (text or "").lower()
    cue_positions: List[int] = []
    for cue in cues:
        start = low.find(cue)
        while start >= 0:
            cue_positions.append(start)
            start = low.find(cue, start + 1)
    if not cue_positions:
        return None

    candidates = parse_dates(text)
    if not candidates:
        return None

    spans = sentence_spans(text)

    def sentence_bounds(offset: int) -> tuple[int, int]:
        for start, end, _body in spans:
            if start <= offset < end:
                return start, end
        return -1, -1

    def weighted(distance: int, date_start: int, cue_pos: int) -> int:
        return distance if date_start >= cue_pos else distance * _BACKWARD_PENALTY

    best_in_sentence: Optional[tuple[int, dt.date]] = None
    best_anywhere: Optional[tuple[int, dt.date]] = None

    for date_value, start, _end in candidates:
        for cue_pos in cue_positions:
            distance = abs(start - cue_pos)
            if distance > radius:
                continue
            score = weighted(distance, start, cue_pos)
            lo, hi = sentence_bounds(cue_pos)
            if lo >= 0 and lo <= start < hi:
                if best_in_sentence is None or score < best_in_sentence[0]:
                    best_in_sentence = (score, date_value)
            if best_anywhere is None or score < best_anywhere[0]:
                best_anywhere = (score, date_value)

    chosen = best_in_sentence or best_anywhere
    return chosen[1] if chosen else None


LOCK_IN_CUES = (
    "lock-in",
    "lock in",
    "lockin",
    "lock-in expiry",
    "expiry of lock",
    "lock-in period",
)
BOARD_MEETING_CUES = (
    "board meeting",
    "meeting of the board",
    "board of directors",
    "meeting held on",
    "meeting to be held on",
    "board will meet",
    "its meeting",
)
RECORD_DATE_CUES = ("record date", "cut-off date", "cut off date")


def extract_lock_in_expiry(text: str) -> Optional[dt.date]:
    return _date_near(text, LOCK_IN_CUES)


def extract_board_meeting_date(text: str) -> Optional[dt.date]:
    return _date_near(text, BOARD_MEETING_CUES)


def extract_record_date(text: str) -> Optional[dt.date]:
    return _date_near(text, RECORD_DATE_CUES)


# Anchor dates for the forward calendar. Each rule in triggers/rules.py counts
# its statutory offset from one of these.
ALLOTMENT_CUES = (
    "date of allotment",
    "allotment of equity shares",
    "allotted on",
    "basis of allotment",
    "allotment was made",
    "allotment date",
)
LISTING_CUES = (
    "date of listing",
    "listed on",
    "commencement of trading",
    "listing and trading approval",
    "trading approval",
    "listing date",
)
RESOLUTION_CUES = (
    "special resolution",
    "shareholders approved",
    "passed by the shareholders",
    "postal ballot",
    "extra-ordinary general meeting",
    "extraordinary general meeting",
    "annual general meeting",
)


def extract_allotment_date(text: str) -> Optional[dt.date]:
    return _date_near(text, ALLOTMENT_CUES)


def extract_listing_date(text: str) -> Optional[dt.date]:
    return _date_near(text, LISTING_CUES)


def extract_resolution_date(text: str) -> Optional[dt.date]:
    return _date_near(text, RESOLUTION_CUES)


# Public alias so the triggers package does not import a private name.
date_near = _date_near


# --------------------------------------------------------------------------
# Investors and promoters
# --------------------------------------------------------------------------
@dataclasses.dataclass
class EntityMention:
    name: str
    kind: str                 # "investor" | "promoter"
    entity_type: str = "Unknown"
    is_marquee: bool = False
    discovered: bool = False
    confidence: float = 1.0
    context: str = ""


class InvestorMatcher:
    """Alias-driven matcher over the tracked investor registry."""

    def __init__(self, registry: Dict[str, Any]) -> None:
        self.entries: List[Dict[str, Any]] = list(registry.get("investors", []) or [])
        discovery = registry.get("discovery", {}) or {}
        self.suffixes: List[str] = [s.lower() for s in discovery.get("generic_suffixes", [])]
        self.stoplist: List[str] = [s.lower() for s in discovery.get("stoplist", [])]
        self._patterns: List[tuple[re.Pattern[str], Dict[str, Any]]] = []
        for entry in self.entries:
            aliases = {entry.get("name", ""), *(entry.get("aliases") or [])}
            for alias in sorted(filter(None, aliases), key=len, reverse=True):
                pattern = re.compile(rf"\b{re.escape(str(alias).lower())}\b", re.IGNORECASE)
                self._patterns.append((pattern, entry))

    def match_known(self, text: str) -> List[EntityMention]:
        seen: set[str] = set()
        mentions: List[EntityMention] = []
        for pattern, entry in self._patterns:
            match = pattern.search(text or "")
            if not match:
                continue
            name = entry.get("name", "")
            if name in seen:
                continue
            seen.add(name)
            mentions.append(
                EntityMention(
                    name=name,
                    kind="investor",
                    entity_type=entry.get("type", "Unknown"),
                    is_marquee=bool(entry.get("marquee")),
                    confidence=1.0,
                    context=window(text, match.start(), match.end()),
                )
            )
        return mentions

    def discover(self, text: str, nlp: Any = None) -> List[EntityMention]:
        """Harvest untracked institutional names.

        Uses spaCy ORG entities when a model is loaded, otherwise a capitalised
        n-gram scan. Both paths then require an institutional suffix, which is
        what keeps ordinary company names out of the investor table.
        """
        candidates: List[tuple[str, int, int]] = []
        if nlp is not None:
            try:
                doc = nlp(text[:100_000])
                candidates = [
                    (ent.text, ent.start_char, ent.end_char)
                    for ent in doc.ents
                    if ent.label_ in {"ORG", "PERSON"}
                ]
            except Exception as exc:  # pragma: no cover - model-dependent
                log.warning("spaCy NER failed, falling back", extra={"error": str(exc)})
        if not candidates:
            pattern = re.compile(
                r"\b(?:[A-Z][\w&.]*\s+){0,4}[A-Z][\w&.]*\s+"
                r"(?:Capital|Partners|Fund|Funds|Holdings|Investments|Ventures|"
                r"Advisors|Trust|LLP)\b"
            )
            candidates = [(m.group(0), m.start(), m.end()) for m in pattern.finditer(text or "")]

        mentions: List[EntityMention] = []
        seen: set[str] = set()
        for raw, start, end in candidates:
            name = re.sub(r"\s+", " ", raw).strip(" .,;:")
            low = name.lower()
            if len(name) < 4 or low in seen:
                continue
            if any(stop in low for stop in self.stoplist):
                continue
            if not any(low.endswith(suffix) or f" {suffix}" in low for suffix in self.suffixes):
                continue
            seen.add(low)
            mentions.append(
                EntityMention(
                    name=name,
                    kind="investor",
                    entity_type="Unknown",
                    discovered=True,
                    confidence=0.6,
                    context=window(text, start, end),
                )
            )
        return mentions


_PROMOTER_PATTERNS = (
    # "Mr. Rakesh Sharma, Promoter" / "Promoter, Mr. Rakesh Sharma"
    re.compile(
        r"\b(?:Mr\.?|Mrs\.?|Ms\.?|Shri|Smt\.?|Dr\.?)\s+"
        r"(?P<name>[A-Z][\w.]+(?:\s+[A-Z][\w.]+){0,3})"
        r"(?=[^.]{0,60}\bpromoter)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpromoter[s]?(?:\s+group)?[,:\s]+(?:Mr\.?|Mrs\.?|Ms\.?|Shri|Smt\.?|M/s\.?)\s+"
        r"(?P<name>[A-Z][\w.]+(?:\s+[A-Z][\w.]+){0,3})",
        re.IGNORECASE,
    ),
    # Corporate promoters: "XYZ Holdings Private Limited, one of the promoters"
    re.compile(
        r"(?P<name>[A-Z][\w&.]*(?:\s+[A-Z][\w&.]*){0,4}\s+"
        r"(?:Private\s+Limited|Limited|Holdings|Enterprises|LLP))"
        r"(?=[^.]{0,60}\bpromoter)"
    ),
)


def extract_promoters(text: str) -> List[EntityMention]:
    mentions: List[EntityMention] = []
    seen: set[str] = set()
    for pattern in _PROMOTER_PATTERNS:
        for match in pattern.finditer(text or ""):
            name = re.sub(r"\s+", " ", match.group("name")).strip(" .,;:")
            key = name.lower()
            if len(name) < 4 or key in seen:
                continue
            # "Promoter Group" itself is a category, not a person.
            if key in {"promoter", "promoters", "promoter group"}:
                continue
            seen.add(key)
            mentions.append(
                EntityMention(
                    name=name,
                    kind="promoter",
                    confidence=0.75,
                    context=window(text, match.start(), match.end()),
                )
            )
    return mentions


def promoter_is_involved(text: str) -> bool:
    """True when the filing concerns promoter shareholding, not just mentions it."""
    low = (text or "").lower()
    if "promoter" not in low:
        return False
    action_cues = (
        "sell",
        "sale",
        "sold",
        "dispose",
        "divest",
        "dilut",
        "transfer",
        "pledge",
        "encumbr",
        "invocation",
        "reduc",
        "offer for sale",
    )
    for sentence in split_sentences(low):
        if "promoter" in sentence and any(cue in sentence for cue in action_cues):
            return True
    return False


# --------------------------------------------------------------------------
# Aggregate
# --------------------------------------------------------------------------
@dataclasses.dataclass
class ExtractionResult:
    amount_inr: Optional[float] = None
    amount_raw: Optional[str] = None
    # True when the value was computed from shares x price rather than stated.
    amount_derived: bool = False
    num_shares: Optional[float] = None
    price_per_share: Optional[float] = None
    percent_of_equity: Optional[float] = None
    lock_in_expiry_date: Optional[dt.date] = None
    board_meeting_date: Optional[dt.date] = None
    record_date: Optional[dt.date] = None
    allotment_date: Optional[dt.date] = None
    listing_date: Optional[dt.date] = None
    resolution_date: Optional[dt.date] = None
    investors: List[EntityMention] = dataclasses.field(default_factory=list)
    promoters: List[EntityMention] = dataclasses.field(default_factory=list)
    promoter_involved: bool = False
    marquee_investor: bool = False

    def to_dict(self) -> Dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["investors"] = [m["name"] for m in payload["investors"]]
        payload["promoters"] = [m["name"] for m in payload["promoters"]]
        return payload


def extract_all(
    text: str,
    matcher: Optional[InvestorMatcher] = None,
    nlp: Any = None,
    discover_investors: bool = True,
) -> ExtractionResult:
    """Run every extractor over a filing's full text."""
    cleaned = clean_text(text)
    result = ExtractionResult()

    amount = best_deal_amount(cleaned)
    if amount:
        result.amount_inr = amount.value_inr
        result.amount_raw = amount.raw

    result.num_shares = extract_share_count(cleaned)
    result.price_per_share = extract_price_per_share(cleaned)

    # An OFS intimation typically states a floor price and a share count but no
    # aggregate; the implied value is what makes it comparable to other events.
    if result.amount_inr is None and result.num_shares and result.price_per_share:
        result.amount_inr = result.num_shares * result.price_per_share
        result.amount_raw = f"derived: {result.num_shares:,.0f} shares x {result.price_per_share:,.2f}"
        result.amount_derived = True
    result.percent_of_equity = extract_stake_percent(cleaned)
    result.lock_in_expiry_date = extract_lock_in_expiry(cleaned)
    result.board_meeting_date = extract_board_meeting_date(cleaned)
    result.record_date = extract_record_date(cleaned)
    result.allotment_date = extract_allotment_date(cleaned)
    result.listing_date = extract_listing_date(cleaned)
    result.resolution_date = extract_resolution_date(cleaned)

    if matcher is not None:
        known = matcher.match_known(cleaned)
        result.investors = list(known)
        if discover_investors:
            known_lower = {m.name.lower() for m in known}
            for mention in matcher.discover(cleaned, nlp=nlp):
                low = mention.name.lower()
                # "Blackstone Capital Partners" must not be filed as a second
                # investor alongside the registry's "Blackstone".
                if low in known_lower or any(name in low for name in known_lower):
                    continue
                result.investors.append(mention)
        result.marquee_investor = any(m.is_marquee for m in known)

    result.promoters = extract_promoters(cleaned)
    result.promoter_involved = promoter_is_involved(cleaned)
    return result
