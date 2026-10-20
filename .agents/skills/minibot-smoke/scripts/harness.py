"""Smoke-test harness: a full MiniBot session (container, dispatcher, extensions, task queue and forked
workers) driven by scripted LLMs instead of a real provider.

Import it from a scenario script run from the repo root with ``poetry run python``. Each session
writes its own config.toml and databases to a temporary directory; nothing touches config.toml,
data/ or the network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import tomlkit

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from minibot.adapters.messaging.console.service import ConsoleService  # noqa: E402
from minibot.app.tasks import worker  # noqa: E402
from minibot.llm.services.tool_executor import execute_tool_calls_for_runtime  # noqa: E402
from tests.fixtures.console_harness import console_session  # noqa: E402
from tests.fixtures.llm.mock_client import ScriptedLLMClient, ScriptedLLMFactory  # noqa: E402

# A policy gets the rendered request messages of one provider step and returns the reply:
# {"content": "..."} to answer, or {"tool_name": "...", "arguments": {...}} to call one tool.
Policy = Callable[[list[dict[str, Any]]], dict[str, Any]]


class SmokeFailure(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


# --- reading the context a policy receives -------------------------------------------------------


def message_texts(messages: Sequence[dict[str, Any]], role: str | None = None) -> list[str]:
    """Plain text of each message (string content or text parts), optionally for one role."""
    texts: list[str] = []
    for message in messages:
        if role is not None and message.get("role") != role:
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.append("".join(str(part.get("text", "")) for part in content if isinstance(part, dict)))
    return texts


def last_user_text(messages: Sequence[dict[str, Any]]) -> str:
    texts = message_texts(messages, role="user")
    return texts[-1] if texts else ""


def tool_results(messages: Sequence[dict[str, Any]]) -> int:
    """How many tool results the context holds."""
    return sum(1 for message in messages if message.get("role") == "tool")


def pending_tool_result(messages: Sequence[dict[str, Any]]) -> Any:
    """The parsed result of the tool call this step answers, or None when the step starts a turn."""
    if not messages or messages[-1].get("role") != "tool":
        return None
    content = messages[-1].get("content")
    try:
        return json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        return content


def call(tool_name: str, **arguments: Any) -> dict[str, Any]:
    return {"tool_name": tool_name, "arguments": arguments}


def answer(text: str) -> dict[str, Any]:
    return {"content": text}


# --- scripted LLM --------------------------------------------------------------------------------


class _PolicyLLMClient(ScriptedLLMClient):
    def __init__(self, policy: Policy, role: str) -> None:
        super().__init__(provider="openai", model=f"smoke-{role}")
        self._policy = policy
        self._role = role
        self._step = 0

    async def complete_once(self, messages, tools=None, response_schema=None, **kwargs):
        self._step += 1
        reply = dict(self._policy(list(messages)))
        reply.setdefault("call_id", f"smoke-{self._role}-{self._step}")
        self.runtime_steps.append(reply)
        return await super().complete_once(messages, tools=tools, response_schema=response_schema, **kwargs)

    async def execute_tool_calls_for_runtime(
        self, tool_calls, tools, context, responses_mode=False, should_interrupt=None
    ):
        return await execute_tool_calls_for_runtime(
            tool_calls,
            tools,
            context,
            responses_mode=responses_mode,
            logger=logging.getLogger(f"minibot.smoke.{self._role}"),
            should_interrupt=should_interrupt,
        )


def _factory(policy: Policy, role: str) -> ScriptedLLMFactory:
    return ScriptedLLMFactory(default_client=_PolicyLLMClient(policy, role))


def _no_worker(_messages: list[dict[str, Any]]) -> dict[str, Any]:
    raise SmokeFailure("a task worker ran, but the scenario gave no worker policy")


# --- configuration -------------------------------------------------------------------------------


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def smoke_config(directory: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Base config: console only, the SQLite task queue polled fast, the `wait` tool on, skills off."""
    base = {
        "runtime": {"log_level": "INFO" if "-v" in sys.argv else "WARNING"},
        "channels": {"telegram": {"enabled": False, "bot_token": ""}},
        "llm": {"provider": "openai", "model": "smoke-model", "system_prompt": "You are Minibot."},
        "providers": {"openai": {"api_key": "smoke", "base_url": "http://smoke.invalid/v1"}},
        "memory": {"sqlite_url": f"sqlite+aiosqlite:///{(directory / 'memory.db').as_posix()}"},
        "scheduler": {"prompts": {"enabled": False}},
        "tools": {"wait": {"enabled": True}, "skills": {"enabled": False}},
        "tasks": {
            "enabled": True,
            "backend": "sqlite",
            "sqlite": {
                "sqlite_url": f"sqlite+aiosqlite:///{(directory / 'tasks.db').as_posix()}",
                "poll_interval_seconds": 1,
            },
        },
    }
    return _merge(base, overrides or {})


