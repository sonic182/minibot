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
    _ensure_supported_text_file(path, mime_type)
    return IndexableDocument(
        text=path.read_text(encoding="utf-8", errors="replace"),
        mime_type=mime_type or "text/plain",
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


def _ensure_supported_text_file(path: Path, mime_type: str | None) -> None:
    if mime_type and _is_supported_text_mime(mime_type):
        return

    sample = path.read_bytes()[:8192]
    if b"\x00" in sample:
        raise ValueError("Only UTF-8 text and PDF files are supported for indexing.")

    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Only UTF-8 text and PDF files are supported for indexing.") from exc


def _is_supported_text_mime(mime_type: str) -> bool:
    normalized = mime_type.lower()
    return normalized.startswith("text/") or normalized in {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/x-javascript",
        "image/svg+xml",
    }


def _load_pdf_reader_class():
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required for PDF indexing. Run `poetry install --all-extras`.") from exc
    return PdfReader
