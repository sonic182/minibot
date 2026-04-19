# Tool Patterns Reference

## File layout

```
minibot/llm/tools/
├── <module>.py                  # tool class
└── descriptions/
    └── <tool_name>.txt          # one file per tool name
```

## Minimal single tool

```python
from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import strict_object, string_field


class MyTool:
    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="my_tool_name",
            description=load_tool_description("my_tool_name"),
            parameters=strict_object(
                properties={
                    "param1": string_field("What this param does."),
                },
                required=["param1"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        param1 = payload.get("param1")
        return {"result": param1}
```

## Tool with constructor config and context

```python
class MyTool:
    def __init__(self, config: MyToolConfig) -> None:
        self._config = config

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    async def _handle(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        # context.channel, context.chat_id, context.user_id, context.owner_id
        ...
```

## Multi-tool class

```python
class MyTool:
    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._foo_schema(), handler=self._foo),
            ToolBinding(tool=self._bar_schema(), handler=self._bar),
        ]

    def _foo_schema(self) -> Tool:
        return Tool(name="foo", description=load_tool_description("foo"), parameters=...)

    def _bar_schema(self) -> Tool:
        return Tool(name="bar", description=load_tool_description("bar"), parameters=...)

    async def _foo(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]: ...
    async def _bar(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]: ...
```

## Schema helpers (schema_utils.py)

| Helper | JSON type | Notes |
|---|---|---|
| `string_field(description)` | `string` | required string |
| `integer_field(minimum, description)` | `integer` | required int, optional min |
| `nullable_string(description)` | `["string","null"]` | optional string |
| `nullable_integer(minimum, description)` | `["integer","null"]` | optional int |
| `nullable_boolean(description)` | `["boolean","null"]` | optional bool |
| `empty_object_schema()` | `object {}` | no parameters |
| `strict_object(properties, required)` | `object` | always use this — sets `additionalProperties: false` |

Always wrap properties with `strict_object()`. Never build raw dicts for the top-level schema.

## arg_utils helpers

Use these to coerce/validate payload values inside handlers:

- `require_non_empty_str(value, field)` — raises `ValueError` if missing or blank
- `optional_str(value, field, ...)` — returns `str | None`
- `optional_int(value, field, min_value, ...)` — returns `int | None`
- `int_with_default(value, default, field, ...)` — returns `int`
- `optional_bool(value, field)` — returns `bool | None`

## Config model pattern (schema.py)

```python
class MyToolConfig(BaseModel):
    enabled: bool = False
    timeout_seconds: PositiveInt = 30
```

Add field to `ToolsConfig`:
```python
class ToolsConfig(BaseModel):
    ...
    my_tool: MyToolConfig = MyToolConfig()
```

## Factory builder pattern (factory.py)

```python
def _build_my_tool_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    return MyTool(config=context.settings.tools.my_tool).bindings()
```

```python
ToolFeature(
    key="my_tool",
    labels=("my_tool_name",),
    enabled_in_config=lambda settings: _tool_enabled(settings, "my_tool"),
    builder=_build_my_tool_feature,
),
```

If the builder needs an optional dependency that may be `None`, return `[]` early:
```python
def _build_my_tool_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    if context.some_dep is None:
        return []
    return MyTool(dep=context.some_dep, config=context.settings.tools.my_tool).bindings()
```

## Description file (.txt)

Plain text, no markdown. Three parts:
1. One-sentence summary.
2. Usage guidance — when to call, when NOT to call, preconditions.
3. What the tool returns.

Example (`descriptions/my_tool_name.txt`):
```
Fetch the current price of a cryptocurrency by symbol.

Use only when the user explicitly asks for a crypto price. Do not call during general conversation.

Returns symbol, price in USD, and timestamp of the last update.
```
