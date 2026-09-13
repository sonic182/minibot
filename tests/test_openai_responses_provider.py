from minibot.llm.providers.openai_responses import PatchedOpenAIResponsesProvider


def _provider() -> PatchedOpenAIResponsesProvider:
    return PatchedOpenAIResponsesProvider(api_key="test-key")


def test_messages_to_input_replays_reasoning_item_before_function_call() -> None:
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}
    tool_call = {"id": "fc_1", "type": "function", "function": {"name": "current_datetime", "arguments": "{}"}}
    messages = [
        {"role": "user", "content": "what time is it?"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_details": [reasoning_item],
            "tool_calls": [tool_call],
        },
        {"type": "function_call_output", "call_id": "fc_1", "output": "2026-09-13"},
    ]

    result = PatchedOpenAIResponsesProvider._messages_to_input(_provider(), messages)

    types = [item.get("type") or item.get("role") for item in result]
    assert types == ["user", "reasoning", "function_call", "function_call_output"]
    assert result[1] == reasoning_item
    assert result[2]["call_id"] == "fc_1"


def test_messages_to_input_matches_base_behavior_without_reasoning_details() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "fc_2", "type": "function", "function": {"name": "noop", "arguments": "{}"}}],
        }
    ]

    result = PatchedOpenAIResponsesProvider._messages_to_input(_provider(), messages)

    assert [item.get("type") for item in result] == ["function_call"]
