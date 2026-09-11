"""PDF download, extraction fallback chain and table interpretation."""

from __future__ import annotations

import hashlib

import pytest

from bse_monitor.pdf_processor.downloader import PdfDownloader
from bse_monitor.pdf_processor.extractor import ExtractionOutput, PdfExtractor
from bse_monitor.pdf_processor.tables import parse_table, parse_tables, summarise
from bse_monitor.scraper.base import ScraperError

PDF_BYTES = b"%PDF-1.4\n" + b"x" * 512


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def iter_content(self, chunk_size: int = 1024):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    def close(self) -> None:
        pass


class FakeClient:
    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def request(self, _method: str, url: str, **_kwargs):
        self.calls.append(url)
        value = self.responses.get(url)
        if value is None:
            raise ScraperError(f"404 {url}")
        return FakeResponse(value)


def test_download_writes_content_addressed_file(tmp_path) -> None:
    client = FakeClient({"https://x/a.pdf": PDF_BYTES})
    result = PdfDownloader(client, tmp_path).download(["https://x/a.pdf"])
    assert result.ok
    assert result.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert result.path.exists() and result.path.name.endswith(".pdf")


def test_download_falls_back_to_the_historical_url(tmp_path) -> None:
    """BSE moves attachments out of AttachLive once they age."""
    client = FakeClient({"https://x/hist/a.pdf": PDF_BYTES})
    result = PdfDownloader(client, tmp_path).download(
        ["https://x/live/a.pdf", "https://x/hist/a.pdf"]
    )
    assert result.ok
    assert client.calls == ["https://x/live/a.pdf", "https://x/hist/a.pdf"]


def test_html_error_page_is_rejected(tmp_path) -> None:
    """A 200 carrying an HTML error page must not be stored as a PDF."""
    client = FakeClient({"https://x/a.pdf": b"<html>Not found</html>"})
    result = PdfDownloader(client, tmp_path).download(["https://x/a.pdf"])
    assert not result.ok and "not a PDF" in (result.error or "")


def test_oversized_download_is_skipped(tmp_path) -> None:
    client = FakeClient({"https://x/a.pdf": PDF_BYTES})
    result = PdfDownloader(client, tmp_path, max_bytes=16).download(["https://x/a.pdf"])
    assert result.status == "SKIPPED"


def test_identical_content_reuses_the_same_path(tmp_path) -> None:
    downloader = PdfDownloader(FakeClient({"https://x/a.pdf": PDF_BYTES, "https://x/b.pdf": PDF_BYTES}), tmp_path)
    first = downloader.download(["https://x/a.pdf"])
    second = downloader.download(["https://x/b.pdf"])
    assert first.path == second.path and second.from_cache is True


def test_download_failure_is_reported_not_raised(tmp_path) -> None:
    result = PdfDownloader(FakeClient({}), tmp_path).download(["https://x/missing.pdf"])
    assert result.status == "FAILED" and result.error


def test_missing_file_returns_error() -> None:
    assert PdfExtractor().extract("/nonexistent/file.pdf").error == "file not found"


def test_thin_text_layer_triggers_ocr_escalation(tmp_path, monkeypatch) -> None:
    """A scanned filing has pages but almost no characters; OCR must take over."""
    target = tmp_path / "scan.pdf"
    target.write_bytes(PDF_BYTES)
    extractor = PdfExtractor(ocr_enabled=True, ocr_trigger_chars_per_page=100)

    monkeypatch.setattr(
        extractor, "_with_pdfplumber",
        lambda _p: ExtractionOutput(text="sig", method="pdfplumber", page_count=4),
    )
    monkeypatch.setattr(
        extractor, "_with_pymupdf", lambda _p: ExtractionOutput(text="", method="none")
    )
    monkeypatch.setattr(
        extractor, "_with_ocr",
        lambda _p: ExtractionOutput(text="x" * 900, method="ocr", page_count=4, ocr_used=True),
    )
    output = extractor.extract(target)
    assert output.method == "ocr" and output.ocr_used is True


def test_ocr_is_not_used_when_the_text_layer_is_good(tmp_path, monkeypatch) -> None:
    target = tmp_path / "born_digital.pdf"
    target.write_bytes(PDF_BYTES)
    extractor = PdfExtractor(ocr_enabled=True, ocr_trigger_chars_per_page=100)
    monkeypatch.setattr(
        extractor, "_with_pdfplumber",
        lambda _p: ExtractionOutput(text="y" * 900, method="pdfplumber", page_count=2),
    )
    monkeypatch.setattr(
        extractor, "_with_ocr",
        lambda _p: pytest.fail("OCR must not run on a healthy text layer"),
    )
    assert extractor.extract(target).method == "pdfplumber"


def test_pymupdf_rescues_a_pdfplumber_failure(tmp_path, monkeypatch) -> None:
    target = tmp_path / "odd.pdf"
    target.write_bytes(PDF_BYTES)
    extractor = PdfExtractor(ocr_enabled=False)
    monkeypatch.setattr(
        extractor, "_with_pdfplumber",
        lambda _p: ExtractionOutput(text="", method="none", error="pdfplumber: boom"),
    )
    monkeypatch.setattr(
        extractor, "_with_pymupdf",
        lambda _p: ExtractionOutput(text="z" * 800, method="pymupdf", page_count=2),
    )
    assert extractor.extract(target).method == "pymupdf"


def test_tables_from_pdfplumber_are_preserved_through_escalation(tmp_path, monkeypatch) -> None:
    target = tmp_path / "mixed.pdf"
    target.write_bytes(PDF_BYTES)
    extractor = PdfExtractor(ocr_enabled=True, ocr_trigger_chars_per_page=100)
    tables = [{"page": 1, "rows": [["Name", "Shares"], ["A", "10"]]}]
    monkeypatch.setattr(
        extractor, "_with_pdfplumber",
        lambda _p: ExtractionOutput(text="tiny", method="pdfplumber", page_count=3, tables=tables),
    )
    monkeypatch.setattr(extractor, "_with_pymupdf", lambda _p: ExtractionOutput(method="none"))
    monkeypatch.setattr(
        extractor, "_with_ocr",
        lambda _p: ExtractionOutput(text="q" * 900, method="ocr", page_count=3, ocr_used=True),
    )
    output = extractor.extract(target)
    assert output.method == "ocr" and output.tables == tables


# -- table interpretation --------------------------------------------------
ALLOTMENT_TABLE = {
    "page": 1,
    "rows": [
        ["Name of the Allottee", "No. of Shares", "Issue Price (Rs.)", "% of post-issue capital"],
        ["Blackstone Capital Partners", "1,20,00,000", "745.50", "4.2"],
        ["GIC Private Limited", "80,00,000", "745.50", "2.8"],
        ["Total", "2,00,00,000", "", "7.0"],
    ],
}


def test_allotment_table_parsing() -> None:
    rows = parse_table(ALLOTMENT_TABLE)
    assert len(rows) == 2                               # the Total row is dropped
    assert rows[0].shares == pytest.approx(12_000_000)
    assert rows[0].price == pytest.approx(745.5)


def test_layout_noise_is_ignored() -> None:
    assert parse_table({"rows": [["a", "b"], ["c", "d"]]}) == []
    assert parse_table({"rows": [["Name"]]}) == []


def test_summarise_aggregates() -> None:
    stats = summarise(parse_tables([ALLOTMENT_TABLE]))
    assert stats["total_shares"] == pytest.approx(20_000_000)
    assert stats["max_percent"] == pytest.approx(4.2)
