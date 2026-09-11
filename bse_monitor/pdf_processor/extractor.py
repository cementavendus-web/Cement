"""PDF text and table extraction with a three-stage fallback chain.

1. **pdfplumber** — best layout fidelity and the only one of the three that
   also gives usable table geometry.
2. **PyMuPDF (fitz)** — much faster, and recovers text from files whose
   structure pdfplumber chokes on.
3. **Tesseract OCR** — for scanned filings with no text layer at all, which is
   a meaningful minority of BSE attachments (signed board resolutions are
   routinely scanned).

The chain escalates on *quality*, not just on exceptions: if the text layer
yields fewer than ``ocr_trigger_chars_per_page`` characters per page the
document is treated as scanned and sent to OCR even though extraction
"succeeded".

Every engine is an optional import so the module loads — and reports honestly —
on a machine where none of them are installed.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

try:  # pragma: no cover - optional
    import pdfplumber

    PDFPLUMBER_AVAILABLE = True
except ImportError:  # pragma: no cover
    PDFPLUMBER_AVAILABLE = False

try:  # pragma: no cover - optional
    import fitz  # PyMuPDF

    PYMUPDF_AVAILABLE = True
except ImportError:  # pragma: no cover
    PYMUPDF_AVAILABLE = False

try:  # pragma: no cover - optional
    import pytesseract
    from PIL import Image

    TESSERACT_AVAILABLE = True
except ImportError:  # pragma: no cover
    TESSERACT_AVAILABLE = False


@dataclasses.dataclass
class ExtractionOutput:
    text: str = ""
    method: str = "none"
    page_count: int = 0
    ocr_used: bool = False
    tables: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    error: Optional[str] = None

    @property
    def char_count(self) -> int:
        return len(self.text)


class PdfExtractor:
    def __init__(
        self,
        max_pages_text: int = 60,
        ocr_enabled: bool = True,
        ocr_language: str = "eng",
        ocr_dpi: int = 200,
        ocr_max_pages: int = 12,
        ocr_trigger_chars_per_page: int = 120,
        extract_tables: bool = True,
    ) -> None:
        self.max_pages_text = max_pages_text
        self.ocr_enabled = ocr_enabled
        self.ocr_language = ocr_language
        self.ocr_dpi = ocr_dpi
        self.ocr_max_pages = ocr_max_pages
        self.ocr_trigger = ocr_trigger_chars_per_page
        self.extract_tables = extract_tables

    # -- engines -----------------------------------------------------------
    def _with_pdfplumber(self, path: Path) -> ExtractionOutput:
        if not PDFPLUMBER_AVAILABLE:
            return ExtractionOutput(method="none", error="pdfplumber not installed")
        try:
            chunks: List[str] = []
            tables: List[Dict[str, Any]] = []
            with pdfplumber.open(str(path)) as pdf:
                total_pages = len(pdf.pages)
                for index, page in enumerate(pdf.pages[: self.max_pages_text]):
                    chunks.append(page.extract_text() or "")
                    if self.extract_tables:
                        for table in page.extract_tables() or []:
                            rows = [
                                [(cell or "").strip() for cell in row]
                                for row in table
                                if any(cell for cell in row)
                            ]
                            if len(rows) >= 2:
                                tables.append({"page": index + 1, "rows": rows})
            return ExtractionOutput(
                text="\n".join(chunks).strip(),
                method="pdfplumber",
                page_count=total_pages,
                tables=tables,
            )
        except Exception as exc:
            log.warning("pdfplumber failed", extra={"path": str(path), "error": str(exc)})
            return ExtractionOutput(method="none", error=f"pdfplumber: {exc}")

    def _with_pymupdf(self, path: Path) -> ExtractionOutput:
        if not PYMUPDF_AVAILABLE:
            return ExtractionOutput(method="none", error="pymupdf not installed")
        try:
            document = fitz.open(str(path))
            chunks = [
                document.load_page(i).get_text("text")
                for i in range(min(document.page_count, self.max_pages_text))
            ]
            page_count = document.page_count
            document.close()
            return ExtractionOutput(
                text="\n".join(chunks).strip(), method="pymupdf", page_count=page_count
            )
        except Exception as exc:
            log.warning("pymupdf failed", extra={"path": str(path), "error": str(exc)})
            return ExtractionOutput(method="none", error=f"pymupdf: {exc}")

    def _with_ocr(self, path: Path) -> ExtractionOutput:
        """Rasterise via PyMuPDF, then Tesseract each page image."""
        if not (TESSERACT_AVAILABLE and PYMUPDF_AVAILABLE):
            missing = "pytesseract/Pillow" if PYMUPDF_AVAILABLE else "PyMuPDF"
            return ExtractionOutput(method="none", error=f"OCR unavailable ({missing} missing)")
        try:
            import io

            document = fitz.open(str(path))
            zoom = self.ocr_dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            chunks: List[str] = []
            for index in range(min(document.page_count, self.ocr_max_pages)):
                pixmap = document.load_page(index).get_pixmap(matrix=matrix)
                image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                chunks.append(pytesseract.image_to_string(image, lang=self.ocr_language))
            page_count = document.page_count
            document.close()
            return ExtractionOutput(
                text="\n".join(chunks).strip(),
                method="ocr",
                page_count=page_count,
                ocr_used=True,
            )
        except Exception as exc:
            log.warning("OCR failed", extra={"path": str(path), "error": str(exc)})
            return ExtractionOutput(method="none", error=f"ocr: {exc}")

    # -- orchestration -----------------------------------------------------
    def _is_thin(self, output: ExtractionOutput) -> bool:
        """True when the text layer is too sparse to be a real one."""
        pages = max(1, min(output.page_count or 1, self.max_pages_text))
        return (output.char_count / pages) < self.ocr_trigger

    def extract(self, path: str | Path) -> ExtractionOutput:
        pdf_path = Path(path)
        if not pdf_path.exists():
            return ExtractionOutput(method="none", error="file not found")

        best = self._with_pdfplumber(pdf_path)

        if not best.text or self._is_thin(best):
            alternative = self._with_pymupdf(pdf_path)
            if alternative.char_count > best.char_count:
                # Keep pdfplumber's tables; only the text was inferior.
                alternative.tables = best.tables
                best = alternative

        if self.ocr_enabled and (not best.text or self._is_thin(best)):
            log.info(
                "Text layer thin, escalating to OCR",
                extra={"path": str(pdf_path), "chars": best.char_count, "pages": best.page_count},
            )
            ocr_output = self._with_ocr(pdf_path)
            if ocr_output.char_count > best.char_count:
                ocr_output.tables = best.tables
                ocr_output.page_count = ocr_output.page_count or best.page_count
                best = ocr_output

        if not best.text and not best.error:
            best.error = "no text recoverable"
        log.info(
            "PDF extracted",
            extra={
                "path": str(pdf_path),
                "method": best.method,
                "chars": best.char_count,
                "pages": best.page_count,
                "ocr": best.ocr_used,
                "tables": len(best.tables),
            },
        )
        return best


def extractor_from_config(config: Any) -> PdfExtractor:
    cfg = config.section("pdf")
    return PdfExtractor(
        max_pages_text=int(cfg.get("max_pages_text", 60)),
        ocr_enabled=bool(cfg.get("ocr_enabled", True)),
        ocr_language=str(cfg.get("ocr_language", "eng")),
        ocr_dpi=int(cfg.get("ocr_dpi", 200)),
        ocr_max_pages=int(cfg.get("ocr_max_pages", 12)),
        ocr_trigger_chars_per_page=int(cfg.get("ocr_trigger_chars_per_page", 120)),
        extract_tables=bool(cfg.get("extract_tables", True)),
    )
