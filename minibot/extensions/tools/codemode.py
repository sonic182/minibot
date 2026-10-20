from __future__ import annotations

import asyncio
import importlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from types import ModuleType
from typing import Any
from uuid import uuid4

from llm_async.models import Tool

from minibot.app.agent_policies import RESERVED_DELEGATION_TOOL_NAMES
from minibot.app.extensions import DerivedTool, ExtensionContext
from minibot.app.tool_approval import approval_wait_observer
from minibot.app.tool_policy_utils import matches_any, normalize_patterns
from minibot.config.schema import CodemodeToolConfig
from minibot.core.tools import ToolContext, ToolPayload
from minibot.llm.services.tool_executor import canonical_tool_name, normalize_tool_result
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import single_required_field_object, string_field
from minibot.llm.tools.script_signatures import admits_null, render_signature, unique_identifiers
from minibot.shared.errors import TOOL_APPROVAL_DENIED, ToolInputError

TOOL_NAME = "run_tools_script"
SYNTAX_ERROR = "codemode:syntax_error"
SCRIPT_ERROR = "codemode:script_error"
TIMEOUT = "codemode:timeout"
MEMORY_LIMIT = "codemode:memory_limit"
TOOL_CALL_LIMIT = "codemode:tool_call_limit"
SANDBOX_FAILED = "codemode:sandbox_failed"
_SIGNATURES_MAX_CHARS = 6000
_SUMMARY_MAX_CHARS = 200
# These tools put content into the turn itself: skill instructions (the system prompt only advertises skills
# while the skill tools are attached) and self_insert_artifact's message directive, which the runtime only accepts
# from that tool by name. Called from a script that would be lost, so they stay direct, like the delegation tools.
_DIRECT_TOOL_NAMES = frozenset({"activate_skill", "list_skills", "self_insert_artifact"})
_UNLISTED_NAMES_MAX = 100
_TEXT_UNITS = ("chars", "lines", "bytes")
_TEXT_DEFAULT_LIMIT = 2000
_logger = logging.getLogger("minibot.codemode")
_INSTALL_HINT = "run `poetry install --extras codemode` (or `pip install 'minibot[codemode]'`)"


def register(mb: ExtensionContext) -> None:
    config = mb.settings.tools.codemode
    if not config.enabled:
        return
    monty = _import_monty()
    mb.add_tool_deriver(lambda tools: _derive(monty, config, tools))


def _import_monty() -> ModuleType:
    try:
        return importlib.import_module("pydantic_monty")
    except ImportError as exc:
        raise ValueError(
            f"[tools.codemode] is enabled but the 'pydantic-monty' package is not installed: {_INSTALL_HINT}"
        ) from exc


def _derive(monty: ModuleType, config: CodemodeToolConfig, tools: Sequence[ToolBinding]) -> list[DerivedTool]:
    """``run_tools_script`` over every tool a script may call, replacing them as direct tools.

    The delegation tools, ``_DIRECT_TOOL_NAMES`` and those matching ``exclude`` are not callable from scripts and stay
    direct tools.
    """
    excluded = normalize_patterns(config.exclude)
    callable_tools = sorted(
        (
            binding
            for binding in tools
            if binding.tool.name not in RESERVED_DELEGATION_TOOL_NAMES
            and binding.tool.name not in _DIRECT_TOOL_NAMES
            and binding.tool.name != TOOL_NAME
            and not matches_any(canonical_tool_name(binding.tool.name), excluded)
        ),
        key=lambda binding: binding.tool.name,
    )
    if not callable_tools:
        return []
    runner = _ScriptRunner(monty, config, callable_tools)
    return [
        DerivedTool(
            binding=ToolBinding(tool=runner.schema(), handler=runner.handle),
            replaces=frozenset(binding.tool.name for binding in callable_tools),
        )
    ]


class _BoundedText:
    """Collects text without ever holding more than ``limit`` characters: the start and the end survive."""

    def __init__(self, limit: int) -> None:
        self._head_size = limit // 2
        self._tail_size = limit - self._head_size
        self._head = ""
        self._tail = ""
        self._omitted = 0

    def write(self, text: str) -> None:
        room = self._head_size - len(self._head)
        if room > 0:
            self._head += text[:room]
            text = text[room:]
        if text:
            combined = self._tail + text
            if len(combined) > self._tail_size:
                self._omitted += len(combined) - self._tail_size
                combined = combined[len(combined) - self._tail_size :]
            self._tail = combined

    @property
    def truncated(self) -> bool:
        return self._omitted > 0

    def render(self) -> str:
        if not self._omitted:
            return self._head + self._tail
        return f"{self._head}\n...[{self._omitted} characters omitted]...\n{self._tail}"


