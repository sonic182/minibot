from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from llm_async.models import Tool

from minibot.adapters.config.schema import MainAgentConfig, OrchestrationConfig, Settings, ToolOutputSpillConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.app.agent_policies import RESERVED_DELEGATION_TOOL_NAMES
from minibot.app.event_bus import EventBus
from minibot.app.extensions import load_extensions
from minibot.app.tasks import worker
from minibot.app.tool_approval import apply_tool_approval
from minibot.app.tool_capabilities import main_agent_tool_view
from minibot.app.tool_factory import derive_extension_tools
from minibot.core.agent_runtime import AgentMessage, AppendMessageDirective, MessagePart, ToolResult
from minibot.core.agents import AgentSpec
from minibot.core.events import ToolCallEvent
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.output_spill import apply_tool_output_spill
from minibot.llm.tools.tool_events import apply_tool_call_events
from minibot.shared.errors import TOOL_APPROVAL_DENIED, ToolInputError

pytest.importorskip("pydantic_monty")

_SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "delay": {"type": ["number", "null"]}},
    "required": ["label", "delay"],
}


def _binding(name: str, handler: Any, description: str = "A test tool.") -> ToolBinding:
    return ToolBinding(tool=Tool(name=name, description=description, parameters=_SCHEMA), handler=handler)


