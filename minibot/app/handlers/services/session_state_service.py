from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecentFileRef:
    operation: str
    path_absolute: str
    path_relative: str | None
    path_scope: str


class SessionStateService:
    def __init__(self) -> None:
        self.session_total_tokens: dict[str, int] = {}
        self.session_previous_response_ids: dict[str, str] = {}
        self.session_previous_response_prompt_fingerprints: dict[str, str] = {}
        self.session_latest_input_tokens: dict[str, int] = {}
        self.session_latest_output_tokens: dict[str, int] = {}
        self.session_latest_total_tokens: dict[str, int] = {}
        self.session_latest_cached_input_tokens: dict[str, int] = {}
        self.session_latest_reasoning_output_tokens: dict[str, int] = {}
        self.session_latest_provider_tool_calls: dict[str, int] = {}
        self.session_recent_files: dict[str, list[RecentFileRef]] = {}

    def track_tokens(self, session_id: str, tokens: int | None) -> int:
        if tokens is None or tokens <= 0:
            return 0
        self.session_total_tokens[session_id] = self.session_total_tokens.get(session_id, 0) + tokens
        return tokens

    def current_tokens(self, session_id: str) -> int:
        return self.session_total_tokens.get(session_id, 0)

    def set_previous_response_id(
        self,
        session_id: str,
        response_id: str | None,
        *,
        system_prompt: str | None = None,
    ) -> None:
        if response_id:
            self.session_previous_response_ids[session_id] = response_id
            if system_prompt is not None:
                fingerprint = self._prompt_fingerprint(system_prompt)
                self.session_previous_response_prompt_fingerprints[session_id] = fingerprint
            else:
                self.session_previous_response_prompt_fingerprints.pop(session_id, None)

    def get_previous_response_id(self, session_id: str, *, system_prompt: str | None = None) -> str | None:
        response_id = self.session_previous_response_ids.get(session_id)
        if response_id is None:
            return None
        expected_fingerprint = self.session_previous_response_prompt_fingerprints.get(session_id)
        if system_prompt is None or expected_fingerprint is None:
            return response_id
        if expected_fingerprint == self._prompt_fingerprint(system_prompt):
            return response_id
        self.clear_previous_response_id(session_id)
        return None

    def clear_previous_response_id(self, session_id: str) -> None:
        self.session_previous_response_ids.pop(session_id, None)
        self.session_previous_response_prompt_fingerprints.pop(session_id, None)

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
        provider_tool_calls: int | None = None,
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
        if provider_tool_calls is not None and provider_tool_calls >= 0:
            self.session_latest_provider_tool_calls[session_id] = provider_tool_calls

    def latest_input_tokens(self, session_id: str) -> int | None:
        return self.session_latest_input_tokens.get(session_id)

    def latest_usage_trace(self, session_id: str) -> dict[str, int | None]:
        return {
            "input_tokens": self.session_latest_input_tokens.get(session_id),
            "output_tokens": self.session_latest_output_tokens.get(session_id),
            "total_tokens": self.session_latest_total_tokens.get(session_id),
            "cached_input_tokens": self.session_latest_cached_input_tokens.get(session_id),
            "reasoning_output_tokens": self.session_latest_reasoning_output_tokens.get(session_id),
            "provider_tool_calls": self.session_latest_provider_tool_calls.get(session_id),
        }

    def track_recent_file(self, session_id: str, ref: RecentFileRef, *, max_entries: int = 10) -> None:
        current = list(self.session_recent_files.get(session_id, []))
        deduped = [item for item in current if item.path_absolute != ref.path_absolute]
        deduped.append(ref)
        if len(deduped) > max_entries:
            deduped = deduped[-max_entries:]
        self.session_recent_files[session_id] = deduped

    def recent_files(self, session_id: str, *, limit: int = 5) -> list[RecentFileRef]:
        items = list(self.session_recent_files.get(session_id, []))
        if limit <= 0:
            return []
        return list(reversed(items[-limit:]))

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

    @staticmethod
    def _prompt_fingerprint(system_prompt: str) -> str:
        return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
