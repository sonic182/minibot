# System Prompt Composition

There are **two different mechanisms** for adding something to the system prompt, and choosing
the wrong one is the most common mistake in this area. Read the decision rule before editing.

## Composition order

`PromptService.compose_system_prompt(channel)` — `app/handlers/services/prompt_service.py`.
Fragments are joined with blank lines in exactly this order:

| # | Fragment | Source | Conditional on |
|---|---|---|---|
| 0 | base system prompt | `prompts/main_agent_system.md` via `[llm].system_prompt_file` | always |
| 1 | **every** policy file | `load_policy_prompts` → `prompts/policies/*.md` | **nothing — all files, always** |
| 2 | specialist roster | `_specialist_roster_fragment` | `spawn_task` or `delegate` attached + registry non-empty |
| 3 | skill catalog | `_skill_catalog_fragment` | `activate_skill` attached |
| 4 | runtime capability status | `_capability_status_fragment` | always emitted; **contents** vary by attached tools |
| 5 | channel fragment | `load_channel_prompt` → `prompts/channels/<channel>.md` | the active channel |
| 6 | environment context | `app/environment_context.py` | non-empty |
| 7 | artifact hint | inline block in `compose_system_prompt` | `self_insert_artifact` attached |

Fragment 4 nests a further conditional block, `_task_worker_guidance_fragment`, gated
on `spawn_task` or `delegate` plus `list_tasks` / `cancel_task`.

## The decision rule

**Is the rule conditional on a tool being attached?**

- **No — it applies on every turn, every channel** → drop a file into `prompts/policies/`.
  Zero code change. Loaded by glob in sorted order (`_load_policy_prompts_cached` in `shared/prompt_loader.py`).

- **No — but it is specific to one channel** → `prompts/channels/<channel>.md`. Keyed by
  `ChannelMessage.channel`; the name is sanitized to alnum / `_` / `-` and silently ignored
  otherwise (`_load_channel_prompt_cached` in `shared/prompt_loader.py`). Zero code change.

- **Yes — it must only appear when a given tool is attached** → add a gated
  `_<name>_fragment()` method to `PromptService` and call it from `compose_system_prompt`.
  Use `_skill_catalog_fragment` as the template: it builds a tool-name set from
  `self._tools`, returns `""` when the gating tool is absent, and the caller appends only a
  non-empty result.

### The trap

Files in `prompts/policies/` are **not** tool-gated. `load_policy_prompts` globs the directory
and concatenates everything it finds; nothing inspects the attached tools.

`prompts/policies/graph.md` opens with "Graph memory policy (applies when a `graph` tool is
attached)". That sentence is **prose addressed to the model**, not a condition the loader
evaluates. The fragment ships in every system prompt on every channel, including runs where the
`graph` tool is disabled. It works only because the model is expected to no-op when the tool is
absent.

So: writing "(applies when X is attached)" at the top of a policy file does **not** gate it.
If the rule would be actively misleading with the tool absent, it belongs in `prompt_service.py`,
not in `policies/`.

## Caching

All three loaders in `shared/prompt_loader.py` — `load_channel_prompt`, `load_policy_prompts`,
`load_compact_prompt` — read through `@lru_cache`'d `_load_*_cached` helpers keyed on `prompts_dir`.

**Prompt files are read once per process.** Editing a prompt file requires a restart. This is
the opposite of skills, which hot-reload on an mtime/size fingerprint
(`SkillRegistry.refresh_if_stale` in `app/skill_registry.py`). Do not tell a user their prompt edit is live until the daemon
has been restarted.

## Related surfaces

- **Compaction**: `prompts/compact.md`, appended after the base prompt when a turn triggers
  compaction; falls back to a built-in one-liner if the file is missing
  (`prompt_service.compact_system_prompt`).
- **Specialist prompts** do not go through this path at all — the Markdown body of
  `agents/<name>.md` *is* the specialist system prompt.
- **Worker prompts** get `_WORKER_SYSTEM_PROMPT_SUFFIX` (`app/tasks/worker.py`).
- **`prompts_dir`** is `[llm].prompts_dir`, default `./prompts`. Both Docker and systemd
  deployments include the directory.

When you add or remove a file under `prompts/`, update `docs/prompts.rst` — it enumerates the
shipped tree file by file and drifts easily.
