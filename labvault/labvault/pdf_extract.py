"""PDF text extraction with OCR fallback.

Uses PyMuPDF for the text layer. Pages that come back essentially empty are
assumed to be scanned/photographed and are rendered to an image and run
through Tesseract (if installed). Password-protected PDFs raise a clear error
rather than silently producing nothing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError as e:  # pragma: no cover
    raise ImportError("PyMuPDF is required: pip install pymupdf") from e


# A page with fewer than this many characters of real text is treated as scanned.
_MIN_CHARS_FOR_TEXT_PAGE = 25


class PdfError(RuntimeError):
    pass


class PdfEncryptedError(PdfError):
    pass


@dataclass
class ExtractedPdf:
    text: str
    page_count: int
    used_ocr: bool
    ocr_pages: list[int] = field(default_factory=list)


def file_sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _ocr_page(page: "fitz.Page") -> str:
    import io

    import pytesseract
    from PIL import Image

    # 300 DPI render is a good balance for lab-report OCR.
    pix = page.get_pixmap(dpi=300)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img)


def extract_pdf(path: Path | str, *, allow_ocr: bool = True) -> ExtractedPdf:
    path = Path(path)
    if not path.exists():
        raise PdfError(f"File not found: {path}")
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise PdfError(f"Could not open PDF: {e}") from e

    if doc.needs_pass:
        raise PdfEncryptedError(
            f"{path.name} is password-protected. Remove the password (e.g. in Preview: "
            "File > Export as PDF without encryption) and try again."
        )

    parts: list[str] = []
    ocr_pages: list[int] = []
    ocr_ready = allow_ocr and _ocr_available()

    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        if len(text) < _MIN_CHARS_FOR_TEXT_PAGE and ocr_ready:
            try:
                ocr_text = _ocr_page(page).strip()
                if len(ocr_text) > len(text):
                    text = ocr_text
                    ocr_pages.append(i + 1)
            except Exception:
                # OCR failed for this page; keep whatever text layer we had.
                pass
        parts.append(f"--- page {i + 1} ---\n{text}")

    page_count = doc.page_count
    doc.close()

    full = "\n\n".join(parts).strip()
    if not full or all(len(p.split("\n", 1)[-1].strip()) == 0 for p in parts):
        hint = (
            "" if ocr_ready else " No text layer was found and OCR is unavailable "
            "(install with: pip install 'labvault[ocr]' and `brew install tesseract`)."
        )
        raise PdfError(f"No extractable text in {path.name}.{hint}")

    return ExtractedPdf(text=full, page_count=page_count, used_ocr=bool(ocr_pages), ocr_pages=ocr_pages)
