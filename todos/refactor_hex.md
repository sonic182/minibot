# Hex refactor backlog

Remaining layer-boundary work from the hex review of `minibot/`, ordered by value over effort (best first).
Line references are as of the review; re-check before editing.

## Already done

- `ToolContext`, `ToolPayload`, `ToolHandler` moved to `minibot/core/tools.py`; `llm/tools/base.py` re-exports them and keeps `ToolBinding`.
- `session_id_for` / `session_identifier` moved from `shared/utils.py` to `core/channels.py`.
- `ExtensionContext.vault` / `load_extensions(vault=...)` typed against `core.secrets.SecretVault` instead of the concrete `Vault`.

## Open items

### 1. `FileStorage` protocol in `core` for the `llm` tools
- Effort: low to medium (type-only changes). Value: medium to high.
- 12 modules in `minibot/llm/tools/` import the concrete `LocalFileStorage`: `factory.py:7`, `http_client.py`, `grep.py`, `code_read.py`, `audio_transcription.py`, `audio_transcription_facade.py`, `bash.py`, `skill_loader.py`, `file_storage.py`, `rag_tools.py`, `output_spill.py`, `python_exec.py`. Most also import their adapter config class from `adapters.config.schema`.
- Add a `FileStorage` protocol (e.g. `core/files.py`) covering only what the tools call, type the tools against it, and keep `LocalFileStorage` as the adapter implementation.
- Breaks most of the `llm` -> `adapters` coupling without changing behavior.

### 2. `app/task_consumer_service.py` depends on concrete task adapters
- Effort: low to medium. Value: medium.
- `app/task_consumer_service.py:8-9` imports `TaskManager` and `SQLiteTaskStore`.
- Depend on `core.tasks.TaskRepository` (already exists) and a small protocol for what the consumer calls on the manager; inject both.
- Breaks one direction of the `app` <-> `adapters` cycle. Prerequisite for item 6.

### 3. `llm/tools/tasks.py` and `factory.py` import the concrete `TaskManager`
- Effort: medium. Value: medium.
- `llm/tools/tasks.py:11`, `llm/tools/factory.py:24` -> type against `core.tasks.TaskProducer` (or a slightly wider protocol).
- Check first whether `TaskTools` uses `TaskManager` methods beyond the producer API; if so the protocol must cover them.

### 4. Remove the service locator from `app/dispatcher.py`
- Effort: medium. Value: medium (testability, dependency direction).
- `app/dispatcher.py:7,52-113` calls about 10 `AppContainer.get_*()` methods (pending turn store, settings, memory backend, agent registry, LLM factory, skill registry, config path, extensions, LLM client).
- Inject them through the `Dispatcher` constructor; `daemon.py` / `console.py` (composition roots) do the `AppContainer` lookups.

### 5. Channel capability flags instead of hardcoded Telegram branches in `app`
- Effort: medium. Value: medium.
- `app/tool_approval.py:93` (`channel != "telegram"`), `app/handlers/services/turn_service.py:466` (`_set_reply_target`), `app/handlers/services/prompt_service.py:224-233` (Telegram formatting-repair prompt), `app/handlers/services/input_service.py:52` ("Telegram reply context").
- Add capability flags in `core.channels` (supports tool approval, reply targets, formatted-parse-error retry) and let the Telegram adapter supply the Telegram-specific wording.
- These branch on the structured `channel` field, so they do not break the no-text-classification rule; the issue is that `app` knows about one transport.
- Minor related: `llm/tools/filesystem.txt:15` mentions Telegram/Console behavior in the tool description.

### 6. Move task orchestration out of `adapters/tasks`
- Effort: high (about 1,300 lines). Value: high. Do last; needs items 1, 2 and 3 first.
- `adapters/tasks/manager.py` (`TaskManager`, delegation budgets, retry-after handling, continuation and status policy) and `adapters/tasks/worker.py` (builds `AgentRuntime`, imports about 15 `app` modules and about 15 `llm.tools.*` classes) are orchestration, not adapters.
- `worker.py:315-326` rebuilds the tool set by hand, duplicating `llm/tools/factory.py`.
- Move both to `minibot/app/tasks/`; keep `sqlite_store.py` and `retention.py` in adapters behind `core.tasks.TaskRepository`.

### 7. `llm_async.Tool` still imported in `app/extensions.py`
- Effort: low to medium. Value: low.
- `app/extensions.py:12` imports the provider SDK type only because `mb.tool` (around line 145) constructs `Tool(...)` for the `ToolBinding`.
- Move that construction behind a helper in `minibot/llm/tools/` and call it from `app/extensions.py`.

### 8. `app` still imports `ToolBinding` and `LLMClient` from `llm`
- Effort: high. Value: medium.
- `ToolBinding.tool` is an `llm_async.models.Tool`, so `ToolBinding` cannot move to `core` as is. The `app` <-> `llm` cycle is reduced, not removed.
- Options: make the `core` side generic or a protocol over "a tool definition with name, description, parameters", or accept `app` -> `llm` as the allowed direction and forbid `llm` -> `app` imports instead (`llm/tools/{factory,tasks,scheduler,skill_installer,skill_loader,agent_info,tool_events,file_storage}.py` and others still import `app.*`).
- Decide the direction before doing more work here.

### 9. Config schema imported by `app`
- Effort: high. Value: low.
- Nine `app` modules import `adapters.config.schema` (`Settings` and section configs): `agent_definitions_loader`, `environment_context`, `token_limits_autoconfig`, `llm_client_factory`, `task_consumer_service`, `skill_registry`, `tool_capabilities`, `agent_policies`, `scheduler_service`, plus `extensions`.
- Mostly an ownership question. Recommendation: accept as a known exception (config is shared) unless the schema is moved to a neutral module.

## Not audited yet

- Bundled extensions `extensions/services/scheduler.py` (118 lines), `integrations/mcp.py` (105), `tools/memory.py` (98): they import `adapters.http` render helpers; confirm they are still thin `register(mb)` composition.
- Function bodies in `app/handlers/services/*` (about 1,900 lines) for persistence or business policy that belongs elsewhere; only imports were scanned.
- `shared/console_compat.py` (rich UI) and `shared/tool_call_display.py`: only imports were checked; both look UI-specific and may not be generic enough for `shared`.
- `core/agents.py` and `core/skills.py` import `pathlib.Path`; confirm they do no filesystem I/O.
