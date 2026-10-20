Agent Tools
===========

.. meta::
   :description: The Minibot agent tool surface — Python, Bash, HTTP, file storage, grep, patch editing, and MCP bridges — with config and defaults.
   :keywords: AI agent tools, Python AI agent, AI assistant with tools, Telegram bot tools

Tools are LLM-callable functions assembled at startup from ``config.toml``.
Each row below lists the public tool name exposed to the model, how it is enabled,
and the intended use.

Tool Surface
------------

.. list-table::
   :header-rows: 1
   :widths: 16 24 24 36

   * - Group
     - Tool
     - Availability
     - Purpose
   * - Chat history
     - ``chat_history_info``
     - Always available
     - Inspect the current conversation history size.
   * - Chat history
     - ``chat_history_trim``
     - Always available
     - Remove old history entries for the current conversation.
   * - Runtime
     - ``get_settings``
     - Always available
     - Read-only report of what this instance is running: version, config path, channels, LLM provider and
       model, enabled tools with their non-secret settings, agents, and whether tasks, the scheduler and the
       vault are on. A feature that is off is absent. Never returns tokens, API keys or credential URLs. A
       specialist that sets ``tools_allow`` must list it explicitly.
   * - Runtime
     - ``read_docs``
     - ``minibot-docs`` skill enabled
     - Read-only access to the documentation bundled with the installed version: list pages, search by words, or
       read one page (config sections are expanded to their field reference). Turn it off with
       ``[tools.skills] native_disabled = ["minibot-docs"]``.
   * - Memory
     - ``memory_create``, ``memory_update``, ``memory_get``, ``memory_search``, ``memory_delete``, ``memory_list_titles``
     - ``[tools.kv_memory]``
     - Save, update, retrieve, search, list, and delete persistent user notes. One tool per operation, so a pattern such as ``memory_*`` covers all of them. The old ``memory`` name is rejected in ``tools_allow``, ``tools_deny`` and ``require_approval``.
   * - Relation graph
     - ``graph_link``, ``graph_unlink``, ``graph_merge``, ``graph_neighbors``, ``graph_path``, ``graph_search``
     - ``minibot.extensions.tools.graph`` extension; ``graph`` extra
     - Store and traverse typed, owner-scoped relationships between entities. One tool per operation, so a pattern such as ``graph_*`` covers all of them. The old ``graph`` name is rejected in ``tools_allow``, ``tools_deny`` and ``require_approval``. See :doc:`graph`.
   * - Utility
     - ``current_datetime``
     - ``[tools.time]``; enabled by default
     - Return the current UTC datetime using an optional ``strftime`` format.
   * - Utility
     - ``wait``
     - ``[tools.wait]``
     - Pause execution for a given number of milliseconds (clamped to ``max_milliseconds``).
   * - Utility
     - ``calculate_expression``
     - ``[tools.calculator]``; enabled by default
     - Evaluate bounded arithmetic with Decimal precision.
   * - Response
     - ``pre_response``
     - Always available
     - Declare final-answer metadata before answering: ``kind`` (``text``/``html``/``markdown``), ``meta.disable_link_preview``, and outbound ``attachments``.
   * - HTTP
     - ``http_request``
     - ``[tools.http_client]``
     - Fetch HTTP/HTTPS resources with size limits and optional managed-file spillover.
       HTML is rendered as compact semantic text (links, forms, inputs and tables kept;
       classes, styles and scripts dropped).
   * - Host execution
     - ``python_execute``
     - ``[tools.python_exec]``
     - Run host Python code with timeout, output caps, sandbox mode, and artifact export.
   * - Host execution
     - ``python_environment_info``
     - ``[tools.python_exec]``
     - Inspect the Python runtime used by ``python_execute``.
   * - Host execution
     - ``bash``
     - ``[tools.bash]``
     - Run shell commands through ``/bin/bash -c`` (``-lc``, a login shell that re-reads profile files, only when ``pass_parent_env = true``).
   * - Editing
     - ``apply_patch``
     - ``[tools.apply_patch]``
     - Apply structured add/update/delete/move patches under the configured workspace.
   * - Managed files
     - ``list_files``
     - ``[tools.file_storage]``
     - List managed files and folders under a folder path.
   * - Managed files
     - ``file_info``
     - ``[tools.file_storage]``
     - Return size, MIME type, and timestamps for a managed path.
   * - Managed files
     - ``write_file``
     - ``[tools.file_storage]``
     - Create or overwrite a managed text file.
   * - Managed files
     - ``move_file``
     - ``[tools.file_storage]``
     - Move or rename a managed file.
   * - Managed files
     - ``delete_file``
     - ``[tools.file_storage]``
     - Delete a managed file or folder permanently.
   * - Managed files
     - ``send_file``
     - ``[tools.file_storage]``
     - Deliver a managed file to the active conversation channel.
   * - Managed files
     - ``glob_files``
     - ``[tools.file_storage]``
     - List managed files matching a glob pattern.
   * - Managed files
     - ``read_file``
     - ``[tools.file_storage]``
     - Read a complete managed text file.
   * - Managed files
     - ``code_read``
     - ``[tools.file_storage]``
     - Read a bounded line window from a managed text file.
   * - Managed files
     - ``grep``
     - ``[tools.grep]`` and ``[tools.file_storage]``
     - Search managed files with regex or fixed-string matching.
   * - Managed files
     - ``self_insert_artifact``
     - ``[tools.file_storage]``
     - Inject a managed file or image into the active runtime context.
   * - Audio
     - ``transcribe_audio``
     - ``[tools.audio_transcription]`` and ``[tools.file_storage]``
     - Transcribe or translate managed audio files with faster-whisper.
   * - Scheduled prompts
     - ``schedule_prompt``
     - ``[scheduler.prompts]``
     - Create a one-time or recurring scheduled prompt. The old ``schedule`` facade was removed and its name is rejected in ``tools_allow``, ``tools_deny`` and ``require_approval``.
   * - Scheduled prompts
     - ``list_scheduled_prompts``
     - ``[scheduler.prompts]``
     - List scheduled prompts for the current owner/chat context.
   * - Scheduled prompts
     - ``cancel_scheduled_prompt``
     - ``[scheduler.prompts]``
     - Mark a scheduled prompt as cancelled.
   * - Scheduled prompts
     - ``delete_scheduled_prompt``
     - ``[scheduler.prompts]``
     - Cancel and remove a scheduled prompt.
   * - Delegation
     - ``fetch_agent_info``
     - Enabled when agent definitions exist
     - Inspect a specialist's definition and defaults, and list the providers a delegation can target.
   * - Skills
     - ``list_skills``
     - ``[tools.skills]``
     - Discover current skills from configured skill directories.
   * - Skills
     - ``activate_skill``
     - ``[tools.skills]``
     - Load full instructions for a discovered skill.
   * - Skills
     - ``install_skill``
     - ``[tools.skills] install = true``
     - Preview or install a published skill from GitHub or an archive URL.
   * - Async tasks
     - ``delegate``
     - ``[tasks]``
     - Queue a background worker task whose result comes back to the main agent as a new turn; with
       ``spawn_task``, the only way to hand work to a specialist agent. Can override ``model_provider``,
       ``model`` and ``reasoning_effort`` for that one task.
   * - Async tasks
     - ``spawn_task``
     - ``[tasks] delegation_mode = "auto"``
     - Queue a background worker task whose answer goes straight to the user. Same parameters as
       ``delegate``; not registered when ``delegation_mode = "delegate"``.
   * - Async tasks
     - ``cancel_task``
     - ``[tasks]``
     - Cancel an active background task by ID.
   * - Async tasks
     - ``message_task``
     - ``[tasks]``
     - Send an instruction to a running background task; the worker reads it between steps.
   * - Async tasks
     - ``list_tasks``
     - ``[tasks]``
     - List persisted background tasks and their terminal state.
   * - Async tasks
     - ``get_task``
     - ``[tasks]``
     - Retrieve one task's result, structured progress, and compact event history.
   * - MCP
     - ``mcp_<server>__<remote_tool>``
     - ``[tools.mcp]``
     - Dynamically discovered Model Context Protocol tools.
   * - RAG / vector store
     - ``rag_index``, ``rag_search``, ``rag_list_metadata``, ``rag_delete``
     - ``[tools.rag]``
     - Index text files into the configured vector store and retrieve semantically relevant chunks.
   * - Scripting
     - ``run_tools_script``
     - ``[tools.codemode]``
     - Run a short Python script in a sandbox that calls the agent's other tools and returns only its printed
       output and final value. When enabled, it replaces those tools as direct calls. See `Codemode scripts`_
       below.
   * - Credential vault
     - ``list_secrets``
     - ``[vault] enabled`` (unlocked)
     - List the names of stored secrets; values are never exposed. See :doc:`vault`.
   * - Agent management
     - ``reload_agents``
     - ``[orchestration.agent_management] reload``
     - Re-read agent definition files and report which names were added, removed or updated. A failed
       reload leaves the previous roster in place. See :doc:`agents`.
   * - Agent management
     - ``create_agent``, ``update_agent``, ``delete_agent``
     - ``[orchestration.agent_management] write``
     - Create, replace or remove a model-authored specialist. Every call is validated against the
       owner's ceiling before anything is written, and a successful write refreshes the roster. The
       bundled ``create-agent`` skill is hidden until this switch is on.

