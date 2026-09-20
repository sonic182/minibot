Multi-tenant deployments
========================

.. meta::
   :description: Why Minibot is single-owner by design, which state is shared across conversations, and how to run it safely for many users — for example a WhatsApp Cloud API support bot.
   :keywords: minibot multi-tenant, multi-user AI agent, per-tenant isolation, WhatsApp Cloud API bot, self-hosted AI support bot

MiniBot is built as a personal assistant for **exactly one owner**. This page explains what that means in practice, which state is shared between conversations, and how to deploy MiniBot for many unrelated users — the plan worth checking before you point a public channel at a running instance.

If you only ever serve one person (or one team acting as a single owner), you can ignore all of this. If you want to serve many distinct users — a support bot behind the WhatsApp Cloud API, for example — read the isolation options at the end before enabling anything beyond conversation history.

One owner, many conversations
------------------------------

Two identifiers matter, and they are easy to confuse:

- **``[runtime].owner_id``** (default ``"primary"``) is the owner of long-term data. It is a constant of the deployment: it is never derived from a message, a task payload, or any caller-supplied field, and it becomes ``ToolContext.owner_id`` for every tool call on every entrypoint. It owns key/value memory, the relation graph, scheduled jobs, tasks, and the RAG corpus.
- **The session id** is ``channel:chat_id``. Conversation history is scoped per session. One owner can have many sessions — a private Telegram chat, a group and the console keep independent history while sharing the one owner's long-term data.

So long-term state is **shared across every conversation under one process**, and only conversation history is separated. That is the whole model; there is no per-user identity layered on top of it.

See :doc:`config` for the ``owner_id`` setting and :doc:`architecture` for how a message is routed.

How the built-in channels scope today
-------------------------------------

- **Telegram** carries a real ``chat_id`` and ``user_id`` per conversation, so ``chat_history_*`` is isolated per chat and RAG scopes to the sender/chat.
- **Console** is a single local session.
- **Web chat** deliberately uses one shared ``web:1`` session for the whole server. Everyone who can reach ``/chat`` sees the same conversation, so it is **not** per-user even though it looks like a chat UI.

A multi-tenant channel you add (for example WhatsApp Cloud API) must supply a distinct ``chat_id`` per customer for conversation and RAG scoping to mean anything. It still cannot change ``owner_id``.

Tool-by-tool tenant safety
--------------------------

"Tenant-safe" here means: **can tenant A reach tenant B's data, the host, or the shared owner's resources through this tool?** For a multi-user deployment, assume each customer is a distinct ``chat_id``/``user_id`` under one process.

.. list-table::
   :header-rows: 1
   :widths: 12 20 22 12 34

   * - Group
     - Tool
     - State it can touch
     - Tenant-safe
     - Notes
   * - Stateless
     - ``calculate_expression``
     - none (pure)
     - **Yes**
     - Safe.
   * - Stateless
     - ``current_datetime``
     - none
     - **Yes**
     - Safe.
   * - Stateless
     - ``wait``
     - none (holds the turn)
     - **Yes**
     - Safe; bound it with ``max_milliseconds``.
   * - Stateless
     - ``transcribe_audio``
     - the caller's own attachment
     - **Yes**
     - Operates on the audio in the current turn.
   * - Conversation
     - ``chat_history_info``
     - history per ``channel:chat_id``
     - **Yes**
     - Isolated only if the channel gives each customer a distinct ``chat_id``.
   * - Conversation
     - ``chat_history_trim``
     - history per ``channel:chat_id``
     - **Yes**
     - Wipes only the caller's own session.
   * - RAG
     - ``rag_index``
     - vector store, tagged ``user_id`` / ``chat_id`` / ``agent_id``
     - **Conditional**
     - ``user_id`` and ``chat_id`` default to the turn's context and an explicit mismatched value is rejected; ``agent_id`` defaults to the global owner. Safe only if the channel supplies distinct per-tenant values.
   * - RAG
     - ``rag_search``
     - same
     - **Conditional**
     - Same scoping as ``rag_index``.
   * - RAG
     - ``rag_list_metadata``
     - same
     - **Conditional**
     - Same scoping.
   * - RAG
     - ``rag_delete``
     - same
     - **Conditional**
     - Same scoping; a tenant can delete only its own scope.
   * - Long-term memory
     - ``memory``
     - ``KeyValueMemory`` by ``owner_id``
     - **No**
     - All customers share one store. One can read or overwrite another's entries.
   * - Graph
     - ``graph``
     - graph store by ``owner_id``
     - **No**
     - Shared entity graph across every customer.
   * - Tasks
     - ``spawn_task``
     - task repository by ``owner_id``
     - **No**
     - One shared queue; spawns delegated runs with the owner's tools.
   * - Tasks
     - ``cancel_task``
     - task repository by ``owner_id``
     - **No**
     - Can cancel another customer's task.
   * - Tasks
     - ``list_tasks``
     - task repository by ``owner_id``
     - **No**
     - Lists every customer's tasks.
   * - Tasks
     - ``get_task``
     - task repository by ``owner_id``
     - **No**
     - Reads every customer's task and events.
   * - Scheduler
     - ``schedule``, ``schedule_prompt``
     - scheduler by ``owner_id``
     - **No**
     - A customer can schedule a prompt that later runs with the shared owner's privileges.
   * - Scheduler
     - ``cancel_scheduled_prompt``
     - scheduler by ``owner_id``
     - **No**
     - Can cancel another customer's job.
   * - Scheduler
     - ``list_scheduled_prompts``
     - scheduler by ``owner_id``
     - **No**
     - Lists every customer's jobs.
   * - Scheduler
     - ``delete_scheduled_prompt``
     - scheduler by ``owner_id``
     - **No**
     - Deletes any customer's job.
   * - Delegation
     - ``invoke_agent``
     - runs sub-agents as the owner
     - **No**
     - Full tool use under the shared owner; a tenant can pivot through it.
   * - Delegation
     - ``fetch_agent_info``
     - agent registry (global)
     - **Yes**
     - Read-only; reveals the agent catalog only.
   * - Files
     - ``filesystem``
     - a single ``root_dir``
     - **No**
     - No per-tenant namespace; one tenant can read, write or delete another's files.
   * - Files
     - ``glob_files``
     - a single ``root_dir``
     - **No**
     - Lists across all tenants.
   * - Files
     - ``read_file``
     - a single ``root_dir``
     - **No**
     - Reads across all tenants.
   * - Files
     - ``self_insert_artifact``
     - a single ``root_dir`` into the conversation
     - **No**
     - Pulls any tenant's file into the current chat.
   * - Files
     - ``code_read``
     - a single ``root_dir``
     - **No**
     - Reads across all tenants.
   * - Files
     - ``grep``
     - a single ``root_dir``
     - **No**
     - Greps across all tenants.
   * - Files
     - ``apply_patch``
     - a single ``root_dir``
     - **No**
     - Edits files shared by all tenants.
   * - Files
     - ``tool_output_spill``
     - a global subdirectory under ``root_dir``
     - **No**
     - Spilled tool output is readable by any tenant via ``read_file``.
   * - Execution
     - ``bash``
     - the host shell
     - **No**
     - Arbitrary host execution; a tenant compromises the whole process.
   * - Execution
     - ``python_execute``
     - the host Python runtime
     - **No**
     - Same as ``bash``.
   * - Execution
     - ``python_environment_info``
     - host and environment details
     - **No**
     - Leaks host details to every tenant.
   * - Network
     - ``http_request``
     - shared egress
     - **No**
     - Shared credentials and egress, plus SSRF into internal services or MiniBot's own admin routes.
   * - Skills
     - ``list_skills``
     - the global skill catalog
     - **Yes**
     - Read-only; leaks skill names only.
   * - Skills
     - ``activate_skill``
     - the global skill catalog into the caller's turn
     - **Yes**
     - Read-only; loads shared instructions into the caller's turn.
   * - Skills
     - ``install_skill``
     - the global ``write_path``
     - **No**
     - One tenant can alter the bot's instructions for all.
   * - Vault
     - ``list_secrets``
     - the shared vault
     - **No**
     - Lists the owner's credential names to any tenant; names only, never values.
   * - MCP
     - ``mcp_*`` (dynamic)
     - external MCP servers with the owner's credentials
     - **No**
     - Every call acts as the owner against their accounts.
   * - Internal
     - ``pre_response``
     - signalling only
     - **N/A**
     - Not LLM-visible; excluded from the toolset.

