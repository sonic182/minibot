from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))


@pytest.fixture
def numeric_tokenizer(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    tokenizer = MagicMock()

    def encode(text: str, *, add_special_tokens: bool) -> list[int]:
        assert add_special_tokens is False
        return [int(token) for token in text.split()]

    def tokenize(
        text: str,
        *,
        add_special_tokens: bool,
        truncation: bool,
        return_attention_mask: bool,
        return_token_type_ids: bool,
        verbose: bool,
    ) -> dict[str, list[int]]:
        assert add_special_tokens is False
        assert truncation is False
        assert return_attention_mask is False
        assert return_token_type_ids is False
        assert verbose is False
        return {"input_ids": encode(text, add_special_tokens=False)}

    def decode(
        token_ids: list[int],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        assert skip_special_tokens is True
        assert clean_up_tokenization_spaces is False
        return " ".join(str(token_id) for token_id in token_ids)

    tokenizer.side_effect = tokenize
    tokenizer.encode.side_effect = encode
    tokenizer.decode.side_effect = decode
    monkeypatch.setattr("minibot.rag.chunking.get_tokenizer", lambda _model, _truncate_dim: tokenizer)
    return tokenizer