Runtime Notes
-------------

- Tool defaults are defined in ``minibot.adapters.config.schema`` and configured in ``config.toml``.
- ``[tools.file_storage]`` is the shared managed-file root used by file, grep, HTTP spillover, and audio tools.
- ``[tools.tool_output_spill]`` generalizes spillover to every tool binding (main agent, delegated
  agents, and task workers), not just ``http_request``: an oversized result is swapped for a preview
  plus a managed-file pointer the agent can read back with ``grep``, ``code_read``, ``read_file``, or
  ``bash``. Tools in ``exclude_tools`` (``http_request`` and ``pre_response`` by default) are
  skipped since they already manage their own output size. ``activate_skill`` spills only past
  64,000 characters (or ``spill_after_chars``, if larger), so skill instructions normally arrive inline.
  Spill does not apply to calls made from a ``run_tools_script`` script: the result goes to the script, not
  the model, so the script can filter it, and the script's own output limits protect the context.
- ``[tools.audio_transcription]`` requires the ``stt`` extra: ``pip install "minibot[stt]"``.
- ``[tools.http_client]`` needs no extra: selectolax ships with the base install and renders HTML
  as compact semantic text instead of plain-text extraction.
- ``[tools.mcp]`` needs no extra: the MCP client is a JSON-RPC implementation with no third-party SDK.
- ``[tasks]`` gates the task tools and the consumer. ``backend = "sqlite"`` (the recommended default)
  needs no broker and no extra; ``backend = "rabbitmq"`` requires the ``rabbitmq`` extra and a broker
  configured in ``[rabbitmq]``. See :doc:`architecture` for the trade-off.
