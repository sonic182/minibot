Agents
======

Agent definitions live in ``./agents/*.md`` as markdown files with YAML frontmatter
followed by a system prompt body. The main agent discovers and delegates to specialists
at runtime.

Agent Definitions
-----------------

Minimal example:

.. code-block:: markdown

   ---
   name: workspace_manager_agent
   description: Handles workspace file operations
   mode: agent
   model_provider: openai_responses
   model: gpt-5-mini
   temperature: 0.1
   tools_allow:
     - filesystem
     - glob_files
     - read_file
     - self_insert_artifact
   ---

   You manage files in the workspace safely and precisely.

Frontmatter fields: ``name``, ``description``, ``mode`` (always ``"agent"``), ``enabled``
(default ``true``), ``model_provider``, ``model``, ``temperature``, ``max_new_tokens``,
``reasoning_effort``, ``max_tool_iterations``, ``tools_allow``, ``tools_deny``, ``mcp_servers``.

Tool Scoping
------------

``tools_allow`` and ``tools_deny`` are mutually exclusive. Wildcards (``fnmatch``) are supported:

- ``tools_allow: ["mcp_playwright-cli__*"]``
- ``tools_deny: ["mcp_playwright-cli__browser_close"]``

Behavior rules:

- If neither is set, local (non-MCP) tools are not exposed to the agent.
- ``mcp_servers`` limits MCP tools to the listed server names; tools from other servers are excluded.
- In ``tools_allow`` mode: allowed local tools + allowed MCP-server tools are exposed.
- In ``tools_deny`` mode: all local tools except denied + allowed MCP-server tools are exposed.
- A local-only agent: use ``tools_deny: ["mcp*"]`` with no ``mcp_servers``.

Main-agent tool policy is set under ``[orchestration.main_agent]``:

.. code-block:: toml

   [orchestration.main_agent]
   tools_allow = ["memory", "schedule", "http_request"]

``tool_ownership_mode`` under ``[orchestration]`` controls sharing:

- ``shared`` (default) — all agents share tools.
- ``exclusive`` — specialist-owned tools are removed from the main agent.
- ``exclusive_mcp`` — only specialist-owned MCP tools are removed from the main agent.

``delegated_tool_call_policy``:

- ``auto`` (default) — requires at least one tool call when the agent has scoped tools.
- ``always`` — requires a tool call for every delegation.
- ``never`` — disables enforcement.

Assigning an MCP server to an agent:

.. code-block:: markdown

   ---
   name: browser_agent
   description: Browser automation specialist
   mode: agent
   model_provider: openai_responses
   model: gpt-5-mini
   mcp_servers:
     - playwright-cli
   ---

   Use browser tools to navigate, inspect, and extract results.

Agent Skills
------------

Skills are reusable instruction packs the model loads on demand via ``list_skills`` /
``activate_skill``. Each skill is a directory containing a ``SKILL.md`` file.

Skill file format
~~~~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: my-skill
   description: One-line summary shown in the catalog.
   enabled: true
   ---

   # My Skill

   Full instructions here...

Frontmatter fields: ``name`` (required), ``description`` (optional), ``enabled`` (default ``true``).

Runtime behavior
~~~~~~~~~~~~~~~~

- ``list_skills`` rescans skill directories on demand — new skills are picked up without restarting.
- ``activate_skill`` requires the exact name returned by ``list_skills``.
- Set ``tools.skills.preload_catalog = true`` to embed a names/descriptions snapshot in the system prompt.

Discovery paths
~~~~~~~~~~~~~~~

When ``tools.skills.paths`` is empty (the default), MiniBot scans these locations in priority order:

.. list-table::
   :header-rows: 1

   * - Priority
     - Path
   * - 1 (highest)
     - ``./.agents/skills/``
   * - 2
     - ``./.claude/skills/``
   * - 3
     - ``~/.agents/skills/``
   * - 4 (lowest)
     - ``~/.claude/skills/``

Recommended setup: ``./skills``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: toml

   [tools.skills]
   enabled = true
   preload_catalog = false
   paths = ["./skills"]

Then place one subdirectory per skill::

   skills/
     my-skill/
       SKILL.md
     another-skill/
       SKILL.md

Setting ``paths`` to a non-empty list **replaces** all default locations entirely.
To disable skill support: ``enabled = false``.

OpenRouter Custom Params per Agent
-----------------------------------

For agents running on OpenRouter, override provider-routing params in frontmatter using
``openrouter_provider_<field_name>`` keys:

.. code-block:: markdown

   ---
   name: browser_agent
   description: Browser automation specialist
   mode: agent
   model_provider: openrouter
   model: x-ai/grok-4.1-fast
   openrouter_provider_only:
     - openai
     - anthropic
   openrouter_provider_sort: price
   openrouter_provider_allow_fallbacks: true
   ---

   Use browser tools to navigate, inspect, and extract results.

Supported keys mirror ``[llm.openrouter.provider]`` fields (``only``, ``sort``, ``order``,
``allow_fallbacks``, ``max_price``, etc.). Agent-level values override global provider config
for matching fields. Keep credentials in ``[providers.openrouter]`` — never in agent files.

Suggested Model Presets
-----------------------

- ``openai_responses``: ``gpt-5-mini`` with ``reasoning_effort = "medium"`` — solid quality/cost balance.
- ``openrouter``: ``x-ai/grok-4.1-fast`` with medium reasoning effort — comparable balance.