async def _slow_lookup(payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
    await asyncio.sleep(payload["delay"] or 0.3)
    return {"label": payload["label"], "delay": payload["delay"]}


async def _flaky(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
    raise ToolInputError("label is not known", error_code="flaky:bad_label")


class _CodedFailure(RuntimeError):
    error_code = "coded:failure"


async def _coded_failure(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
    raise _CodedFailure("boom")


def _settings(**codemode: Any) -> Settings:
    return Settings.from_dict({"tools": {"codemode": {"enabled": True, **codemode}}})


def _fake_tools(approve: AsyncMock | None = None) -> list[ToolBinding]:
    tools = [
        _binding("slow_lookup", _slow_lookup),
        _binding("flaky-tool.v2", _flaky),
        _binding("guarded_tool", _slow_lookup),
        _binding("spawn_task", _slow_lookup),
        _binding("coded_failure", _coded_failure),
    ]
    return apply_tool_approval(tools, patterns=["guarded_tool"], approve=approve or AsyncMock(return_value=True))


async def _codemode_tool(
    settings: Settings, tools: list[ToolBinding], event_bus: EventBus | None = None
) -> ToolBinding:
    registry = load_extensions(settings, event_bus or EventBus(), entrypoint="console")
    (tool,) = derive_extension_tools(tools, settings=settings, extensions=registry, event_bus=event_bus).tools
    return tool


async def _drain(subscription: Any) -> list[ToolCallEvent]:
    events: list[ToolCallEvent] = []
    iterator = subscription.__aiter__()
    while True:
        try:
            events.append(await asyncio.wait_for(anext(iterator), timeout=0.2))
        except TimeoutError:
            return events


_CONTEXT = ToolContext(channel="console", turn_id="turn-1")


@pytest.mark.asyncio
async def test_script_calls_tools_in_parallel_and_nested_events_name_their_parent() -> None:
    settings = _settings()
    event_bus = EventBus()
    subscription = event_bus.subscribe(types=(ToolCallEvent,))
    tools = apply_tool_call_events(_fake_tools(), event_bus=event_bus)
    tool = await _codemode_tool(settings, tools, event_bus)
    code = """
import asyncio
first, second = await asyncio.gather(
    tools.slow_lookup(label="a"), tools.slow_lookup(label="b", delay=0.25)
)
try:
    await tools.flaky_tool_v2(label="zzz")
except ValueError as exc:
    print("caught", exc)
{"labels": [first["label"], second["label"]]}
"""
    started = time.perf_counter()
    try:
        result = await tool.handler({"code": code}, _CONTEXT)
        elapsed = time.perf_counter() - started
        events = await _drain(subscription)
    finally:
        await subscription.close()

    assert result["ok"] is True
    assert result["result"] == {"labels": ["a", "b"]}
    assert "caught flaky:bad_label: label is not known" in result["output"]
    assert result["tool_errors"] == [{"tool": "flaky-tool.v2", "error_code": "flaky:bad_label"}]
    assert elapsed < 0.55
    top_level = [event for event in events if event.tool_name == "run_tools_script" and event.phase == "started"]
    assert len(top_level) == 1 and top_level[0].parent_call_id is None
    nested = [event for event in events if event.tool_name != "run_tools_script" and event.phase == "started"]
    assert sorted(event.tool_name for event in nested) == ["flaky-tool.v2", "slow_lookup", "slow_lookup"]
    assert {event.parent_call_id for event in nested} == {top_level[0].call_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("in_worker", [False, True], ids=["daemon", "worker"])
async def test_nested_results_reach_the_script_in_full_despite_output_spill(tmp_path: Path, in_worker: bool) -> None:
    async def big(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"ok": True, "items": ["x" * 20_000, "deep"]}

    async def readback(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"ok": True}

    settings = _settings()
    bus = EventBus()
    wrapped = apply_tool_output_spill(
        [_binding("big_tool", big), _binding("grep", readback)],
        storage=LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=10_000_000),
        config=ToolOutputSpillConfig(spill_after_chars=1000),
    )
    if in_worker:
        (tool,) = load_extensions(settings, bus, entrypoint="console").derive_tools(wrapped).tools
    else:
        tool = await _codemode_tool(settings, apply_tool_call_events(wrapped, event_bus=bus), bus)

    result = await tool.handler(
        {"code": "r = await tools.big_tool(label='a', delay=None)\n[len(r['items'][0]), r['items'][1]]"}, _CONTEXT
    )

    assert result["ok"] is True
    assert result["result"] == [20_000, "deep"]


@pytest.mark.asyncio
async def test_description_lists_signatures_and_hides_excluded_and_delegation_tools() -> None:
    tool = await _codemode_tool(_settings(exclude=["guarded_*"]), _fake_tools())

    description = tool.tool.description

    assert "await tools.slow_lookup(*, label: str, delay: float | None = None)" in description
    assert "await tools.flaky_tool_v2(" in description
    assert "guarded_tool" not in description
    assert not any(name in description for name in RESERVED_DELEGATION_TOOL_NAMES)
    result = await tool.handler({"code": "await tools.guarded_tool(label='x')"}, _CONTEXT)
    assert result["error_code"] == "codemode:script_error"
    assert "hint" in result


@pytest.mark.asyncio
async def test_denied_approval_reaches_the_script_and_stops_the_turn_when_uncaught() -> None:
    approve = AsyncMock(return_value=False)
    tool = await _codemode_tool(_settings(), _fake_tools(approve))

    caught = await tool.handler(
        {"code": "try:\n    await tools.guarded_tool(label='x')\nexcept PermissionError:\n    print('denied')\n"},
        _CONTEXT,
    )
    with pytest.raises(ToolInputError) as denied:
        await tool.handler({"code": "print('before')\nawait tools.guarded_tool(label='x')"}, _CONTEXT)

    approve.assert_awaited()
    assert caught["ok"] is True and caught["output"].strip() == "denied"
    assert caught["tool_errors"] == [{"tool": "guarded_tool", "error_code": TOOL_APPROVAL_DENIED}]
    assert denied.value.error_code == TOOL_APPROVAL_DENIED
    assert "before" in str(denied.value)


@pytest.mark.asyncio
async def test_a_denial_wrapped_in_another_exception_does_not_stop_the_turn() -> None:
    tool = await _codemode_tool(_settings(), _fake_tools(AsyncMock(return_value=False)))
    code = """
try:
    await tools.guarded_tool(label='x')
except PermissionError as exc:
    raise RuntimeError('wrapped')
"""

    result = await tool.handler({"code": code}, _CONTEXT)

    assert result["error_code"] == "codemode:script_error"


@pytest.mark.asyncio
async def test_a_caught_denial_does_not_relabel_a_later_unrelated_script_failure() -> None:
    tool = await _codemode_tool(_settings(), _fake_tools(AsyncMock(return_value=False)))
    code = """
try:
    await tools.guarded_tool(label='x')
except PermissionError:
    pass
await tools.slow_lookup(label='y', delay=0.01)
{}['missing']
"""

    result = await tool.handler({"code": code}, _CONTEXT)

    assert result["ok"] is False
    assert result["error_code"] == "codemode:script_error"
    assert result["tool_errors"] == [{"tool": "guarded_tool", "error_code": TOOL_APPROVAL_DENIED}]


@pytest.mark.asyncio
async def test_time_spent_waiting_for_approval_does_not_count_against_the_script_timeout() -> None:
    async def slow_approve(*_: Any) -> bool:
        await asyncio.sleep(1.3)
        return True

    tool = await _codemode_tool(_settings(timeout_seconds=1), _fake_tools(slow_approve))
    waits = "await asyncio.gather(tools.guarded_tool(label='a'), tools.guarded_tool(label='b'))\n"

    approved = await tool.handler({"code": f"import asyncio\n{waits}'done'"}, _CONTEXT)
    runaway = await tool.handler(
        {"code": f"import asyncio\n{waits}await tools.slow_lookup(label='x', delay=30)"}, _CONTEXT
    )

    assert approved["ok"] is True and approved["result"] == "done"
    assert runaway["error_code"] == "codemode:timeout"


@pytest.mark.asyncio
async def test_nested_failures_keep_their_error_code_and_scripts_get_a_tool_call_limit() -> None:
    tool = await _codemode_tool(_settings(max_tool_calls=2), _fake_tools())
    code = """
try:
    await tools.coded_failure(label='x')
except RuntimeError:
    pass
await tools.slow_lookup(label='a', delay=0.01)
try:
    await tools.slow_lookup(label='b', delay=0.01)
except ValueError as exc:
    print('limited', exc)
"""

    result = await tool.handler({"code": code}, _CONTEXT)

    assert result["ok"] is True and "limited codemode:tool_call_limit" in result["output"]
    assert result["tool_errors"] == [
        {"tool": "coded_failure", "error_code": "coded:failure"},
        {"tool": "slow_lookup", "error_code": "codemode:tool_call_limit"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error_code", "partial"),
    [
        ("print('partial')\nx = (", "codemode:syntax_error", ""),
        ("print('partial')\nawait tools.slow_lookup(label='x', delay=30)", "codemode:timeout", "partial"),
        ("print('partial')\nbig = [0] * 10**9", "codemode:memory_limit", "partial"),
        ("print('partial')\nawait tools.missing()", "codemode:script_error", "partial"),
    ],
)
async def test_failed_scripts_return_a_structured_error_with_partial_output(
    code: str, error_code: str, partial: str
) -> None:
    tool = await _codemode_tool(_settings(timeout_seconds=1, max_memory_mb=16), _fake_tools())

    result = await tool.handler({"code": code}, _CONTEXT)

    assert result["ok"] is False
    assert result["error_code"] == error_code
    assert result["output"].strip() == partial
    assert result["error"]


@pytest.mark.asyncio
async def test_scripts_discover_tools_and_cut_results_with_text() -> None:
    tool = await _codemode_tool(_settings(), _fake_tools())
    code = """
names = [entry["name"] for entry in list_tools()]
flaky = list_tools("FLAKY")
info = describe_tool("flaky-tool.v2")
rows = {"rows": list(range(50))}
try:
    describe_tool("nope")
except ValueError as exc:
    print("unknown:", exc)
{
    "names": names,
    "flaky": [entry["name"] for entry in flaky],
    "signature": info["signature"],
    "lines": text(rows, limit=3, unit="lines"),
    "chars": text("abcdef", limit=4),
    "bytes": text("ñññ", limit=3, unit="bytes"),
    "whole": text("short"),
}
"""

    result = await tool.handler({"code": code}, _CONTEXT)

    assert result["ok"] is True, result
    value = result["result"]
    assert value["names"] == ["coded_failure", "flaky_tool_v2", "guarded_tool", "slow_lookup"]
    assert value["flaky"] == ["flaky_tool_v2"]
    assert value["signature"] == "await tools.flaky_tool_v2(*, label: str, delay: float | None = None)"
    assert value["lines"] == '{\n  "rows": [\n    0,\n...[51 more lines]'
    assert value["chars"] == "abcd\n...[2 more characters]"
    assert value["bytes"] == "ñ\n...[4 more bytes]"
    assert value["whole"] == "short"
    assert "unknown: unknown tool 'nope'" in result["output"]


@pytest.mark.asyncio
async def test_directives_from_nested_calls_reach_the_turn() -> None:
    message = AgentMessage(role="user", content=[MessagePart(type="text", text="inserted")])

    async def insert(_: dict[str, Any], __: ToolContext) -> ToolResult:
        return ToolResult(
            content={"status": "ok"},
            directives=[AppendMessageDirective(type="append_message", message=message)],
        )

    tool = await _codemode_tool(_settings(), [_binding("self_insert_artifact", insert)])

    result = await tool.handler({"code": "(await tools.self_insert_artifact(label='a'))['status']"}, _CONTEXT)

    assert isinstance(result, ToolResult)
    assert result.content["result"] == "ok"
    assert [directive.message for directive in result.directives] == [message]


@pytest.mark.asyncio
async def test_long_output_keeps_start_and_end() -> None:
    tool = await _codemode_tool(_settings(max_output_chars=100), _fake_tools())

    result = await tool.handler({"code": "print('S' * 80)\nprint('M' * 500)\nprint('E' * 80)"}, _CONTEXT)

    assert result["output_truncated"] is True
    assert result["output"].startswith("S" * 50)
    assert result["output"].rstrip().endswith("E" * 40)
    assert "characters omitted" in result["output"]


def test_the_main_agent_sees_only_the_script_and_the_tools_scripts_cannot_call() -> None:
    settings = _settings(exclude=["coded_*"])
    registry = load_extensions(settings, EventBus(), entrypoint="console")
    tools = [*_fake_tools(), _binding("activate_skill", _slow_lookup)]

    def view(main_agent: MainAgentConfig) -> set[str]:
        result = main_agent_tool_view(
            tools=tools,
            orchestration_config=OrchestrationConfig(main_agent=main_agent),
            agent_specs=[],
            derive_tools=lambda final: derive_extension_tools(final, settings=settings, extensions=registry),
        )
        return {binding.tool.name for binding in result.tools}

    assert view(MainAgentConfig()) == {"run_tools_script", "spawn_task", "coded_failure", "activate_skill"}
    assert view(MainAgentConfig(tools_deny=["run_tools_script"])) == {binding.tool.name for binding in tools}


@pytest.mark.asyncio
async def test_tools_removed_by_the_main_agent_policy_are_not_callable_from_the_script() -> None:
    settings = _settings()
    registry = load_extensions(settings, EventBus(), entrypoint="console")
    config = OrchestrationConfig(main_agent=MainAgentConfig(tools_deny=["slow_lookup"]))

    view = main_agent_tool_view(
        tools=_fake_tools(),
        orchestration_config=config,
        agent_specs=[],
        derive_tools=lambda final: derive_extension_tools(final, settings=settings, extensions=registry),
    )
    script = next(binding for binding in view.tools if binding.tool.name == "run_tools_script")
    result = await script.handler({"code": "await tools.slow_lookup(label='x')"}, _CONTEXT)
    denied_script = main_agent_tool_view(
        tools=_fake_tools(),
        orchestration_config=OrchestrationConfig(main_agent=MainAgentConfig(tools_deny=["run_tools_script"])),
        agent_specs=[],
        derive_tools=lambda final: derive_extension_tools(final, settings=settings, extensions=registry),
    )

    assert "slow_lookup" not in script.tool.description
    assert result["error_code"] == "codemode:script_error"
    assert "run_tools_script" not in {binding.tool.name for binding in denied_script.tools}


@pytest.mark.asyncio
async def test_worker_gets_the_tool_only_for_specialists_that_claim_it() -> None:
    settings = Settings.from_dict(
        {
            "scheduler": {"prompts": {"enabled": False}},
            "tools": {"codemode": {"enabled": True}},
            "tasks": {"worker_extensions": ["codemode"]},
        }
    )
    extensions = load_extensions(settings, EventBus(), entrypoint="worker")
    final = _fake_tools()
    approve = AsyncMock(return_value=True)

    def derive(spec: AgentSpec) -> list[ToolBinding]:
        visible = worker.filter_tools_for_agent(final, spec, mcp_name_prefix="mcp")
        return worker._with_derived_tools(
            visible, extensions=extensions, spec=spec, settings=settings, approve=approve
        )

    generic = worker._build_worker_spec(system_prompt="p", environment_prompt_fragment="")
    specialist = AgentSpec(
        name="scripter",
        description="d",
        system_prompt="p",
        source_path=Path("/tmp/scripter.md"),
        tools_allow=["run_tools_script", "slow_lookup"],
    )
    generic_names = {binding.tool.name for binding in derive(replace(generic, tools_allow=["slow_lookup"]))}
    specialist_tools = derive(specialist)
    script = next(binding for binding in specialist_tools if binding.tool.name == "run_tools_script")
    result = await script.handler({"code": "await tools.flaky_tool_v2(label='x')"}, _CONTEXT)

    assert "run_tools_script" not in generic_names and "slow_lookup" in generic_names
    assert {binding.tool.name for binding in specialist_tools} == {"run_tools_script"}
    assert "flaky_tool_v2" not in script.tool.description
    assert result["error_code"] == "codemode:script_error"


def test_codemode_is_off_by_default_and_a_missing_extra_fails_with_the_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = load_extensions(Settings(), EventBus(), entrypoint="console")
    assert Settings().tools.codemode.enabled is False
    assert all(summary["tool_derivers"] == 0 for summary in default.summaries())

    monkeypatch.setitem(sys.modules, "pydantic_monty", None)
    with pytest.raises(ValueError, match=r"poetry install --extras codemode"):
        load_extensions(_settings(), EventBus(), entrypoint="console")