# --- session -------------------------------------------------------------------------------------


class _SilentConsole:
    def print(self, value: object) -> None:
        return None


@dataclass
class SmokeSession:
    console: ConsoleService
    directory: Path
    replies: list[str] = field(default_factory=list)

    async def say(self, text: str) -> None:
        """Send a user message to the orchestrator, as the console channel would."""
        await self.console.publish_user_message(text)

    async def reply(self, *, until: Callable[[str], bool] = lambda _text: True, timeout: float = 10) -> str:
        """The next outbound text that satisfies `until`; every reply is kept in `replies`."""
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                response = await self.console.wait_for_response(remaining)
            except TimeoutError:
                break
            text = response.response.text or response.rendered_text
            self.replies.append(text)
            if until(text):
                return text
        raise SmokeFailure(f"no matching reply within {timeout}s; replies so far: {self.replies}")


@asynccontextmanager
async def smoke_session(
    *,
    orchestrator: Policy,
    worker_policy: Policy | None = None,
    config: dict[str, Any] | None = None,
) -> AsyncIterator[SmokeSession]:
    """A console session whose main agent runs `orchestrator` and whose task workers run `worker_policy`.

    `config` is merged over `smoke_config()`.
    """
    with tempfile.TemporaryDirectory(prefix="minibot-smoke-") as tmp:
        directory = Path(tmp)
        config_path = directory / "config.toml"
        config_path.write_text(tomlkit.dumps(smoke_config(directory, config)), encoding="utf-8")
        # Workers reload settings from MINIBOT_CONFIG, and the worker LLM patch reaches them only
        # because they are forked from this already-patched process.
        multiprocessing.set_start_method("fork", force=True)
        with (
            patch.dict(os.environ, {"MINIBOT_CONFIG": str(config_path)}),
            patch.object(
                worker, "LLMClientFactory", lambda _settings: _factory(worker_policy or _no_worker, "worker")
            ),
        ):
            async with console_session(
                config_path=config_path,
                llm_factory=_factory(orchestrator, "orchestrator"),
                chat_id=1,
                user_id=1,
                console=_SilentConsole(),
            ) as console:
                yield SmokeSession(console=console, directory=directory)


# --- running -------------------------------------------------------------------------------------


def run(scenario: Callable[[], Any]) -> None:
    """Run an async scenario and exit 0 on PASS, 1 on FAIL. `-v` shows INFO logs on stderr."""
    logging.basicConfig(
        level=logging.INFO if "-v" in sys.argv else logging.WARNING,
        format="%(asctime)s %(processName)s %(name)s %(message)s",
        stream=sys.stderr,
        force=True,
    )
    name = Path(sys.argv[0]).stem
    started = time.monotonic()
    try:
        summary = asyncio.run(scenario())
    except SmokeFailure as exc:
        print(f"FAIL {name}: {exc}")  # noqa: T201
        raise SystemExit(1) from exc
    summary = {**(summary or {}), "elapsed_s": round(time.monotonic() - started, 2)}
    print(f"PASS {name}: {json.dumps(summary, default=str)}")  # noqa: T201
