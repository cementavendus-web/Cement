"""Text normalisation shared by the classifier and the entity extractor."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Iterable, List

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
# Ligatures and smart punctuation arrive from PDF text layers and break literal
# keyword matching if left alone.
_TRANSLATIONS = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "­": "",
}


def clean_text(raw: str) -> str:
    """Strip markup and normalise whitespace/punctuation, preserving line breaks."""
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = _TAG_RE.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    for source, target in _TRANSLATIONS.items():
        text = text.replace(source, target)
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def normalize_for_match(raw: str) -> str:
    """Lower-cased, punctuation-flattened form used for keyword lookups."""
    text = clean_text(raw).lower()
    # Hyphens and slashes are inconsistent across filings ("sell-down"/"sell down").
    text = re.sub(r"[\-/]", " ", text)
    text = re.sub(r"[^\w\s%₹.,()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_boilerplate(text: str, phrases: Iterable[str]) -> str:
    """Drop sentences containing regulatory boilerplate before scoring."""
    if not text:
        return ""
    lowered_phrases = [p.lower() for p in phrases if p]
    kept: List[str] = []
    for sentence in split_sentences(text):
        low = sentence.lower()
        if any(phrase in low for phrase in lowered_phrases):
            continue
        kept.append(sentence)
    return " ".join(kept) if kept else text


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(₹])|\n+")


def split_sentences(text: str) -> List[str]:
    if not text:
        return []
    parts = _SENT_SPLIT_RE.split(text)
    return [part.strip() for part in parts if part and part.strip()]


def window(text: str, start: int, end: int, radius: int = 160) -> str:
    """Context slice around a match, for storing alongside an extracted entity."""
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi].strip()


def sentence_spans(text: str) -> List[tuple[int, int, str]]:
    """Sentences with their character offsets, so a match can be mapped back."""
    if not text:
        return []
    spans: List[tuple[int, int, str]] = []
    cursor = 0
    for part in _SENT_SPLIT_RE.split(text):
        if part is None:
            continue
        stripped = part.strip()
        if not stripped:
            continue
        start = text.find(stripped, cursor)
        if start < 0:
            start = cursor
        end = start + len(stripped)
        spans.append((start, end, stripped))
        cursor = end
    return spans


def sentence_at(spans: List[tuple[int, int, str]], offset: int) -> str:
    """The sentence containing ``offset`` (empty string when out of range)."""
    for start, end, body in spans:
        if start <= offset < end:
            return body
    return ""
