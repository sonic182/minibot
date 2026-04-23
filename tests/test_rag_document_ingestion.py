from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from minibot.rag.document_ingestion import extract_indexable_document


def test_extract_indexable_document_reads_plain_text_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello world", encoding="utf-8")

    document = extract_indexable_document(path)

    assert document.text == "hello world"
    assert document.mime_type == "text/plain"
    assert document.source_type == "file"


def test_extract_indexable_document_reads_utf8_text_without_known_text_mime(tmp_path: Path) -> None:
    path = tmp_path / "README"
    path.write_text("hello world", encoding="utf-8")

    document = extract_indexable_document(path)

    assert document.text == "hello world"
    assert document.mime_type == "text/plain"


def test_extract_indexable_document_extracts_pdf_text_with_page_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4")

    class _FakeReader:
        def __init__(self, _path: str) -> None:
            self.pages = [
                SimpleNamespace(extract_text=lambda: "First page\r\nLine two  "),
                SimpleNamespace(extract_text=lambda: ""),
                SimpleNamespace(extract_text=lambda: "Third page"),
            ]

    monkeypatch.setattr("minibot.rag.document_ingestion._load_pdf_reader_class", lambda: _FakeReader)

    document = extract_indexable_document(path)

    assert document.mime_type == "application/pdf"
    assert document.text == "[PAGE 1]\nFirst page\nLine two\n\n[PAGE 3]\nThird page"


def test_extract_indexable_document_fails_when_pdf_has_no_extractable_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4")

    class _FakeReader:
        def __init__(self, _path: str) -> None:
            self.pages = [
                SimpleNamespace(extract_text=lambda: ""),
                SimpleNamespace(extract_text=lambda: None),
            ]

    monkeypatch.setattr("minibot.rag.document_ingestion._load_pdf_reader_class", lambda: _FakeReader)

    with pytest.raises(ValueError, match="OCR is not supported in v1"):
        extract_indexable_document(path)


def test_extract_indexable_document_rejects_binary_files(tmp_path: Path) -> None:
    path = tmp_path / "archive.zip"
    path.write_bytes(b"PK\x03\x04\x00binary")

    with pytest.raises(ValueError, match="Only UTF-8 text and PDF files are supported"):
        extract_indexable_document(path)
