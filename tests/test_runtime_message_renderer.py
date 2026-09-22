from __future__ import annotations

from types import SimpleNamespace

from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.llm.services.runtime_message_renderer import RuntimeMessageRenderer
from tests.fixtures.llm.fakes import FakeMessage as _FakeMessage
from tests.fixtures.llm.fakes import FakeToolCall as _FakeToolCall


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


def test_renderer_replays_structured_reasoning_but_never_the_plain_text() -> None:
    renderer = RuntimeMessageRenderer(media_input_mode="chat_completions")
    state = AgentState(
        messages=[
            AgentMessage(
                role="assistant",
                content=[MessagePart(type="text", text="a")],
                metadata={"reasoning": "let me think", "had_reasoning_context": True},
            ),
            AgentMessage(
                role="assistant",
                content=[MessagePart(type="text", text="b")],
                metadata={
                    "reasoning_details": [{"type": "reasoning", "id": "rs_1", "encrypted_content": "x"}],
                    "had_reasoning_context": True,
                },
            ),
        ]
    )

    text_only, structured = renderer.render_messages(state)

    # Fireworks/DeepSeek reject any extra field on messages[*]; the text is display-only anyway.
    assert "reasoning" not in text_only
    # The Responses API rebuilds its `rs_` item from this, and rejects a function_call without it.
    assert structured["reasoning_details"] == [{"type": "reasoning", "id": "rs_1", "encrypted_content": "x"}]
    assert "reasoning" not in structured


def test_renderer_echoes_reasoning_content_under_its_own_key() -> None:
    renderer = RuntimeMessageRenderer(media_input_mode="chat_completions")
    state = AgentState(
        messages=[
            AgentMessage(
                role="assistant",
                content=[MessagePart(type="text", text="a")],
                metadata={"reasoning": "let me think", "reasoning_key": "reasoning_content"},
            )
        ]
    )

    (rendered,) = renderer.render_messages(state)

    assert rendered["reasoning_content"] == "let me think"
    assert "reasoning" not in rendered


def test_provider_message_records_the_reasoning_key() -> None:
    renderer = RuntimeMessageRenderer(media_input_mode="chat_completions")
    message = SimpleNamespace(content="a", reasoning_content="deep thoughts")

    agent_message = renderer.from_provider_assistant_message(message)

    assert agent_message.metadata["reasoning_key"] == "reasoning_content"


def test_renderer_replays_plain_reasoning_text_when_enabled() -> None:
    renderer = RuntimeMessageRenderer(media_input_mode="chat_completions", replay_reasoning_text=True)
    state = AgentState(
        messages=[
            AgentMessage(
                role="assistant",
                content=[MessagePart(type="text", text="a")],
                metadata={"reasoning": "let me think", "had_reasoning_context": True},
            )
        ]
    )

    (rendered,) = renderer.render_messages(state)

    assert rendered["reasoning"] == "let me think"
