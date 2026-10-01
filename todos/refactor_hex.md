# Hex refactor backlog

Boundary work from the review of `minibot/`.

## Completed

- `ToolContext`, `ToolPayload`, and `ToolHandler` live in `minibot/core/tools.py`; `llm/tools/base.py` re-exports them and keeps `ToolBinding`.
- `session_id_for` and `session_identifier` live in `minibot/core/channels.py`.
- `ExtensionContext.vault` and `load_extensions(vault=...)` use `core.secrets.SecretVault`.
- LLM tools depend on the `core.files.FileStorage` protocol; `LocalFileStorage` remains an adapter implementation.
- The task consumer uses core task contracts for its repository, manager, and polling settings. Task tools use `TaskProducer`, `TaskRepository`, and `TaskManager` contracts.
- `Dispatcher` receives dependencies from daemon/console composition roots; it no longer uses `AppContainer` as a service locator.
- Telegram support for approval, reply targets, formatted-parse-error repair, and file delivery is supplied by `ChannelCapabilities` from the Telegram adapter.
- Task orchestration and worker execution live in `minibot/app/tasks/`; SQLite storage and retention remain adapters.
- Extension tool schema construction uses an LLM helper, so `app/extensions.py` does not import the provider SDK type.
- Dependency direction is `app` -> `llm`; `minibot.llm` has no imports from `minibot.app`.
- The LLM MCP bridge uses core MCP contracts, and tool-event publishing uses the core event publisher contract.
- Shared config schemas and environment expansion live in `minibot/config/`; old adapter paths are compatibility aliases.
- Config-path resolution is a neutral `minibot.config` helper; config loading and the Codex login CLI stay at the adapter edge.
- Terminal presentation helpers moved out of `shared` to the console adapter and LLM tool package.
- `minibot/core` has no imports from app, adapters, or LLM packages.
- Remaining `app` -> `adapters` imports are in daemon/console/task-worker composition roots.
- Handler-service bodies were checked for direct adapter/ORM/file access; none was found. Core agent and skill models use `Path` as data only.
- Scheduler, MCP, and memory extensions compose their adapters/services and register optional HTTP pages through the extension API.

## Remaining

### Share applicable tool construction with task workers

`app/tasks/worker.py` keeps a deliberately narrower tool set and does not load extensions. Factor reusable constructors from `app/tool_factory.py` where that preserves the worker's current visibility; do not enable main-agent-only tools for workers.