- ``[tools.rag]`` requires the ``rag`` extra (``pip install "minibot[rag]"``) for PDF ingestion and
  the SQLite backend's numpy, plus torch and sentence-transformers installed manually. The default
  ``backend = "sqlite"`` needs no service; ``"qdrant"`` needs a running instance. See :doc:`rag`.
- The relation graph requires the ``graph`` extra and the ``minibot.extensions.tools.graph``
  extension. It persists typed edges in SQLite; see :doc:`graph`.
- ``[tools.codemode]`` requires the ``codemode`` extra (``pip install "minibot[codemode]"``, or
  ``poetry install --extras codemode``); enabling it without the extra stops startup with that hint.
- Hidden compatibility aliases are normalized at execution time; prefer the public names in the table.

Codemode scripts
----------------

``run_tools_script`` lets the model write one short Python script instead of making a tool call per step. The
script calls other tools, combines and filters their results, and returns only what matters: each nested result
stays out of the model's context, and independent calls run in parallel with ``asyncio.gather``. It is off by
default; enable it with ``[tools.codemode] enabled = true`` (see :doc:`config`).

Codemode is a mode, not an extra tool: with it on, the tools a script can call are no longer offered to the model
as direct tool calls, so every tool use goes through a script, a single call included. The model sees
``run_tools_script`` plus the tools scripts cannot call, which stay direct:

- the delegation tools (``delegate``, ``spawn_task``, ``fetch_agent_info``, ``list_tasks``, ``get_task``,
  ``cancel_task``, ``message_task``);
- ``activate_skill``, ``list_skills`` and ``self_insert_artifact``, since they put content into the turn itself
  (skill instructions, an image or document), which a script result cannot carry;
- anything matching ``exclude``.

