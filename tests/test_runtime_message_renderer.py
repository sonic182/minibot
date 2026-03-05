from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.llm.services.runtime_message_renderer import RuntimeMessageRenderer


@dataclass
class _FakeToolCall:
    id: str
    type: str = "function"
    function: dict[str, Any] | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class _FakeMessage:
    content: Any
    tool_calls: list[_FakeToolCall] | None = None


def test_renderer_renders_managed_file_reference_to_input_file_data_url(tmp_path) -> None:
    managed_root = tmp_path / "files"
    managed_root.mkdir(parents=True, exist_ok=True)
    artifact_path = managed_root / "uploads" / "a.txt"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("hello", encoding="utf-8")
    renderer = RuntimeMessageRenderer(media_input_mode="responses", managed_files_root=str(managed_root))
    state = AgentState(
        messages=[
            AgentMessage(
                role="user",
                content=[
                    MessagePart(
                        type="file",
                        source={"type": "managed_file", "path": "uploads/a.txt"},
                        mime="text/plain",
                        filename="a.txt",
                    )
                ],
            )
        ]
    )

    rendered_messages = renderer.render_messages(state)

    user_content = rendered_messages[0]["content"]
    assert isinstance(user_content, list)
    assert user_content[0]["type"] == "input_file"
    assert user_content[0]["filename"] == "a.txt"
    assert user_content[0]["file_data"].startswith("data:text/plain;base64,")


def test_renderer_maps_provider_tool_calls_to_assistant_metadata() -> None:
    renderer = RuntimeMessageRenderer(media_input_mode="responses")
    message = _FakeMessage(
        content="thinking",
        tool_calls=[_FakeToolCall(id="call-1", function={"name": "tool_a", "arguments": "{}"}, name="tool_a")],
    )

    agent_message = renderer.from_provider_assistant_tool_call_message(message)

    assert agent_message.role == "assistant"
    assert agent_message.content[0].text == "thinking"
    assert agent_message.metadata["tool_calls"][0]["id"] == "call-1"
    assert agent_message.metadata["tool_calls"][0]["name"] == "tool_a"
