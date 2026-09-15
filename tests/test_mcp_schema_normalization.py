from __future__ import annotations

from minibot.llm.tools.mcp_bridge import _normalize_schema


def test_keeps_a_plain_schema_untouched() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string", "description": "a name"}}}

    assert _normalize_schema(schema) == schema


def test_wraps_ref_and_preserves_sibling_keywords() -> None:
    schema = {
        "type": "object",
        "$defs": {"EntityInput": {"type": "object", "properties": {"name": {"type": "string"}}}},
        "properties": {
            "source": {
                "$ref": "#/$defs/EntityInput",
                "description": "Entity the relation points from.",
                "minProperties": 1,
            },
            "relation": {"type": "string", "description": "snake_case verb"},
        },
        "required": ["source", "relation"],
    }

    normalized = _normalize_schema(schema)

    assert normalized["properties"]["source"] == {
        "allOf": [{"$ref": "#/$defs/EntityInput"}],
        "description": "Entity the relation points from.",
        "minProperties": 1,
    }
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

    assert normalized["properties"]["items"]["items"] == {
        "allOf": [{"$ref": "#/$defs/Node"}],
        "title": "Node",
    }
    assert normalized["properties"]["either"]["anyOf"] == [
        {"allOf": [{"$ref": "#/$defs/Node"}], "description": "x"},
        {"type": "null"},
    ]


def test_still_fills_in_a_missing_type() -> None:
    assert _normalize_schema({"properties": {}})["type"] == "object"
    assert _normalize_schema({}) == {"type": "object", "properties": {}, "additionalProperties": True}
