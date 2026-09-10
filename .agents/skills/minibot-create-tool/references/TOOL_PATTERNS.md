# Tool Patterns Reference

## Choose a path first

- **Extension (default)** — standalone `register(mb)` module, enabled via `[extensions].modules`.
  No factory/config/schema wiring. The docstring is the description; the first argument's
  pydantic model is the schema. Best for user-specific or experimental tools.
- **Core (bundled)** — tool class under `minibot/llm/tools/`, optional `[tools.<key>]` config,
  registered from a bundled extension module (`minibot/extensions/tools/`) or wired into
  `build_enabled_tools` for always-on tools.

## Extension tool (default path)

One file. Tool name = function name, description = docstring, schema = first argument's
pydantic model. Settings come from the extension's `[extensions.config.<module>]` slice.

```python
# my_tool.py — enable via [extensions].modules = ["my_tool"]
from pydantic import BaseModel, Field

from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.base import ToolContext


class WordCountArgs(BaseModel):
    text: str = Field(description="Text to count words in.")


def register(mb: ExtensionContext) -> None:
    @mb.tool
    async def word_count(args: WordCountArgs, context: ToolContext) -> dict[str, int]:
        """Count the words in a piece of text."""
        return {"words": len(args.text.split())}
```

```toml
[extensions]
modules = ["my_tool"]
```

Notes:
- Define pydantic models at module scope, not inside `register()` (postponed annotations
  cannot resolve them otherwise).
- Use `mb.add_tool(binding)` when you need a custom tool name, a hand-written schema, or a
  description loaded from a file.
- Invalid arguments produce `invalid_tool_arguments` to the model instead of reaching the handler.
- See `examples/minibot_ext_demo.py` for a tool plus an event subscriber.

## Core tool class

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

### Tool with constructor config and context

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

### Multi-tool class

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

## Config model pattern (schema.py, core path)

Only when the tool needs settings:

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

## Registration (core path)

`ToolFeature` / `_OPTIONAL_FEATURES` no longer exist in `llm/tools/factory.py`. Choose one:

### Bundled extension (default for optional tools)

Add to the matching module under `minibot/extensions/tools/` (e.g. `utility.py`), reading
settings from `mb.settings`:

```python
from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.my_tool import MyTool


def register(mb: ExtensionContext) -> None:
    if mb.settings.tools.my_tool.enabled:
        mb.add_tool(MyTool(config=mb.settings.tools.my_tool).bindings())
```

### `build_enabled_tools` (always-on tools only)

Wire directly in `minibot/llm/tools/factory.py` — see `CalculatorTool` or `SkillLoaderTool`
in `build_enabled_tools` for the pattern. These appear unconditionally (or gated by their
own config) next to the always-on core set.

## Description file (.txt, core path)

Extension tools use the docstring; core tools may use a `.txt` description via
`load_tool_description`. Plain text, no markdown. Three parts:
1. One-sentence summary.
2. Usage guidance — when to call, when NOT to call, preconditions.
3. What the tool returns.

Example (`descriptions/my_tool_name.txt`):
```
Fetch the current price of a cryptocurrency by symbol.

Use only when the user explicitly asks for a crypto price. Do not call during general conversation.

Returns symbol, price in USD, and timestamp of the last update.
```