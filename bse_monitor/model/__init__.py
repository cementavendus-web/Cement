"""Layer 3: labels, panel and the sell-down model."""

from .ingest import IngestStats, ingest_deals, ingest_quotes
from .labels import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_PCT_THRESHOLD,
    DEFAULT_VALUE_THRESHOLD_INR,
    LabelResult,
    LabelSpec,
    coverage_report,
    dedupe_disposals,
    label_for_window,
    label_row,
)

__all__ = [
    "IngestStats",
    "ingest_deals",
    "ingest_quotes",
    "LabelSpec",
    "LabelResult",
    "label_for_window",
    "label_row",
    "dedupe_disposals",
    "coverage_report",
    "DEFAULT_HORIZON_DAYS",
    "DEFAULT_PCT_THRESHOLD",
    "DEFAULT_VALUE_THRESHOLD_INR",
]
