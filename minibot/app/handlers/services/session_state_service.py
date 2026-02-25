from __future__ import annotations

from typing import Any


class SessionStateService:
    def __init__(self) -> None:
        self.session_total_tokens: dict[str, int] = {}
        self.session_previous_response_ids: dict[str, str] = {}
        self.session_latest_input_tokens: dict[str, int] = {}
        self.session_latest_output_tokens: dict[str, int] = {}
        self.session_latest_total_tokens: dict[str, int] = {}
        self.session_latest_cached_input_tokens: dict[str, int] = {}
        self.session_latest_reasoning_output_tokens: dict[str, int] = {}

    def track_tokens(self, session_id: str, tokens: int | None) -> int:
        if tokens is None or tokens <= 0:
            return 0
        self.session_total_tokens[session_id] = self.session_total_tokens.get(session_id, 0) + tokens
        return tokens

    def current_tokens(self, session_id: str) -> int:
        return self.session_total_tokens.get(session_id, 0)

    def set_previous_response_id(self, session_id: str, response_id: str | None) -> None:
        if response_id:
            self.session_previous_response_ids[session_id] = response_id

    def get_previous_response_id(self, session_id: str) -> str | None:
        return self.session_previous_response_ids.get(session_id)

    def clear_previous_response_id(self, session_id: str) -> None:
        self.session_previous_response_ids.pop(session_id, None)

    def has_previous_response_id(self, session_id: str) -> bool:
        return session_id in self.session_previous_response_ids

    def track_usage(
        self,
        session_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        cached_input_tokens: int | None,
        reasoning_output_tokens: int | None,
    ) -> None:
        if input_tokens is not None and input_tokens >= 0:
            self.session_latest_input_tokens[session_id] = input_tokens
        if output_tokens is not None and output_tokens >= 0:
            self.session_latest_output_tokens[session_id] = output_tokens
        if total_tokens is not None and total_tokens >= 0:
            self.session_latest_total_tokens[session_id] = total_tokens
        if cached_input_tokens is not None and cached_input_tokens >= 0:
            self.session_latest_cached_input_tokens[session_id] = cached_input_tokens
        if reasoning_output_tokens is not None and reasoning_output_tokens >= 0:
            self.session_latest_reasoning_output_tokens[session_id] = reasoning_output_tokens

    def latest_input_tokens(self, session_id: str) -> int | None:
        return self.session_latest_input_tokens.get(session_id)

    def latest_usage_trace(self, session_id: str) -> dict[str, int | None]:
        return {
            "input_tokens": self.session_latest_input_tokens.get(session_id),
            "output_tokens": self.session_latest_output_tokens.get(session_id),
            "total_tokens": self.session_latest_total_tokens.get(session_id),
            "cached_input_tokens": self.session_latest_cached_input_tokens.get(session_id),
            "reasoning_output_tokens": self.session_latest_reasoning_output_tokens.get(session_id),
        }

    @staticmethod
    def build_token_trace(
        *,
        turn_total_tokens: int,
        session_total_tokens_before_compaction: int | None,
        session_total_tokens_after_compaction: int,
        compaction_performed: bool,
    ) -> dict[str, Any]:
        return {
            "turn_total_tokens": max(0, int(turn_total_tokens)),
            "session_total_tokens": max(0, int(session_total_tokens_after_compaction)),
            "session_total_tokens_before_compaction": session_total_tokens_before_compaction,
            "session_total_tokens_after_compaction": max(0, int(session_total_tokens_after_compaction)),
            "compaction_performed": compaction_performed,
            "accounting_scope": "all_turn_calls",
        }
