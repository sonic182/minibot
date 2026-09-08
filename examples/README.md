# Minibot extension examples

`minibot_ext_demo.py` is a working extension: it contributes a `demo_greet` tool and
subscribes to `TurnCompletedEvent`.

## Running it

Extensions are resolved by normal Python import, so anything importable works — a
pip-installed package, or a local file on `PYTHONPATH` as here.

```toml
# config.toml
[extensions]
modules = ["minibot_ext_demo"]

[extensions.config.minibot_ext_demo]
greeting = "hola"
```

```bash
PYTHONPATH=examples poetry run minibot console --once "Greet Ana with the demo tool."
```

## Writing your own

Expose a module-level `register(mb)`. The `mb` object (`ExtensionContext`) gives you:

| Field / method | What it is |
| --- | --- |
| `mb.config` | your `[extensions.config.<module>]` slice, as a dict |
| `mb.settings` | the full validated `Settings`, read-only |
| `mb.event_bus` | the event bus, if you need to publish |
| `mb.logger` | a logger namespaced to your extension |
| `mb.add_tool(binding)` | contribute one or more `ToolBinding`s |
| `mb.on(EventType, handler)` | subscribe an `async def handler(event)` |
| `mb.add_service(service)` | register something with `start()` / `stop()` |

Notes:

- **Load failures are fatal.** A module that can't be imported, has no `register`, or
  whose `register` raises will stop the process at boot. A silently missing tool is
  much harder to debug than a crash.
- **Tool names must be unique.** Colliding with a built-in tool raises at startup.
- **Event handlers are lossy subscribers.** If your handler is slow enough to fill its
  queue, events are dropped with a warning rather than stalling the bot. Don't do
  slow work inline — hand it to a task.
- A handler that raises is logged; it does not kill the subscription.
- Your tools automatically get `ToolCallEvent` emission and large-output spill, the
  same as built-in tools.
