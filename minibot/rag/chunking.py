from __future__ import annotations

from typing import Any

from minibot.rag.embeddings import get_tokenizer


def chunk_text(
    text: str,
    *,
    chunk_size_tokens: int = 96,
    overlap_tokens: int = 20,
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2",
    truncate_dim: int | None = None,
) -> list[str]:
    text = text.strip()
    if not text:
        return []

    _validate_token_window(chunk_size_tokens=chunk_size_tokens, overlap_tokens=overlap_tokens)
    tokenizer = get_tokenizer(embedding_model, truncate_dim)
    token_ids = _encode_token_ids(tokenizer, text)
    if not token_ids:
        return []

    chunks: list[str] = []
    step = chunk_size_tokens - overlap_tokens
    start = 0
    token_count = len(token_ids)

    while start < token_count:
        end = min(start + chunk_size_tokens, token_count)
        chunk = _decode_token_ids(tokenizer, token_ids[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= token_count:
            break
        start += step

    return chunks


def count_text_tokens(
    text: str,
    *,
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2",
    truncate_dim: int | None = None,
) -> int:
    tokenizer = get_tokenizer(embedding_model, truncate_dim)
    return len(_encode_token_ids(tokenizer, text))


def truncate_text_tokens(
    text: str,
    *,
    max_tokens: int,
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2",
    truncate_dim: int | None = None,
) -> tuple[str, int]:
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")

    tokenizer = get_tokenizer(embedding_model, truncate_dim)
    token_ids = _encode_token_ids(tokenizer, text)
    if len(token_ids) <= max_tokens:
        return text, 0

    omitted = len(token_ids) - max_tokens
    return _decode_token_ids(tokenizer, token_ids[:max_tokens]).strip(), omitted


def _validate_token_window(*, chunk_size_tokens: int, overlap_tokens: int) -> None:
    if chunk_size_tokens < 1:
        raise ValueError("chunk_size_tokens must be >= 1")
    if overlap_tokens < 0:
        raise ValueError("chunk_overlap_tokens must be >= 0")
    if overlap_tokens >= chunk_size_tokens:
        raise ValueError("chunk_overlap_tokens must be less than chunk_size_tokens")


def _encode_token_ids(tokenizer: Any, text: str) -> list[int]:
    try:
        encoded = tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            return_attention_mask=False,
            return_token_type_ids=False,
            verbose=False,
        )
        return list(encoded["input_ids"])
    except TypeError:
        return list(tokenizer.encode(text, add_special_tokens=False))


def _decode_token_ids(tokenizer: Any, token_ids: list[int]) -> str:
    try:
        return str(
            tokenizer.decode(
                token_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
        )
    except TypeError:
        return str(tokenizer.decode(token_ids, skip_special_tokens=True))
