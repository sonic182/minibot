"""Doubles for driving ``run_agent_loop`` without a provider or a real agent runtime."""

from __future__ import annotations

from types import SimpleNamespace

from minibot.adapters.agents.definition_reader import LocalAgentDefinitionReader
from minibot.adapters.config.schema import Settings
from minibot.adapters.mcp.client import MCPClient
from minibot.adapters.tasks.worker_process import build_managed_storage
from minibot.app.tasks import worker


def worker_backends(settings: Settings, build_mcp_client=MCPClient) -> worker.WorkerBackends:
    return worker.WorkerBackends(
        load_settings=lambda _secrets: settings,
        agent_reader=LocalAgentDefinitionReader(),
        build_mcp_client=build_mcp_client,
        build_storage=build_managed_storage,
    )


class FakeWorkerFactory:
    def __init__(self, _: Settings) -> None:
        self._client = FakeWorkerClient()

    def create_default(self) -> FakeWorkerClient:
        return self._client

    def create_for_agent(self, _spec) -> FakeWorkerClient:
        return self._client


class FakeWorkerClient:
    def system_prompt(self) -> str:
        return "You are Minibot."

    def model_name(self) -> str:
        return "fake-model"

    def provider_name(self) -> str:
        return "fake-provider"


class FakeWorkerRuntime:
    def __init__(self, **_: object) -> None:
        pass

    async def run(self, **_: object):
        return SimpleNamespace(
            payload="worker result",
            pre_response_meta=None,
            state=SimpleNamespace(messages=[]),
        )


class ToolCapturingRuntime(FakeWorkerRuntime):
    tools: list[object] = []

    def __init__(self, *, tools: list[object], **_: object) -> None:
        type(self).tools = tools
