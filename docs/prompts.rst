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
  (loaded in sorted order).
- **Composition order**: base prompt → policy fragments → channel fragment → environment
  context → tool safety addenda.
- **Prompts directory**: configure root folder with ``llm.prompts_dir`` (default: ``./prompts``).

Editing the System Prompt
--------------------------

1. Edit ``prompts/main_agent_system.md``.
2. Review for content, security, tone, and absence of secrets.
3. Commit with a descriptive message.
4. Deploy via Docker or systemd — both setups include the ``prompts/`` directory automatically.
