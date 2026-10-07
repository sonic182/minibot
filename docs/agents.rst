Multi-Agent Orchestration
=========================

.. meta::
   :description: Minibot multi-agent orchestration: define specialist AI agents in Markdown, scope their tools, and delegate work at runtime.
   :keywords: multi-agent AI assistant, AI agents, agent delegation, tool scoping, Python AI agent

Minibot supports multi-agent orchestration with specialist AI agents, tool scoping, and
delegation. Agent definitions live in ``./agents/*.md`` as markdown files with YAML
frontmatter followed by a system prompt body. The main agent discovers and delegates to
specialists at runtime.

Delegation Tools
----------------

- ``fetch_agent_info`` — inspect a specialist's name, description, defaults and system prompt.
  Always enabled when specialist definitions exist.
- ``spawn_task`` — delegate to a specialist by exact ``agent_name``. Requires ``[tasks].enabled``
  (on by default; the SQLite backend needs no broker). See :doc:`tasks`.

Delegation is **asynchronous**: ``spawn_task`` returns a ``task_id`` and the main agent does not
wait inside the turn. By default the worker's answer, including any files it produced, is sent
straight to the conversation as a later message (fire and forget). A short notice with the agent,
the task id and the outcome is also recorded in the conversation history as a user message, so the
main agent knows the task finished and can call ``get_task`` for its result on the user's next
message. The worker's output itself is never written to the history. Pass
``continue_turn: true`` when the main agent needs the result to keep working, such as a second
step that depends on it or a comparison: the result then comes back as a new turn, saved to the
conversation history, and the main agent answers from it instead of the raw worker text. Failures
and timeouts come back the same way, so the agent can explain or retry. Worker output reaches the
model marked as untrusted data. One turn can start at most three continuing tasks, and a chain of
continuing tasks is limited to three levels. ``[tasks] continue_turn_default = true`` makes
continuing the default when the model leaves ``continue_turn`` unset; past the limits such a task
falls back to fire and forget instead of failing. ``[tasks] continue_turn_mode = "always"`` goes
further: the model's ``continue_turn`` is ignored and every task continues the turn, with the same
fallback past the limits. The default, ``"auto"``, leaves the choice to the model. A rate-limit retry
notice still goes straight to the user, and a continuing task that is cancelled does not report back. Use ``get_task`` to retrieve a
result, ``list_tasks`` to see what is running, and ``cancel_task`` to stop one.

Two switches decide whether any of this exists. ``[orchestration.specialists].enabled`` (default
``true``) is the one for specialists themselves: with it off, the roster, ``fetch_agent_info`` and
named delegation all disappear, and ``spawn_task`` still runs a generic worker. ``[tasks].enabled``
(default ``true``) is the execution backend, so the default follows it: with tasks off and the key
omitted the roster is disabled. An explicit ``[orchestration.specialists].enabled = true`` with
``[tasks].enabled = false`` is a config error rather than a roster nothing can act on. Turning
``[tasks]`` off turns multi-agent orchestration off as well: the specialist roster is dropped from
the system prompt along with the tool that could act on it.

Runtime Agent Management
------------------------

Runtime management is separate and off by default. ``[orchestration.agent_management]``
``reload = true`` exposes ``reload_agents`` so an owner can hand-edit ``agents/*.md`` and re-read
them without a restart; ``write = true`` additionally exposes controlled ``create_agent``,
``update_agent`` and ``delete_agent`` tools for model-authored definitions. Neither switch changes
how existing owner-authored agents run.

The four modes, all reachable by config alone:

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * - Mode
     - Config
     - What exists
   * - No specialists
     - ``[orchestration.specialists] enabled = false``
     - No roster, no ``fetch_agent_info``, no named delegation. ``spawn_task`` still runs a generic
       worker while ``[tasks]`` is on.
   * - Owner-only (default)
     - both management switches false
     - The roster and delegation, from files you write. No management tools or skill at all.
   * - Reload only
     - ``reload = true``
     - Adds ``reload_agents``: edit a file yourself, then re-read it without a restart.
   * - Managed agents
     - ``write = true``
     - Adds ``create_agent``/``update_agent``/``delete_agent``, the bundled ``create-agent`` skill,
       and a managed definition directory the model may write to.

A managed definition is never more powerful than the ceiling in
``[orchestration.agent_management]``. It may not use ``tools_deny``, must grant exact tool names,
claims MCP servers through ``mcp_servers``, and may only target a provider the owner listed. Owner
files are not subject to the ceiling, so the two trust levels stay distinct. The loader records
which directory each definition was read from; a symlink in the managed directory remains managed,
including when the worker applies model overrides. Creating or updating a managed definition whose
name collides with an enabled owner-authored agent is rejected before writing. See :doc:`config` for
the field reference.

Runtime definition reads and managed writes run off the event loop. Reload prepares and validates
candidate definitions in a worker thread, then applies the roster and tool changes on the event-loop
thread. Cancelling a managed write waits for its filesystem operation to finish before releasing
the management lock; cancellation does not roll back a completed write.

That ceiling bounds what an agent may be *told* to do; it is not a sandbox. A managed agent granted
``bash`` or the managed-file tools can reach anything the daemon's OS user can, exactly as an owner-authored
one can. See :doc:`security`.

Main-agent visibility is a separate thing from the managed ceiling. ``[orchestration.main_agent]``
and ``tool_ownership_mode`` decide what the *main* agent sees; they never widen or narrow what a
specialist may be granted, and a specialist may deliberately own a tool the main agent is denied.

Agent Definitions
-----------------

Minimal example:

.. code-block:: markdown

   ---
   name: workspace_manager_agent
   description: Handles workspace file operations
   mode: agent
   model_provider: openai_responses
   model: gpt-5.6-luna
   temperature: 0.1
   tools_allow:
     - list_files
     - file_info
     - write_file
     - move_file
     - delete_file
     - send_file
     - glob_files
     - read_file
     - self_insert_artifact
   ---

   You manage files in the workspace safely and precisely.

Frontmatter fields: ``name``, ``description``, ``mode`` (always ``"agent"``), ``enabled``
(default ``true``), ``model_provider``, ``model``, ``temperature``, ``max_new_tokens``,
``omit_temperature`` (send no temperature at all, for models that reject the parameter),
``reasoning_effort``, ``max_tool_iterations``, ``timeout_seconds`` (per-agent wall-clock budget; it is the
default when a ``spawn_task`` call names no ``timeout_seconds`` of its own, capped by
``[tasks].worker_timeout_seconds``), ``tools_allow``,
``tools_deny``, ``mcp_servers``.

Tool Scoping
------------

``tools_allow`` and ``tools_deny`` are mutually exclusive. Wildcards (``fnmatch``) are supported:

- ``tools_allow: ["mcp_dice_cli__*"]``
- ``tools_deny: ["mcp_dice_cli__roll_dice"]``

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

``shared_mcp_servers`` lists MCP servers that stay visible to the main agent in both exclusive modes,
even when a specialist also claims them — for example a read-only web search server that both the
main agent and a research specialist use. The ``[orchestration.main_agent]`` ``tools_allow`` and
``tools_deny`` policy still applies first, so ``tools_deny = ["mcp*"]`` hides a shared server too:

.. code-block:: toml

   [orchestration]
   tool_ownership_mode = "exclusive_mcp"
   shared_mcp_servers = ["exa"]

Assigning an MCP server to an agent:

.. code-block:: markdown

   ---
   name: dice_agent
   description: Dice-rolling specialist
   mode: agent
   model_provider: openai_responses
   model: gpt-5.6-luna
   mcp_servers:
     - dice_cli
   ---

   Use the dice tools to roll and report results.

Browser Automation
-------------------

Browser automation is not MCP-based: the specialist drives the ``playwright-cli``
binary directly through the ``bash`` tool, guided by a skill rather than a remote
MCP server. See ``agents/browser_agent.md`` for the canonical setup — its
``tools_allow`` lists ``bash``, ``list_files``, ``read_file``, ``grep``, ``http_request``,
``pre_response``, and ``wait`` (no ``mcp_servers`` entry).

Agent Skills
------------

Skills are reusable instruction packs the model loads on demand, and they are shared with the
main agent rather than being an orchestration feature. The ``browser_agent`` above is driven by a
skill rather than an MCP server.

With ``tools_allow``, a specialist gets skill tools only when listed: ``activate_skill``, plus
``list_skills`` if it should discover skills on its own. A ``tools_deny`` specialist gets both
(and ``wait``, when enabled) unless it denies them. Unlike the main
agent, a specialist receives no skill catalog in its prompt, so without ``list_skills`` its prompt
must name the skill to activate. ``install_skill`` is never available to a specialist.

See :doc:`skills` for the format, discovery order, and configuration.

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
   model: x-ai/grok-4.3
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

Choosing a Model
----------------

Run ``minibot configure`` and it queries your configured provider for the models it currently
serves, then offers them as a list — which beats any set of names written down here, since those go
stale as providers retire and rename models. The model IDs in the examples above are illustrative
only; use whatever your provider lists.

``reasoning_effort`` is unset by default, which leaves the provider's own default in place. Set it
per agent when you want to steer that: higher for agents that plan multi-step work, lower for agents
that mostly call one tool and summarize.

Retargeting One Call
--------------------

The frontmatter above is the agent's default, not a fixed binding. ``spawn_task`` accepts
``model_provider``, ``model`` and ``reasoning_effort`` for a single task, so the main agent can
honour a request like *"run that on the deepseek subagent, high effort"* without a config change or
a restart. The specialist's prompt, tool scope and timeouts are unchanged; only the target model is.

``fetch_agent_info`` is the discovery surface: besides the specialist's prompt it returns that
agent's own defaults and the providers that actually have credentials configured, with their
``api_format``, ``base_url`` and advisory ``models`` list (see :ref:`providers-aliases`). A provider
that is not on that list is rejected before the task is queued, because a provider without a key
would otherwise answer with MiniBot's local echo fallback.

A retargeted task keeps its budget: the context window and output cap are re-derived from the target
model's own limits rather than inherited from the agent's frontmatter, so mid-run compaction stays
armed. A model that models.dev does not list has no known window and runs without compaction.
