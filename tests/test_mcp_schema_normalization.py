from __future__ import annotations

from minibot.llm.tools.mcp_bridge import _normalize_schema


def test_keeps_a_plain_schema_untouched() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string", "description": "a name"}}}

    assert _normalize_schema(schema) == schema


def test_drops_property_names_but_keeps_a_property_called_that() -> None:
    schema = {
        "type": "object",
        "properties": {
            "webhook": {"type": "object", "propertyNames": {"type": "string"}, "additionalProperties": {}},
            "propertyNames": {"type": "string"},
        },
        "$defs": {"Headers": {"type": "object", "propertyNames": {"type": "string"}}},
    }

    normalized = _normalize_schema(schema)

    assert normalized["properties"]["webhook"] == {"type": "object", "additionalProperties": {}}
    assert normalized["properties"]["propertyNames"] == {"type": "string"}
    assert normalized["$defs"]["Headers"] == {"type": "object"}


def test_drops_property_count_limits_but_keeps_a_property_called_that() -> None:
    schema = {
        "type": "object",
        "properties": {
            "attributes": {
                "type": "object",
                "minProperties": 1,
                "properties": {
                    "name": {"type": "string"},
                    "tags": {"type": "object", "maxProperties": 5, "additionalProperties": {"type": "string"}},
                },
            },
            "minProperties": {"type": "integer"},
        },
    }

    normalized = _normalize_schema(schema)

    assert normalized["properties"]["attributes"] == {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "tags": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    assert normalized["properties"]["minProperties"] == {"type": "integer"}


def test_drops_string_formats_openai_rejects_and_keeps_the_supported_ones() -> None:
    schema = {
        "type": "object",
        "properties": {
            "mediaUrl": {"anyOf": [{"type": "string", "format": "uri"}, {"type": "null"}]},
            "when": {"type": "string", "format": "date-time"},
            "format": {"type": "string"},
            "opts": {"type": "object", "default": {"format": "uri"}},
        },
    }

    normalized = _normalize_schema(schema)

    assert normalized["properties"]["opts"]["default"] == {"format": "uri"}
    assert normalized["properties"]["mediaUrl"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert normalized["properties"]["when"] == {"type": "string", "format": "date-time"}
    assert normalized["properties"]["format"] == {"type": "string"}


def _has_allof(value: object) -> bool:
    if isinstance(value, dict):
        return "allOf" in value or any(_has_allof(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_allof(item) for item in value)
    return False


def test_merges_ref_and_sibling_keywords_instead_of_allof() -> None:
    # Regression: the mcp_gmem MCP server's `relate` tool has this exact shape for `source`, and
    # OpenAI's function-calling schema validation rejects `allOf` outright (400
    # invalid_function_parameters, "'allOf' is not permitted"), which broke every turn while the
    # tool was in the catalog. Merging the `$defs` target inline avoids `allOf` entirely, so no
    # provider needs to be special-cased.
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
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "description": "Entity the relation points from.",
    }
    assert normalized["properties"]["relation"] == schema["properties"]["relation"]
    assert normalized["$defs"] == schema["$defs"]
    assert normalized["required"] == ["source", "relation"]
    assert not _has_allof(normalized)


def test_reaches_refs_nested_in_arrays_and_subschemas() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/$defs/Node", "title": "Node"}},
            "either": {"anyOf": [{"$ref": "#/$defs/Node", "description": "x"}, {"type": "null"}]},
        },
    }

    normalized = _normalize_schema(schema)

    # No $defs.Node exists here, so the ref can't resolve; the sibling keywords are still kept
    # rather than dropped, and no allOf/$ref survives into the provider-facing schema either way.
    assert normalized["properties"]["items"]["items"] == {"title": "Node"}
    assert normalized["properties"]["either"]["anyOf"] == [{"description": "x"}, {"type": "null"}]
    assert not _has_allof(normalized)


def test_bare_ref_without_siblings_is_left_untouched() -> None:
    # No siblings to merge, so the ref stays a ref: also what stops a self-referencing $defs
    # entry (a tree/graph node schema) from inlining itself forever.
    schema = {
        "type": "object",
        "$defs": {"Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}}},
        "properties": {"root": {"$ref": "#/$defs/Node"}},
    }

    normalized = _normalize_schema(schema)

    assert normalized["properties"]["root"] == {"$ref": "#/$defs/Node"}
    assert normalized["$defs"]["Node"]["properties"]["child"] == {"$ref": "#/$defs/Node"}


def test_still_fills_in_a_missing_type() -> None:
    assert _normalize_schema({"properties": {}})["type"] == "object"
    assert _normalize_schema({}) == {"type": "object", "properties": {}, "additionalProperties": True}