class _ScriptDeadline:
    """The script's wall-clock budget, paused while a nested call waits for the owner's approval.

    The owner's thinking time must not count: the approval has its own timeout, and a script that ran out
    of time first would leave a late approval with nothing to do. Waits can overlap (``asyncio.gather``),
    so the budget stays paused until the last one ends.
    """

    def __init__(self) -> None:
        self._timeout: asyncio.Timeout | None = None
        self._waiting = 0
        self._remaining = 0.0

    def attach(self, timeout: asyncio.Timeout) -> None:
        self._timeout = timeout

    def close(self) -> None:
        self._timeout = None

    def begin(self) -> None:
        timeout = self._timeout
        if timeout is not None and self._waiting == 0 and not timeout.expired():
            when = timeout.when()
            if when is not None:
                self._remaining = max(when - asyncio.get_running_loop().time(), 0.0)
                timeout.reschedule(None)
        self._waiting += 1

    def end(self) -> None:
        self._waiting -= 1
        timeout = self._timeout
        # An expired timeout is already cancelling the script; its state can no longer be changed.
        if timeout is not None and self._waiting == 0 and timeout.when() is None and not timeout.expired():
            timeout.reschedule(asyncio.get_running_loop().time() + self._remaining)


@dataclass
class _Run:
    output: _BoundedText
    tool_errors: list[dict[str, str]] = field(default_factory=list)
    # Tools whose approval was denied, in order. A denial reaches the script as PermissionError, one of the
    # builtins Monty keeps intact; a script that ends on an uncaught PermissionError surfaces the denial itself so
    # the turn loop stops the remaining calls like it does for a direct one. A denial the script caught or wrapped
    # in another exception type ends as an ordinary script failure.
    denied: list[str] = field(default_factory=list)
    called: list[str] = field(default_factory=list)


