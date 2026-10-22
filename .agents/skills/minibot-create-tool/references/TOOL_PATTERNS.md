# Tool Patterns Reference

Templates for the files `SKILL.md` Step 3 generates. Read only the section for the chosen path.

## Extension tool

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

[extensions.config.my_tool]
# any extension settings with their defaults
```

Notes:
- Invalid arguments are rejected with `invalid_tool_arguments` to the model and never reach
  the handler.
- For `mb.add_tool`, build the binding with `build_extension_tool_binding(name=, description=,
  parameters=, handler=)` (`llm/tools/extension.py`). A description file kept next to the module
  loads with `load_tool_description("<tool_name>", package=__spec__.parent)` — see
  `extensions/tools/agent_management.py`.
- `examples/minibot_ext_demo.py` shows a tool plus an event subscriber.

## Core tool class

```python
from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.llm.tools.arg_utils import require_non_empty_str
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
        param1 = require_non_empty_str(payload, "param1")
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
        # context.owner_id, context.channel, context.chat_id, context.user_id, ...
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

## Schema helpers (`llm/tools/schema_utils.py`)

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

## `arg_utils` helpers (`llm/tools/arg_utils.py`)

All raise `ValueError` on invalid input; keyword-only arguments are after `*`.

- `require_non_empty_str(payload, key)` — reads `payload[key]`; returns the stripped `str`
- `optional_str(value, *, error_message=...)` — `str | None` (blank → `None`)
- `optional_int(value, *, field, min_value=None, ...)` — `int | None`
- `int_with_default(value, *, default, field, min_value=None, max_value=None, clamp_max=False, ...)` — `int`
- `optional_bool(value, *, default, error_message)` — `bool`; accepts `"true"/"1"/"yes"/"on"` and their negatives
- `require_owner(context)` / `require_channel(context)` — the `ToolContext` field or `ValueError`

## Description file (`.txt`, core path)

`minibot/llm/tools/<tool_name>.txt`, one per tool name, read by `load_tool_description`. Plain
text, no markdown. Three parts:
1. One-sentence summary.
2. Usage guidance — when to call, when NOT to call, preconditions.
3. What the tool returns.

Example (`minibot/llm/tools/crypto_price.txt`):
```
Fetch the current price of a cryptocurrency by symbol.

Use only when the user explicitly asks for a crypto price. Do not call during general conversation.

Returns symbol, price in USD, and timestamp of the last update.
```

## Config model (`config/schema.py`, core path)

Only when the tool needs settings. The docstring is rendered into `docs/config.rst`:

```python
class MyToolConfig(BaseModel):
    """My tool settings. TOML section: ``[tools.my_tool]``

    - ``enabled`` — attach the ``my_tool_name`` tool (default: ``false``).
    - ``timeout_seconds`` — request timeout (default: ``30``).
    """

    enabled: bool = False
    timeout_seconds: PositiveInt = 30


class ToolsConfig(BaseModel):
    ...
    my_tool: MyToolConfig = MyToolConfig()
```

```toml
[tools.my_tool]
enabled = false
timeout_seconds = 30
```

## Registration (core path)

### Bundled extension (default)

Add to the matching module under `minibot/extensions/tools/` (e.g. `utility.py`), reading
settings from `mb.settings`:

```python
from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.my_tool import MyTool


def register(mb: ExtensionContext) -> None:
    if mb.settings.tools.my_tool.enabled:
        mb.add_tool(MyTool(config=mb.settings.tools.my_tool).bindings())
```

A new bundled module must also be added to `_bundled_modules` (`app/extensions.py`).

### `build_enabled_tools` (lean-core exception)

Wire directly in `minibot/app/tool_factory.py` only when the tool needs state `ExtensionContext`
does not expose (conversation memory backend, skill registry, managed storage, config path) —
mirror how `build_enabled_tools` adds `ChatMemoryTool` or `build_skill_loader_bindings`. Being
always-on is not a reason: `CurrentTimeTool` and `CalculatorTool` are registered from
`extensions/tools/utility.py`.
