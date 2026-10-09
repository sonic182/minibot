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
     - ``spawn_task``
     - ``[tasks]``
     - Queue a background worker task; the only way to delegate to a specialist agent. Can override
       ``model_provider``, ``model`` and ``reasoning_effort`` for that one task.
   * - Async tasks
     - ``cancel_task``
     - ``[tasks]``
     - Cancel an active background task by ID.
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
- Hidden compatibility aliases are normalized at execution time; prefer the public names in the table.

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