class _ScriptRunner:
    def __init__(self, monty: ModuleType, config: CodemodeToolConfig, tools: Sequence[ToolBinding]) -> None:
        self._monty = monty
        self._config = config
        identifiers = unique_identifiers([binding.tool.name for binding in tools])
        self._tools = {identifiers[binding.tool.name]: binding for binding in tools}

    def schema(self) -> Tool:
        return Tool(
            name=TOOL_NAME,
            description=self._description(),
            parameters=single_required_field_object(
                "code",
                string_field(
                    "Python script. Call tools as `await tools.<name>(keyword=value)`; "
                    "print what you need and end with the value to return."
                ),
            ),
        )

    def _description(self) -> str:
        static = load_tool_description(TOOL_NAME, package=__spec__.parent if __spec__ else __package__ or "")
        lines: list[str] = []
        unlisted: list[str] = []
        used = 0
        for identifier, binding in self._tools.items():
            line = render_signature(identifier, binding.tool.description or "", binding.tool.parameters or {})
            if unlisted or used + len(line) + 1 > _SIGNATURES_MAX_CHARS:
                unlisted.append(identifier)
                continue
            lines.append(line)
            used += len(line) + 1
        header = "Tools available to scripts:"
        footer = ""
        if unlisted:
            shown = ", ".join(unlisted[:_UNLISTED_NAMES_MAX])
            more = len(unlisted) - _UNLISTED_NAMES_MAX
            rest = f", and {more} more (search them with list_tools(query))" if more > 0 else ""
            footer = f"\nMore tools, without their signature here (use describe_tool(name) first): {shown}{rest}"
        return f"{static}\n\n{header}\n" + "\n".join(lines) + footer

    async def handle(self, payload: ToolPayload, context: ToolContext) -> dict[str, Any]:
        code = payload.get("code") if isinstance(payload, dict) else None
        if not isinstance(code, str) or not code.strip():
            raise ToolInputError("code must be a non-empty Python script", error_code="invalid_tool_arguments")
        run = _Run(output=_BoundedText(self._config.max_output_chars))
        monty = self._monty
        limits = monty.ResourceLimits(
            max_feed_duration_secs=float(self._config.timeout_seconds),
            max_memory=self._config.max_memory_mb * 1024 * 1024,
        )
        namespace = monty.ClassInstance(self._namespace(run, context), allowed_methods=frozenset(self._tools))
        failure: tuple[str, str, dict[str, Any]] | None = None
        denied_tool: str | None = None
        value: Any = None
        deadline = _ScriptDeadline()
        try:
            async with monty.AsyncMonty() as pool, pool.checkout(limits=limits) as session:
                # Monty's feed limit only counts time inside the sandbox, not awaits on host tools, so the
                # script's real wall-clock budget has to be enforced here.
                async with asyncio.timeout(self._config.timeout_seconds) as timeout:
                    deadline.attach(timeout)
                    observer_token = approval_wait_observer.set(deadline)
                    try:
                        value = await session.feed_run(
                            code,
                            inputs={"tools": namespace},
                            external_lookup=self._helpers(),
                            print_callback=lambda _stream, text: run.output.write(text),
                        )
                    finally:
                        approval_wait_observer.reset(observer_token)
        except TimeoutError:
            failure = (TIMEOUT, f"the script did not finish within {self._config.timeout_seconds} seconds", {})
        except monty.MontySyntaxError as exc:
            failure = (SYNTAX_ERROR, str(exc), {})
        except monty.MontyRuntimeError as exc:
            failure = self._classify_runtime_error(exc)
            denied_tool = self._denied_tool(run, exc)
        except monty.MontyError as exc:
            _logger.warning("codemode sandbox failed", exc_info=True)
            failure = (SANDBOX_FAILED, f"the script sandbox failed: {exc}", {})
        finally:
            deadline.close()
        _logger.info(
            "codemode script finished",
            extra={"ok": failure is None, "tool_calls": run.called, "tool_errors": len(run.tool_errors)},
        )
        if failure is None:
            return self._success(run, value)
        error_code, message, extra = failure
        if denied_tool is not None:
            raise ToolInputError(self._denial_message(run, denied_tool), error_code=TOOL_APPROVAL_DENIED)
        result: dict[str, Any] = {"ok": False, "error_code": error_code, "error": message, **extra}
        result["output"] = run.output.render()
        if run.tool_errors:
            result["tool_errors"] = run.tool_errors
        return result

    def _helpers(self) -> dict[str, Callable[..., Any]]:
        """Host functions scripts call by name: tool discovery and bounded text rendering."""
        by_name = {binding.tool.name: identifier for identifier, binding in self._tools.items()}

        def list_tools(query: str | None = None) -> list[dict[str, str]]:
            needle = (query or "").strip().lower()
            entries = []
            for identifier, binding in self._tools.items():
                description = binding.tool.description or ""
                if needle and needle not in f"{identifier} {binding.tool.name} {description}".lower():
                    continue
                entries.append({"name": identifier, "summary": _summary(description)})
            return entries

        def describe_tool(name: str) -> dict[str, Any]:
            identifier = name if name in self._tools else by_name.get(name)
            if identifier is None:
                raise ValueError(f"unknown tool {name!r}; list_tools() returns the names scripts can call")
            binding = self._tools[identifier]
            parameters = binding.tool.parameters or {}
            return {
                "name": identifier,
                "signature": render_signature(identifier, "", parameters),
                "description": binding.tool.description or "",
                "parameters": parameters,
            }

        return {"list_tools": list_tools, "describe_tool": describe_tool, "text": render_text}

    def _classify_runtime_error(self, exc: Any) -> tuple[str, str, dict[str, Any]]:
        inner = exc.exception()
        message = exc.display("type-msg")
        if isinstance(inner, TimeoutError):
            return TIMEOUT, message, {}
        if isinstance(inner, MemoryError):
            return MEMORY_LIMIT, message, {}
        if isinstance(inner, AttributeError | NameError):
            hint = (
                "Scripts can only call the tools listed in this tool's description or by list_tools(), "
                "as `await tools.<name>(...)`."
            )
            return SCRIPT_ERROR, message, {"hint": hint}
        return SCRIPT_ERROR, message, {}

    def _success(self, run: _Run, value: Any) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": True, "output": run.output.render()}
        if run.output.truncated:
            result["output_truncated"] = True
        if value is not None:
            try:
                text = json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                text = repr(value)
                value = text
            bounded = _BoundedText(self._config.max_output_chars)
            bounded.write(text)
            if bounded.truncated:
                result["result"] = bounded.render()
                result["result_truncated"] = True
            else:
                result["result"] = value
        if run.tool_errors:
            result["tool_errors"] = run.tool_errors
        return result

    @staticmethod
    def _denied_tool(run: _Run, exc: Any) -> str | None:
        if isinstance(exc.exception(), PermissionError) and run.denied:
            return run.denied[-1]
        return None

    @staticmethod
    def _denial_message(run: _Run, denied_tool: str) -> str:
        message = f"The user did not approve {denied_tool}. Do not retry it unless the user asks again."
        printed = run.output.render()
        return f"{message}\nScript output before the denial:\n{printed}" if printed else message

    def _namespace(self, run: _Run, context: ToolContext) -> object:
        # Workers run tools without the events wrapper, so the script may have no call_id; nested calls still
        # need a parent id, which is what exempts them from output spill.
        nested_context = replace(context, parent_call_id=context.call_id or uuid4().hex, call_id=None)
        namespace = _ToolsNamespace()
        for identifier, binding in self._tools.items():
            setattr(
                namespace,
                identifier,
                _bind_call(identifier, binding, run, nested_context, self._config.max_tool_calls),
            )
        return namespace


