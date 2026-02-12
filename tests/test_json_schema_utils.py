from __future__ import annotations

from minibot.shared.json_schema import to_openai_strict_schema


def test_to_openai_strict_schema_enforces_required_and_additional_properties() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "url": {"type": "string"},
        },
        "required": ["name"],
    }

    strict = to_openai_strict_schema(schema)

    assert strict["required"] == ["name", "url"]
    assert strict["additionalProperties"] is False
    assert strict["properties"]["name"]["type"] == "string"
    assert strict["properties"]["url"]["type"] == ["string", "null"]


def test_to_openai_strict_schema_enforces_nested_items_object_rules() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items_list": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "offset": {"type": "integer"},
                        "length": {"type": "integer"},
                        "url": {"type": "string"},
                    },
                    "required": ["type", "offset", "length"],
                },
            }
        },
        "required": ["items_list"],
    }

    strict = to_openai_strict_schema(schema)
    items = strict["properties"]["items_list"]["items"]

    assert items["required"] == ["type", "offset", "length", "url"]
    assert items["additionalProperties"] is False
    assert items["properties"]["url"]["type"] == ["string", "null"]


def test_to_openai_strict_schema_makes_anyof_object_branches_strict() -> None:
    schema = {
        "type": "object",
        "properties": {
            "answer": {
                "anyOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string"},
                            "text": {"type": "string"},
                            "meta": {"type": "object"},
                        },
                        "required": ["kind", "text"],
                    },
                ]
            }
        },
        "required": ["answer"],
    }

    strict = to_openai_strict_schema(schema)
    object_branch = strict["properties"]["answer"]["anyOf"][1]

    assert object_branch["required"] == ["kind", "text", "meta"]
    assert object_branch["additionalProperties"] is False
    assert object_branch["properties"]["meta"]["type"] == ["object", "null"]


def test_to_openai_strict_schema_is_idempotent() -> None:
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "flag": {"type": ["boolean", "null"]},
        },
        "required": ["answer", "flag"],
        "additionalProperties": False,
    }

    once = to_openai_strict_schema(schema)
    twice = to_openai_strict_schema(once)

    assert once == twice
