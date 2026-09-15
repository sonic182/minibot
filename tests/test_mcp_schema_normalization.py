from __future__ import annotations

from minibot.llm.tools.mcp_bridge import _normalize_schema


def test_keeps_a_plain_schema_untouched() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string", "description": "a name"}}}

    assert _normalize_schema(schema) == schema


def test_drops_the_keywords_sitting_next_to_a_ref() -> None:
    # What gmem's `relate` tool actually returns; the provider 400s on the $ref/description pair.
    schema = {
        "type": "object",
        "$defs": {"EntityInput": {"type": "object", "properties": {"name": {"type": "string"}}}},
        "properties": {
            "source": {"$ref": "#/$defs/EntityInput", "description": "Entity the relation points from."},
            "relation": {"type": "string", "description": "snake_case verb"},
        },
        "required": ["source", "relation"],
    }

    normalized = _normalize_schema(schema)

    assert normalized["properties"]["source"] == {"$ref": "#/$defs/EntityInput"}
    assert normalized["properties"]["relation"] == schema["properties"]["relation"]
    assert normalized["$defs"] == schema["$defs"]
    assert normalized["required"] == ["source", "relation"]


def test_reaches_refs_nested_in_arrays_and_subschemas() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/$defs/Node", "title": "Node"}},
            "either": {"anyOf": [{"$ref": "#/$defs/Node", "description": "x"}, {"type": "null"}]},
        },
    }

    normalized = _normalize_schema(schema)

    assert normalized["properties"]["items"]["items"] == {"$ref": "#/$defs/Node"}
    assert normalized["properties"]["either"]["anyOf"] == [{"$ref": "#/$defs/Node"}, {"type": "null"}]


def test_still_fills_in_a_missing_type() -> None:
    assert _normalize_schema({"properties": {}})["type"] == "object"
    assert _normalize_schema({}) == {"type": "object", "properties": {}, "additionalProperties": True}
