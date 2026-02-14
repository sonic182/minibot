from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Sequence

from minibot.core.agent_runtime import ToolResult
from minibot.core.agents import AgentSpec
from minibot.llm.provider_factory import LLMCompletionStep, LLMGeneration, ToolExecutionRecord
from minibot.llm.tools.base import ToolBinding, ToolContext


@dataclass
class _MockToolCall:
    id: str
    type: str
    function: dict[str, Any] | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class _MockMessage:
    content: Any
    tool_calls: list[_MockToolCall] | None = None


class ScriptedLLMClient:
    def __init__(
        self,
        *,
        provider: str,
        model: str = "gpt-4o-mini",
        system_prompt: str = "You are Minibot, a helpful assistant.",
        prompts_dir: str = "./prompts",
        max_tool_iterations: int = 8,
    ) -> None:
        self._provider = provider
        self._model = model
        self._system_prompt = system_prompt
        self._prompts_dir = prompts_dir
        self._max_tool_iterations = max_tool_iterations
        self.generate_steps: list[dict[str, Any]] = []
        self.runtime_steps: list[dict[str, Any]] = []
        self.generate_requests: list[dict[str, Any]] = []
        self.complete_requests: list[dict[str, Any]] = []

    async def generate(
        self,
        history: Sequence[Any],
        user_message: str,
        user_content: str | list[dict[str, Any]] | None = None,
        tools: Sequence[ToolBinding] | None = None,
        tool_context: ToolContext | None = None,
        response_schema: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        previous_response_id: str | None = None,
        system_prompt_override: str | None = None,
    ) -> LLMGeneration:
        self.generate_requests.append(
            {
                "history_count": len(history),
                "user_message": user_message,
                "tool_names": [binding.tool.name for binding in (tools or [])],
                "response_schema": response_schema,
                "prompt_cache_key": prompt_cache_key,
                "previous_response_id": previous_response_id,
                "system_prompt_override": system_prompt_override,
                "has_user_content": user_content is not None,
                "has_tool_context": tool_context is not None,
            }
        )
        step = self._pop_step(self.generate_steps, "generate")
        self._maybe_raise(step)
        return LLMGeneration(
            payload=step.get("payload", ""),
            response_id=step.get("response_id"),
            total_tokens=step.get("total_tokens"),
        )

    async def complete_once(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolBinding] | None = None,
        response_schema: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        previous_response_id: str | None = None,
    ) -> LLMCompletionStep:
        self.complete_requests.append(
            {
                "messages": list(messages),
                "tool_names": [binding.tool.name for binding in (tools or [])],
                "response_schema": response_schema,
                "prompt_cache_key": prompt_cache_key,
                "previous_response_id": previous_response_id,
            }
        )
        step = self._pop_step(self.runtime_steps, "complete_once")
        self._maybe_raise(step)
        tool_name = step.get("tool_name")
        message: _MockMessage
        if isinstance(tool_name, str) and tool_name:
            arguments = step.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            call_id = str(step.get("call_id") or "call-1")
            message = _MockMessage(
                content=str(step.get("content") or ""),
                tool_calls=[
                    _MockToolCall(
                        id=call_id,
                        type="function",
                        function={"name": tool_name, "arguments": json.dumps(arguments)},
                    )
                ],
            )
        else:
            message = _MockMessage(content=step.get("content", ""), tool_calls=[])
        return LLMCompletionStep(
            message=message,
            response_id=step.get("response_id"),
            total_tokens=step.get("total_tokens"),
        )

    async def execute_tool_calls_for_runtime(
        self,
        tool_calls: Sequence[_MockToolCall],
        tools: Sequence[ToolBinding],
        context: ToolContext,
        responses_mode: bool = False,
    ) -> list[ToolExecutionRecord]:
        tool_map = {binding.tool.name: binding for binding in tools}
        records: list[ToolExecutionRecord] = []
        for call in tool_calls:
            tool_name, arguments = self._parse_tool_call(call)
            binding = tool_map.get(tool_name)
            if binding is None:
                result = ToolResult(
                    content={
                        "ok": False,
                        "tool": tool_name,
                        "error_code": "tool_execution_failed",
                        "error": f"tool {tool_name} is not registered",
                    }
                )
            else:
                raw_result = await binding.handler(arguments, context)
                result = raw_result if isinstance(raw_result, ToolResult) else ToolResult(content=raw_result)
            if responses_mode:
                payload = {
                    "type": "function_call_output",
                    "call_id": call.id,
                    "output": self._stringify(result.content),
                }
            else:
                payload = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": tool_name,
                    "content": self._stringify(result.content),
                }
            records.append(
                ToolExecutionRecord(
                    tool_name=tool_name,
                    call_id=call.id,
                    message_payload=payload,
                    result=result,
                )
            )
        return records

    def is_responses_provider(self) -> bool:
        return self._provider == "openai_responses"

    def provider_name(self) -> str:
        return self._provider

    def model_name(self) -> str:
        return self._model

    def system_prompt(self) -> str:
        return self._system_prompt

    def prompts_dir(self) -> str:
        return self._prompts_dir

    def max_tool_iterations(self) -> int:
        return self._max_tool_iterations

    def supports_media_inputs(self) -> bool:
        return self._provider in {"openai", "openai_responses", "openrouter"}

    def media_input_mode(self) -> str:
        if self._provider == "openai_responses":
            return "responses"
        if self._provider in {"openai", "openrouter"}:
            return "chat_completions"
        return "none"

    @staticmethod
    def _parse_tool_call(call: _MockToolCall) -> tuple[str, dict[str, Any]]:
        if isinstance(call.function, dict):
            name = str(call.function.get("name") or "")
            raw_args = call.function.get("arguments")
            if isinstance(raw_args, str) and raw_args.strip():
                parsed = json.loads(raw_args)
                args = parsed if isinstance(parsed, dict) else {}
            elif isinstance(raw_args, dict):
                args = dict(raw_args)
            else:
                args = {}
            return name, args
        if isinstance(call.name, str) and call.name:
            return call.name, dict(call.input or {})
        return "unknown_tool", {}

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=True, default=str)
        return str(value)

    @staticmethod
    def _pop_step(steps: list[dict[str, Any]], operation: str) -> dict[str, Any]:
        if steps:
            return steps.pop(0)
        raise RuntimeError(f"no scripted step for {operation}")

    @staticmethod
    def _maybe_raise(step: dict[str, Any]) -> None:
        error = step.get("raise")
        if error is not None:
            raise RuntimeError(str(error))


class ScriptedLLMFactory:
    def __init__(
        self,
        *,
        default_client: ScriptedLLMClient,
        agent_clients: dict[str, ScriptedLLMClient] | None = None,
    ) -> None:
        self._default_client = default_client
        self._agent_clients = dict(agent_clients or {})

    def create_default(self) -> ScriptedLLMClient:
        return self._default_client

    def create_for_agent(self, spec: AgentSpec) -> ScriptedLLMClient:
        return self._agent_clients.get(spec.name, self._default_client)
