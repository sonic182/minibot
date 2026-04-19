Tools
=====

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
   * - Memory
     - ``memory``
     - ``[tools.kv_memory]``
     - Save, retrieve, search, list, and delete persistent user notes.
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
   * - HTTP
     - ``http_request``
     - ``[tools.http_client]``
     - Fetch HTTP/HTTPS resources with size limits and optional managed-file spillover.
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
     - Run shell commands through ``/bin/bash -lc``.
   * - Editing
     - ``apply_patch``
     - ``[tools.apply_patch]``
     - Apply structured add/update/delete/move patches under the configured workspace.
   * - Managed files
     - ``filesystem``
     - ``[tools.file_storage]``
     - Unified managed-file facade for list, glob, info, write, move, delete, and send.
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
     - ``schedule``
     - ``[scheduler.prompts]``
     - Unified facade for create, list, cancel, and delete scheduled prompts.
   * - Scheduled prompts
     - ``schedule_prompt``
     - ``[scheduler.prompts]``
     - Create a one-time or recurring scheduled prompt.
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
     - Inspect a specialist agent definition.
   * - Delegation
     - ``invoke_agent``
     - Enabled when agent definitions exist
     - Delegate a task to a specialist agent.
   * - Skills
     - ``list_skills``
     - ``[tools.skills]``
     - Discover current skills from configured skill directories.
   * - Skills
     - ``activate_skill``
     - ``[tools.skills]``
     - Load full instructions for a discovered skill.
   * - Async tasks
     - ``spawn_task``
     - ``[tools.tasks]`` and ``[rabbitmq]``
     - Queue a background worker task.
   * - Async tasks
     - ``cancel_task``
     - ``[tools.tasks]`` and ``[rabbitmq]``
     - Cancel an active background task by ID.
   * - Async tasks
     - ``list_tasks``
     - ``[tools.tasks]`` and ``[rabbitmq]``
     - List active background tasks.
   * - MCP
     - ``mcp_<server>__<remote_tool>``
     - ``[tools.mcp]``
     - Dynamically discovered Model Context Protocol tools.

Runtime Notes
-------------

- Tool defaults are defined in ``minibot.adapters.config.schema`` and configured in ``config.toml``.
- ``[tools.file_storage]`` is the shared managed-file root used by file, grep, HTTP spillover, and audio tools.
- ``[tools.audio_transcription]`` requires the ``stt`` extra: ``poetry install --extras stt``.
- ``[tools.mcp]`` requires the ``mcp`` extra: ``poetry install --extras mcp``.
- ``[tools.tasks]`` requires the ``rabbitmq`` extra and ``[rabbitmq].enabled = true``.
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
