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

Always enabled — no config flag required. Available to the main agent when specialist
definitions exist:

- ``fetch_agent_info`` — inspect a specialist's name, description, and system prompt.
- ``invoke_agent`` — run a specialist with a ``task`` and optional ``context``; returns
  the specialist's final text plus metadata (tokens, tool call count, attachments).

Delegation policy (whether tool use is required from the specialist) is controlled by
``[orchestration].delegated_tool_call_policy``.

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
     - filesystem
     - glob_files
     - read_file
     - self_insert_artifact
   ---

   You manage files in the workspace safely and precisely.

Frontmatter fields: ``name``, ``description``, ``mode`` (always ``"agent"``), ``enabled``
(default ``true``), ``model_provider``, ``model``, ``temperature``, ``max_new_tokens``,
``omit_temperature`` (send no temperature at all, for models that reject the parameter),
``reasoning_effort``, ``max_tool_iterations``, ``timeout_seconds`` (per-agent wall-clock budget, overrides
``orchestration.default_timeout_seconds``; values below 30 seconds use the 30-second minimum), ``tools_allow``,
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

``delegated_tool_call_policy``:

- ``auto`` (default) — requires at least one tool call when the agent has scoped tools.
- ``always`` — requires a tool call for every delegation.
- ``never`` — disables enforcement.

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
``tools_allow`` lists ``bash``, ``filesystem``, ``grep``, ``http_request``,
``pre_response``, and ``wait`` (no ``mcp_servers`` entry).

Agent Skills
------------

Skills are reusable instruction packs the model loads on demand, and they are shared with the
main agent rather than being an orchestration feature. A specialist can be pointed at one the
same way the main agent is — the ``browser_agent`` above is driven by a skill rather than an
MCP server.

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

The frontmatter above is the agent's default, not a fixed binding. ``invoke_agent`` and
``spawn_task`` each accept ``model_provider``, ``model`` and ``reasoning_effort`` for a single call,
so the main agent can honour a request like *"run that on the deepseek subagent, high effort"*
without a config change or a restart. The specialist's prompt, tool scope and timeouts are unchanged;
only the target model is.

``fetch_agent_info`` is the discovery surface: besides the specialist's prompt it returns that
agent's own defaults and the providers that actually have credentials configured, with their
``api_format``, ``base_url`` and advisory ``models`` list (see :ref:`providers-aliases`). A provider that is
not on that list is refused — ``invoke_agent`` answers with ``error_code``
``provider_not_available``, and ``spawn_task`` rejects the call before queueing it — because a
provider without a key would otherwise answer with MiniBot's local echo fallback.

One caveat: token auto-config resolves each agent's context window and output cap at boot from the
model in its frontmatter. Those numbers do not transfer to another model, so a retargeted call runs
with the provider's default output cap and without mid-run transcript compaction. Put a model you
delegate to regularly in the agent file (or a copy of the agent) rather than overriding it on every
call.