Running a support bot safely
----------------------------

Disable everything that is owner-scoped or process-global, and keep only the stateless and per-conversation tools. For a WhatsApp-style support bot that means turning off long-term memory, the graph, tasks, the scheduler, delegation, the entire filesystem and execution group, skills installation, the vault listing, MCP, and ``http_request``.

.. code-block:: toml

   [runtime]
   owner_id = "support"          # still one owner for the whole process

   # The relation graph is an extension: leave "minibot.extensions.tools.graph"
   # out of [extensions] modules.
   [tools.kv_memory]
   enabled = false
   [tasks]
   enabled = false
   [scheduler.prompts]
   enabled = false
   [tools.file_storage]
   enabled = false
   [tools.python_exec]
   enabled = false
   [tools.bash]
   enabled = false
   [tools.apply_patch]
   enabled = false
   [tools.http_client]
   enabled = false
   [tools.mcp]
   enabled = false
   [tools.skills]
   enabled = false
   [tools.rag]
   enabled = false

Leave ``[tools.audio_transcription]`` and the calculator/time tools on if you want them; both are stateless. Disabling ``[tools.skills]`` also removes the read-only ``list_skills`` and ``activate_skill``. If you keep the vault enabled, ``list_secrets`` still exposes credential names only, never values. ``rag_*`` can stay on *only* if your channel supplies a real per-customer ``chat_id`` and ``user_id`` and you accept that the index is otherwise shared.

A support bot often does need outbound HTTP (order lookups, ticket creation). ``http_request`` itself is tenant-neutral in the sense that it holds no per-tenant state, but it is a shared-egress SSRF surface: if you keep it, put a strict allowlist or an egress proxy in front rather than exposing the host network.

Isolation options
-----------------

There is no flag combination that turns the owner-scoped tools into real tenant isolation. Only data and process separation does. In rough order of strength:

#. **One process per tenant.** Give each tenant its own container or process with its own ``owner_id``, ``root_dir``, SQLite databases and credentials. This is the only option that fully isolates memory, graph, tasks, scheduler, files and secrets. It costs one running daemon per tenant.
#. **A shared bot with a per-tenant backend.** Keep one process but replace the owner-scoped stores with tenant-aware implementations in an extension. That means implementing your own identity model, not reusing ``chat_id`` — a path MiniBot deliberately leaves open rather than building in.
#. **Conversation-only sharing.** Accept that all long-term data is shared and expose only the stateless and per-conversation tools from the list above. Fine for a single organization's users, not for unrelated customers.

Multi-user or multi-tenant isolation is intentionally out of scope in core: it would arrive as an opt-in extension with its own identity model. Until then, run one owner per process.
