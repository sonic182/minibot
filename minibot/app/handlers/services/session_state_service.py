from __future__ import annotations

from typing import Any


class SessionStateService:
    def __init__(self) -> None:
        self.session_total_tokens: dict[str, int] = {}
        self.session_previous_response_ids: dict[str, str] = {}

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
