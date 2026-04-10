from __future__ import annotations

from pathlib import Path
from typing import Any

from minibot.app.handlers.services.session_state_service import RecentFileRef, SessionStateService
from minibot.core.agent_runtime import AgentState


class RecentFileTrackingService:
    def __init__(
        self,
        *,
        session_state: SessionStateService,
        managed_files_root: str | None = None,
    ) -> None:
        self._session_state = session_state
        self._managed_files_root = Path(managed_files_root).resolve() if managed_files_root else None

    def augment_model_text_with_recent_files(self, session_id: str, model_text: str) -> str:
        recent = self._session_state.recent_files(session_id, limit=5)
        if not recent:
            return model_text
        lines = ["Recent filesystem paths from this session (use exact paths for filesystem/apply_patch/bash):"]
        for item in recent:
            relative = item.path_relative or "-"
            lines.append(
                f"- op={item.operation}; relative={relative}; absolute={item.path_absolute}; scope={item.path_scope}"
            )
        prefix = "\n".join(lines)
        if model_text.strip():
            return f"{model_text}\n\n{prefix}"
        return prefix

    def track_from_runtime_state(self, session_id: str, runtime_state: AgentState | None) -> None:
        if runtime_state is None:
            return
        for message in runtime_state.messages:
            if message.role != "tool" or message.name != "filesystem":
                continue
            for part in message.content:
                if part.type != "json" or not isinstance(part.value, dict):
                    continue
                payload = part.value
                operation = str(payload.get("action") or "filesystem")
                for ref in self._extract_recent_file_refs(payload, operation=operation):
                    self._session_state.track_recent_file(session_id, ref)

    def _extract_recent_file_refs(self, payload: dict[str, Any], *, operation: str) -> list[RecentFileRef]:
        refs: list[RecentFileRef] = []
        seen_abs: set[str] = set()
        for candidate in self._collect_path_candidates(payload):
            canonical = self._canonicalize_path_candidate(candidate)
            if canonical is None:
                continue
            path_absolute, path_relative, path_scope = canonical
            if path_absolute in seen_abs:
                continue
            seen_abs.add(path_absolute)
            refs.append(
                RecentFileRef(
                    operation=operation,
                    path_absolute=path_absolute,
                    path_relative=path_relative,
                    path_scope=path_scope,
                )
            )
        return refs

    @staticmethod
    def _collect_path_candidates(payload: dict[str, Any]) -> list[tuple[str | None, str | None, str | None]]:
        candidates: list[tuple[str | None, str | None, str | None]] = []
        for prefix in ("", "source_", "destination_"):
            absolute = payload.get(f"{prefix}path_absolute")
            relative = payload.get(f"{prefix}path_relative")
            scope = payload.get(f"{prefix}path_scope")
            if isinstance(absolute, str) and absolute.strip():
                candidates.append(
                    (
                        absolute,
                        relative if isinstance(relative, str) else None,
                        scope if isinstance(scope, str) else None,
                    )
                )
        for key in ("path", "source_path", "destination_path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append((value, None, None))
        entries = payload.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                absolute = entry.get("path_absolute")
                relative = entry.get("path_relative")
                scope = entry.get("path_scope")
                if isinstance(absolute, str) and absolute.strip():
                    candidates.append(
                        (
                            absolute,
                            relative if isinstance(relative, str) else None,
                            scope if isinstance(scope, str) else None,
                        )
                    )
                    continue
                path_value = entry.get("path")
                if isinstance(path_value, str) and path_value.strip():
                    candidates.append((path_value, None, None))
        return candidates

    def _canonicalize_path_candidate(
        self,
        candidate: tuple[str | None, str | None, str | None],
    ) -> tuple[str, str | None, str] | None:
        raw_value, relative_hint, scope_hint = candidate
        if not isinstance(raw_value, str) or not raw_value.strip():
            return None
        path = Path(raw_value.strip()).expanduser()
        if path.is_absolute():
            resolved = path.resolve()
            if isinstance(relative_hint, str) and relative_hint.strip():
                relative = relative_hint.strip()
            elif self._managed_files_root is not None and resolved.is_relative_to(self._managed_files_root):
                relative = str(resolved.relative_to(self._managed_files_root)).replace("\\", "/")
            else:
                relative = None
            if isinstance(scope_hint, str) and scope_hint in {"inside_root", "outside_root"}:
                scope = scope_hint
            elif relative is not None:
                scope = "inside_root"
            else:
                scope = "outside_root"
            return resolved.as_posix(), relative, scope
        if self._managed_files_root is not None:
            absolute_path = (self._managed_files_root / path).resolve()
            return absolute_path.as_posix(), str(path).replace("\\", "/"), "inside_root"
        resolved = path.resolve()
        return resolved.as_posix(), str(path).replace("\\", "/"), "outside_root"