If the agent's policy removes ``run_tools_script``, the other tools are offered directly again: with
``[orchestration.main_agent] tools_deny = ["run_tools_script"]``, or with a ``tools_allow`` list that does not name
it, so a main agent with ``tools_allow`` must list ``run_tools_script`` to use codemode. Usage instructions
contributed for a tool (an MCP server's, for example) stay in the system prompt when the tool is only reachable
from scripts. Recent-file tracking only sees direct file tool calls, so a file a script wrote is not remembered as
a recent file.

.. code-block:: toml

   [tools.codemode]
   enabled = true
   timeout_seconds = 60        # wall-clock limit for the whole script, nested tool calls included
   max_tool_calls = 50         # tool calls one script may make
   max_memory_mb = 64          # heap limit of the script itself
   max_output_chars = 20000    # cap on the printed output and on the returned value
   exclude = ["mcp_mail__*"]   # tool names scripts can never call

Scripts run in `Pydantic Monty <https://github.com/pydantic/monty>`_, a Python interpreter written in Rust that
runs in a pool of worker subprocesses, so a crash cannot take the daemon down. It implements a subset of Python:
no file system, network, environment or third-party imports, and the only way to act on the world is the ``tools``
namespace. Scripts call tools as ``await tools.<name>(keyword=value)``, keyword arguments only; every character of
a tool name that is not valid in a Python identifier becomes ``_`` (``mcp_exa__web_search`` is unchanged,
``my-tool.v2`` becomes ``my_tool_v2``). The tool description lists a one-line signature per available tool,
generated from its JSON schema, up to about 6000 characters; the tools beyond that are listed by name only.
A script prints with ``print`` and returns by ending on an expression or with a top-level ``return``.

Three helpers are called by name, without ``await`` or ``tools.``:

``list_tools(query=None)``
   ``[{"name", "summary"}]`` for the tools the script can call; with ``query``, only those whose name or
   description contains it (case-insensitive).

``describe_tool(name)``
   ``{"name", "signature", "description", "parameters"}`` for one tool, by identifier or tool name; an unknown
   name raises ``ValueError``.

``text(value, limit=2000, unit="chars")``
   ``value`` as text, strings as they are and anything else as indented JSON, cut to ``limit`` characters,
   lines (``unit="lines"``) or UTF-8 bytes (``unit="bytes"``) keeping the start, with a marker saying how much
   was left out.

.. code-block:: python

   print(text(await tools.memory_search(query="trip"), limit=40, unit="lines"))

.. code-block:: python

   import asyncio

   first, second = await asyncio.gather(
       tools.memory_search(query="trip"),
       tools.current_datetime(),
   )
   try:
       await tools.http_request(url="https://example.org/missing")
   except ValueError as exc:
       print("skipped:", exc)
   {"now": second, "titles": [entry["title"] for entry in first["entries"]][:5]}

MCP tools are ordinary tools to a script, with three differences to keep in mind. In bridge mode each remote tool is
``tools.<prefix>_<server>__<remote_tool>(...)`` and returns ``{"ok", "server", "tool", "is_error", "result"}``, where
``result`` is usually text (the joined text parts, often JSON that needs ``json.loads``). An error reported by the MCP
server does not raise: it comes back with ``ok: false`` / ``is_error: true``, so the script must check it. In lazy mode the
server exposes ``<prefix>_<server>__list_tools()`` (the catalog with each ``input_schema``) and
``<prefix>_<server>__call_tool(tool_name=..., arguments={...})``, which returns the same dict.

.. code-block:: python

   import asyncio, json

   found = await tools.mcp_mail__imap_search_messages(query="invoice")
   if not found["ok"]:
       return {"error": found["result"]}
   ids = json.loads(found["result"])["ids"][:5]
   msgs = await asyncio.gather(*[tools.mcp_mail__imap_get_message(id=i) for i in ids])
   return [json.loads(m["result"])["subject"] for m in msgs if m["ok"]]

A call whose result feeds the next one is a plain sequence of ``await`` statements. When ``python_execute`` is
available, a script can pass it data with ``tools.python_execute(code=...)`` for computation the sandbox cannot do.

The result is ``{"ok": true, "output": "<printed text>", "result": <final value>}``. Output and the final value are
each cut to ``max_output_chars`` keeping their start and end, with a marker for what was omitted.

What a script can call
   The tools the agent sees after its ``tools_allow`` / ``tools_deny`` and ownership filtering, built after
   approval, event and output-spill wrapping, minus the delegation tools and anything matching ``exclude`` (below).
   A tool the agent cannot see is not callable from a script, and a tool listed in ``require_approval`` still asks
   the owner for every nested call. A tool that
   raises reaches the script as an exception (``ValueError`` for rejected arguments, ``PermissionError`` for a denied
   approval, ``RuntimeError`` otherwise); catch it to continue. A script that ends on an uncaught
   ``PermissionError`` from a denial returns the ``tool_approval:denied`` error, like a direct call; a denial the
   script wraps in another exception type is an ordinary ``codemode:script_error``. The tools listed above as direct,
   ``run_tools_script`` itself and anything matching ``exclude`` are never exposed (they stay direct tools), and
   nested calls do not count against the agent's own per-turn tool-call cap. MCP tools appear under their exposed names; for lazy MCP servers that means
   ``mcp_<server>__list_tools`` and ``mcp_<server>__call_tool``. Large nested results arrive as a preview plus a file
   path, as they do for a direct call. A tool that returns a message directive loses it when called from a script,
   which is why ``self_insert_artifact`` stays a direct tool.

Time and call limits
   ``timeout_seconds`` is wall-clock time for the whole script, nested calls included, except the time a nested
   call spends waiting for an owner approval (``[tools.approval]``): the clock stops while any approval is pending,
   so a slow decision never ends the script, and the approval's own ``timeout_seconds`` still bounds the wait.
   ``max_tool_calls`` caps how many tool calls one script makes; the call past the cap raises a ``ValueError``
   whose text starts with ``codemode:tool_call_limit`` (listed in ``tool_errors`` too), which the script may catch.

Failures
   A script that fails returns ``{"ok": false, "error_code": ..., "error": ..., "output": <partial output>}``.
   ``error_code`` is ``codemode:syntax_error``, ``codemode:timeout``, ``codemode:memory_limit``,
   ``codemode:script_error`` (any uncaught exception, with a ``hint`` when it named a tool that does not exist) or
   ``codemode:sandbox_failed``. ``tool_errors`` lists the nested calls that failed, as tool name and error code (the tool's own
   ``error_code`` when it raised one, ``tool_execution_failed`` otherwise). Each failed nested call is also logged at
   warning level with the tool name and error code, never its arguments or message.
   Calls that already ran are not undone, and calls still running at a timeout are cancelled.

Nested calls and events
   Every nested call publishes its own ``ToolCallEvent`` whose ``parent_call_id`` is the ``call_id`` of the
   ``run_tools_script`` call (see :doc:`events`). The web chat and the console subscribe to these events;
   Telegram does not.

Specialists
   In task workers the tool is opt-in like the other bundled extensions: list ``"codemode"`` in
   ``[tasks] worker_extensions`` and name ``run_tools_script`` in a specialist's ``tools_allow``; its scripts then
   call the tools that specialist itself has, and those tools are no longer direct calls for it. A
   ``tools_deny``-only specialist gets it too, unless its ``tools_deny`` names ``run_tools_script``. A
   ``tools_allow`` specialist that does not name it, and a task without an agent, keep their tools as direct calls.

Trust model
   Monty keeps the script itself away from the file system, network and environment, and every effect is a tool
   call that already passed the existing gates. That narrows the surface but is not containment: MiniBot is still
   not a security boundary, and deployment isolation (see :doc:`security`) still applies.

Implementation Reference
------------------------

.. automodule:: minibot.llm.tools.user_memory
   :no-members:

.. autoclass:: minibot.llm.tools.http_client.HTTPClientTool
   :no-members:

.. autoclass:: minibot.llm.tools.file_storage.FileStorageTool
   :no-members:

.. autoclass:: minibot.llm.tools.grep.GrepTool
   :no-members:

.. autoclass:: minibot.llm.tools.bash.BashTool
   :no-members:

.. autoclass:: minibot.llm.tools.python_exec.HostPythonExecTool
   :no-members:

.. autoclass:: minibot.llm.tools.audio_transcription.AudioTranscriptionTool
   :no-members:

.. autoclass:: minibot.llm.tools.scheduler.SchedulePromptTool
   :no-members:

.. autoclass:: minibot.llm.tools.mcp_bridge.MCPToolBridge
   :no-members:

.. autoclass:: minibot.llm.tools.skill_loader.SkillLoaderTool
   :no-members:

.. autoclass:: minibot.llm.tools.skill_installer.SkillInstallerTool
   :no-members:
