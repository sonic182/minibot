from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_async.models import Tool

from minibot.adapters.config.schema import ApplyPatchToolConfig
from minibot.llm.tools.arg_utils import require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.patch_engine import apply_patch_actions, parse_patch, plan_patch_actions
from minibot.llm.tools.schema_utils import strict_object, string_field


class ApplyPatchTool:
    def __init__(self, config: ApplyPatchToolConfig) -> None:
        self._config = config

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="apply_patch",
            description=load_tool_description("apply_patch"),
            parameters=strict_object(
                properties={
                    "patch_text": string_field(
                        "Full patch text in apply_patch format, including *** Begin Patch and *** End Patch."
                    )
                },
                required=["patch_text"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        patch_text = require_non_empty_str(payload, "patch_text")
        patch_size = len(patch_text.encode("utf-8"))
        if patch_size > self._config.max_patch_bytes:
            raise ValueError(f"patch_text exceeds limit {self._config.max_patch_bytes} bytes")

        try:
            parse_result = parse_patch(patch_text)
            if not parse_result.hunks:
                normalized = patch_text.replace("\r\n", "\n").replace("\r", "\n").strip()
                if normalized == "*** Begin Patch\n*** End Patch":
                    raise ValueError("patch rejected: empty patch")
                raise ValueError("no hunks found")

            workspace_root = Path(self._config.workspace_root).expanduser().resolve()
            actions = plan_patch_actions(
                hunks=parse_result.hunks,
                workspace_root=workspace_root,
                restrict_to_workspace=self._config.restrict_to_workspace,
                allow_outside_workspace=self._config.allow_outside_workspace,
                preserve_trailing_newline=self._config.preserve_trailing_newline,
            )
            result = apply_patch_actions(actions, workspace_root)
        except Exception as exc:
            error = str(exc)
            if error.startswith("patch rejected:"):
                raise ValueError(error) from exc
            raise ValueError(f"apply_patch verification failed: {error}") from exc

        summary = "Success. Updated the following files:\n" + "\n".join(result.summary_lines)
        return {
            "ok": True,
            "summary": summary,
            "updated_files": result.summary_lines,
            "workspace_root": str(workspace_root),
        }
