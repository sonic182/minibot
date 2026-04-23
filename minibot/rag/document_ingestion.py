from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IndexableDocument:
    text: str
    mime_type: str
    source_type: str = "file"


def extract_indexable_document(path: Path) -> IndexableDocument:
    suffix = path.suffix.lower()
    mime_type, _ = mimetypes.guess_type(str(path), strict=False)
    if suffix == ".pdf" or mime_type == "application/pdf":
        return IndexableDocument(
            text=_extract_pdf_text(path),
            mime_type="application/pdf",
        )
    return IndexableDocument(
        text=path.read_text(encoding="utf-8", errors="replace"),
        mime_type="text/plain",
    )


def _extract_pdf_text(path: Path) -> str:
    reader = _load_pdf_reader_class()(str(path))
    parts: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = _normalize_pdf_page_text(page.extract_text() or "")
        if text:
            parts.append(f"[PAGE {page_number}]\n{text}")
    if not parts:
        raise ValueError("PDF has no extractable text. OCR is not supported in v1.")
    return "\n\n".join(parts)


def _normalize_pdf_page_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def _load_pdf_reader_class():
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required for PDF indexing. Run `poetry install --all-extras`.") from exc
    return PdfReader
