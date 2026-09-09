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
| `mb.entrypoint` | `"daemon"` or `"console"` — see the channel note below |
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
- **Bundled extensions load first.** `minibot.extensions.*` (Telegram today) registers
  ahead of anything in `[extensions] modules`, through this exact API.

## Channel extensions

A channel reads its own `[channels.<name>]` section — anything that isn't `telegram` is
passed through verbatim:

```python
def register(mb):
    if mb.entrypoint != "daemon":
        return
    config = mb.settings.channels.section("slack")
    if not config.get("app_token"):
        return
    mb.add_service(SlackService(config, mb.event_bus))
```

Two rules, both load-bearing:

- **Check `mb.entrypoint`.** `minibot console` runs a single channel; a second one there
  would answer real users from an interactive session.
- **Decide in `register()`, not `start()`.** A service that subscribes to the bus in
  `__init__` but only drains in `start()` will fill its 128-slot queue and stall every
  publish. Don't construct what you won't start.

`minibot/extensions/telegram.py` is the worked example.
