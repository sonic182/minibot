Prompt Packs
============

MiniBot composes the system prompt from a base file plus runtime fragments.

Base System Prompt
------------------

- **File-based (default)**: loaded from ``./prompts/main_agent_system.md``
  (configurable via ``llm.system_prompt_file``).
- **Inline fallback**: set ``llm.system_prompt_file = null`` (or empty string) to use
  ``llm.system_prompt`` instead.
- **Fail-fast**: if ``system_prompt_file`` is configured but the file is missing or empty,
  the daemon will fail at startup.

Runtime Fragments
-----------------

- **Channel-specific**: place channel fragments at ``prompts/channels/<channel>.md``
  (e.g. ``prompts/channels/telegram.md``).
- **Policy fragments**: add files under ``prompts/policies/*.md`` for cross-channel rules
  (loaded in sorted order). Every file in the directory is loaded **unconditionally** — the
  loader globs the directory and never inspects which tools are attached. Prompt text such as
  "applies when the ``graph`` tool is attached" is guidance addressed to the model, not a
  condition the loader evaluates. For a fragment that must appear only when a given tool is
  attached, add a gated ``_<name>_fragment()`` method to
  ``minibot/app/handlers/services/prompt_service.py`` instead (see
  ``_skill_catalog_fragment`` for the pattern).
- **Composition order**: base prompt → policy fragments → specialist roster → skill guidance
  (with a catalog snapshot when ``preload_catalog`` is on) → runtime capability status → channel
  fragment → environment context → tool safety addenda.
- **Prompts directory**: configure root folder with ``llm.prompts_dir`` (default: ``./prompts``).

Shipped prompt files
---------------------

The default ``./prompts`` tree ships seven files, each playing one role in the composed
prompt (or the compaction pass):

.. list-table::
   :header-rows: 1

   * - File
     - Role
   * - ``main_agent_system.md``
     - Base system prompt — persona and ground rules
   * - ``policies/delegation.md``
     - Cross-channel delegation policy
   * - ``policies/graph.md``
     - Cross-channel relation-graph vs durable-memory routing policy
   * - ``policies/tool_usage.md``
     - Cross-channel tool routing policy
   * - ``channels/telegram.md``
     - Telegram channel fragment
   * - ``channels/console.md``
     - Console channel fragment
   * - ``compact.md``
     - Memory-compaction pass instructions

``prompts/main_agent_system.md``
   The identity and ground rules: "You are Minibot, a self-hosted personal AI assistant." It
   covers privacy/secrets hygiene, directness, act-now-over-narrate execution, and the
   durable-memory workflow (search before create, update matching entries, persist only
   confirmed facts). Loaded as the base fragment via ``llm.system_prompt_file`` (default
   ``./prompts/main_agent_system.md``); startup fails fast if the file is missing or empty.

``prompts/policies/*.md``
   Cross-channel rules appended right after the base prompt, in alphabetical order,
   regardless of the active channel and regardless of which tools are attached.

   - ``delegation.md`` — keep trivial requests local; prefer ``invoke_agent`` for multi-step
     specialist work; use ``fetch_agent_info`` when the roster description is not enough;
     continue locally when delegation is unavailable or fails.
   - ``graph.md`` — decide between the relation ``graph`` and durable ``memory``: a fact naming
     two things is an edge and belongs in the graph. Note this fragment ships even when the
     ``graph`` tool is disabled; it relies on the model no-opping when the tool is absent.
   - ``tool_usage.md`` — route the model to the right tool: ``memory`` vs ``history``,
     ``apply_patch`` for existing-file edits vs ``filesystem`` for file management, reuse
     canonical path fields from tool output, absolute paths in yolo mode.

``prompts/channels/<channel>.md``
   Channel-specific formatting and capabilities, appended only when that channel is active.

   - ``telegram.md`` — enforce Telegram Bot API formatting: plain text, Telegram-compatible
     inline HTML, or Markdown (no web HTML, no JSON wrappers like ``{"answer": ...}``); when
     delegation results contain ``attachments``, send each with ``filesystem(action="send")``
     and confirm briefly.
   - ``console.md`` — text-only interface: always use ``kind="text"``, report file paths in
     the reply instead of sending files, and never call ``filesystem(action="send")``.

``prompts/compact.md``
   Instructions for the memory-compaction pass: produce an information-dense summary with
   labeled sections (Goals / Progress / Decisions / Constraints / Context / Next), retain
   relevant context over aggressive shortening, and drop chit-chat and tangents. It is
   concatenated after the base system prompt whenever a turn triggers compaction; if the
   file is missing, a built-in one-liner summary prompt is used instead.

Editing the System Prompt
--------------------------

1. Edit ``prompts/main_agent_system.md``.
2. Review for content, security, tone, and absence of secrets.
3. Commit with a descriptive message.
4. Deploy via Docker or systemd — both setups include the ``prompts/`` directory automatically.
