from __future__ import annotations

from unittest.mock import MagicMock

from minibot.rag import chunking


def test_chunk_text_splits_by_token_count_with_overlap(numeric_tokenizer: MagicMock) -> None:
    chunks = chunking.chunk_text(
        "1 2 3 4 5 6 7",
        chunk_size_tokens=3,
        overlap_tokens=1,
        embedding_model="mini",
    )

    assert chunks == ["1 2 3", "3 4 5", "5 6 7"]
    assert numeric_tokenizer.call_count == 1
    assert numeric_tokenizer.decode.call_count == 3


def test_chunk_text_returns_empty_for_blank_text(numeric_tokenizer: MagicMock) -> None:
    assert chunking.chunk_text("   ", embedding_model="mini") == []
    numeric_tokenizer.assert_not_called()


def test_truncate_text_tokens_returns_omitted_token_count(numeric_tokenizer: MagicMock) -> None:
    text, omitted = chunking.truncate_text_tokens("1 2 3 4", max_tokens=2, embedding_model="mini")

    assert text == "1 2"
    assert omitted == 2
    numeric_tokenizer.decode.assert_called_once()
