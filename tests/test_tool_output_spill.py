from __future__ import annotations

from typing import Any, cast

import pytest
from llm_async.models import Tool

from minibot.adapters.config.schema import ToolOutputSpillConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.core.agent_runtime import ToolResult
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.output_spill import apply_tool_output_spill


def _binding(name: str, payload: Any) -> ToolBinding:
    async def handler(_payload: dict[str, Any], _context: ToolContext) -> Any:
        return payload

    return ToolBinding(tool=Tool(name=name, description=name, parameters={}), handler=handler)


def _readback_binding() -> ToolBinding:
    return _binding("grep", {"ok": True, "matches": []})


def _storage(tmp_path) -> LocalFileStorage:
    return LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=10_000_000)


async def _call(binding: ToolBinding) -> Any:
    return await binding.handler({}, ToolContext())


@pytest.mark.asyncio
async def test_large_output_is_spilled_to_managed_file(tmp_path) -> None:
    big = "x" * 20_000
    storage = _storage(tmp_path)
    wrapped = apply_tool_output_spill(
        [_binding("python_execute", {"ok": True, "stdout": big}), _readback_binding()],
        storage=storage,
        config=ToolOutputSpillConfig(),
    )
    result = cast(ToolResult, await _call(wrapped[0]))

    content = cast(dict[str, Any], result.content)
    assert content["output_storage"] == "managed_file"
    assert content["output_preview"] == content["output_preview"][:800]
    assert "grep" in content["output_notice"]
    saved = (storage.root_dir / str(content["output_file_path"])).read_text(encoding="utf-8")
    assert big in saved


@pytest.mark.asyncio
async def test_small_output_is_untouched(tmp_path) -> None:
    wrapped = apply_tool_output_spill(
        [_binding("python_execute", {"ok": True, "stdout": "hi"}), _readback_binding()],
        storage=_storage(tmp_path),
        config=ToolOutputSpillConfig(),
    )
    result = cast(ToolResult, await _call(wrapped[0]))
    assert result.content == {"ok": True, "stdout": "hi"}


@pytest.mark.asyncio
async def test_no_readback_tool_disables_spill(tmp_path) -> None:
    big = {"ok": True, "stdout": "x" * 20_000}
    wrapped = apply_tool_output_spill(
        [_binding("python_execute", big)],
        storage=_storage(tmp_path),
        config=ToolOutputSpillConfig(),
    )
    assert await _call(wrapped[0]) == big


@pytest.mark.asyncio
async def test_error_results_stay_inline(tmp_path) -> None:
    failure = {"ok": False, "error": "x" * 20_000, "failure_signature": "abc"}
    wrapped = apply_tool_output_spill(
        [_binding("python_execute", failure), _readback_binding()],
        storage=_storage(tmp_path),
        config=ToolOutputSpillConfig(),
    )
    result = cast(ToolResult, await _call(wrapped[0]))
    assert result.content == failure


@pytest.mark.asyncio
async def test_excluded_tools_are_not_wrapped(tmp_path) -> None:
    big = {"ok": True, "stdout": "x" * 20_000}
    bindings = [_binding("bash", big), _readback_binding()]
    wrapped = apply_tool_output_spill(bindings, storage=_storage(tmp_path), config=ToolOutputSpillConfig())
    assert wrapped[0].handler is bindings[0].handler


@pytest.mark.asyncio
async def test_disabled_config_is_a_noop(tmp_path) -> None:
    bindings = [_binding("python_execute", {"ok": True, "stdout": "x" * 20_000}), _readback_binding()]
    wrapped = apply_tool_output_spill(
        bindings,
        storage=_storage(tmp_path),
        config=ToolOutputSpillConfig(enabled=False),
    )
    assert wrapped[0].handler is bindings[0].handler