class _ToolsNamespace: ...


def _bind_call(
    identifier: str, binding: ToolBinding, run: _Run, context: ToolContext, max_tool_calls: int
) -> Callable[..., Awaitable[Any]]:
    tool_name = binding.tool.name
    parameters = binding.tool.parameters or {}
    declared = parameters.get("properties")
    properties: dict[str, Any] = declared if isinstance(declared, dict) else {}
    # Strict-schema tools list every property as required and allow null for the optional ones, so a
    # direct call always sends them; fill the omitted ones the same way instead of leaving handlers
    # to a payload shape no model produces.
    nullable_required = [
        key
        for key in parameters.get("required") or []
        if isinstance(properties.get(key), dict) and admits_null(properties[key])
    ]

    async def call(*args: Any, **kwargs: Any) -> Any:
        if args:
            raise TypeError(f"tools.{identifier}() takes keyword arguments only")
        payload: ToolPayload = {key: None for key in nullable_required} | kwargs
        # Nested calls skip the agent's per-turn tool-call cap, so the script gets its own.
        if len(run.called) >= max_tool_calls:
            _record_failure(run, tool_name, TOOL_CALL_LIMIT)
            raise ValueError(f"{TOOL_CALL_LIMIT}: a script can make at most {max_tool_calls} tool calls")
        run.called.append(tool_name)
        try:
            raw = await binding.handler(payload, context)
        except ToolInputError as exc:
            _record_failure(run, tool_name, exc.error_code)
            message = f"{exc.error_code}: {exc}"
            if exc.error_code == TOOL_APPROVAL_DENIED:
                run.denied.append(tool_name)
                raise PermissionError(message) from exc
            raise ValueError(message) from exc
        except Exception as exc:
            error_code = getattr(exc, "error_code", "") or "tool_execution_failed"
            _record_failure(run, tool_name, error_code)
            raise RuntimeError(f"{tool_name} failed: {exc}") from exc
        return _plain(normalize_tool_result(raw).content)

    return call


def _record_failure(run: _Run, tool_name: str, error_code: str) -> None:
    # The message is left out of the log: it may carry what the tool was given.
    run.tool_errors.append({"tool": tool_name, "error_code": error_code})
    _logger.warning("codemode nested tool call failed", extra={"tool": tool_name, "error_code": error_code})


def render_text(value: Any, limit: int = _TEXT_DEFAULT_LIMIT, unit: str = "chars") -> str:
    """``value`` as text cut to ``limit`` chars, lines or UTF-8 bytes, keeping the start.

    Strings are kept as they are; anything else is rendered as indented JSON. A cut text ends with a marker
    saying how much was left out.
    """
    if unit not in _TEXT_UNITS:
        raise ValueError(f"unit must be one of {', '.join(_TEXT_UNITS)}, not {unit!r}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if unit == "lines":
        lines = rendered.splitlines(keepends=True)
        if len(lines) <= limit:
            return rendered
        return "".join(lines[:limit]).rstrip("\n") + f"\n...[{len(lines) - limit} more lines]"
    if unit == "bytes":
        encoded = rendered.encode("utf-8")
        if len(encoded) <= limit:
            return rendered
        # A cut inside a multi-byte character drops that character, so count what was really kept.
        kept = encoded[:limit].decode("utf-8", errors="ignore")
        return kept + f"\n...[{len(encoded) - len(kept.encode('utf-8'))} more bytes]"
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + f"\n...[{len(rendered) - limit} more characters]"


def _summary(description: str) -> str:
    for line in description.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped if len(stripped) <= _SUMMARY_MAX_CHARS else f"{stripped[: _SUMMARY_MAX_CHARS - 3]}..."
    return ""


def _plain(content: Any) -> Any:
    """JSON-shaped copy of a tool result, since only plain values cross into the sandbox."""
    return json.loads(json.dumps(content, default=str))
