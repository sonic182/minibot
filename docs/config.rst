Configuration Reference
=======================

.. meta::
   :description: Complete Minibot config.toml reference — runtime, channels, LLM providers, memory, orchestration, scheduler, logging, tasks, tools, and extensions.
   :keywords: minibot config, config.toml, AI assistant configuration, self-hosted Telegram bot config

MiniBot is configured via a ``config.toml`` file.
Start from the provided example::

    cp config.example.toml config.toml

Values follow standard TOML syntax. Byte-size fields accept human-readable
strings (e.g. ``"64KB"``, ``"5MB"``) in addition to raw integers.

Runtime
-------

.. autoclass:: minibot.adapters.config.schema.RuntimeConfig
   :no-members:

Channels
--------

.. autoclass:: minibot.adapters.config.schema.TelegramChannelConfig
   :no-members:

LLM
---

.. autoclass:: minibot.adapters.config.schema.LLMMConfig
   :no-members:

Providers
---------

.. autoclass:: minibot.adapters.config.schema.ProviderConfig
   :no-members:

Memory
------

.. autoclass:: minibot.adapters.config.schema.MemoryConfig
   :no-members:

Orchestration
-------------

.. autoclass:: minibot.adapters.config.schema.OrchestrationConfig
   :no-members:

Scheduler
---------

.. autoclass:: minibot.adapters.config.schema.ScheduledPromptsConfig
   :no-members:

Logging
-------

.. autoclass:: minibot.adapters.config.schema.LoggingConfig
   :no-members:

Tasks
-----

``[tasks].backend`` selects the queue: ``"sqlite"`` (no broker, polled, survives a crash via lease
expiry) or ``"rabbitmq"`` (needs a broker and the ``rabbitmq`` extra, pushed). ``[tasks.sqlite]``
configures the first, ``[rabbitmq]`` the second.

The default is ``"sqlite"``. A config written before that default changed and left ``backend``
unset now uses the SQLite queue; set ``backend = "rabbitmq"`` explicitly to keep using the broker.

.. autoclass:: minibot.adapters.config.schema.TasksConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.SqliteTaskQueueConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.RabbitMQConsumerConfig
   :no-members:

Extensions
----------

Python extensions loaded at startup. See :doc:`extensions` for the full extension API.

The optional relation graph is a built-in extension. After installing the ``graph`` extra, enable
``minibot.extensions.tools.graph`` in ``[extensions].modules``. Its optional configuration accepts
``sqlite_url`` (default ``sqlite+aiosqlite:///./data/graph.db``) and ``echo`` for SQL logging. See
:doc:`graph`.

.. autoclass:: minibot.adapters.config.schema.ExtensionsConfig
   :no-members:

Tool Configuration
------------------

.. list-table::
   :header-rows: 1
   :widths: 24 22 54

   * - Section
     - Config model
     - Key options
   * - ``[tools.kv_memory]``
     - ``KeyValueMemoryConfig``
     - ``enabled``, ``sqlite_url``, ``default_limit``, ``max_limit``
   * - ``[tools.http_client]``
     - ``HTTPClientToolConfig``
     - ``enabled``, ``timeout_seconds``, ``max_bytes``, ``max_parse_bytes``, ``response_processing_mode`` (``auto``/``compact``/``text``/``none``), ``max_chars``, spillover settings
   * - ``[tools.time]``
     - ``TimeToolConfig``
     - ``enabled``, ``default_format``
   * - ``[tools.wait]``
     - ``WaitToolConfig``
     - ``enabled`` (default ``false``), ``max_milliseconds``
   * - ``[tools.calculator]``
     - ``CalculatorToolConfig``
     - ``enabled``, ``default_scale``, ``max_expression_length``, ``max_exponent_abs``
   * - ``[tools.python_exec]``
     - ``PythonExecToolConfig``
     - ``enabled``, ``python_path``, ``venv_path``, ``sandbox_mode``, timeout/output/code limits, artifact settings, environment policy
   * - ``[tools.python_exec.rlimit]``
     - ``PythonExecRLimitConfig``
     - ``enabled``, CPU, memory, file-size, process, and open-file limits
   * - ``[tools.python_exec.cgroup]``
     - ``PythonExecCgroupConfig``
     - ``enabled``, ``driver``, ``cpu_quota_percent``, ``memory_max_mb``
   * - ``[tools.python_exec.jail]``
     - ``PythonExecJailConfig``
     - ``enabled``, ``command_prefix``
   * - ``[tools.bash]``
     - ``BashToolConfig``
     - ``enabled``, timeout/output limits, parent environment and allowlist policy
   * - ``[tools.tool_output_spill]``
     - ``ToolOutputSpillConfig``
     - ``enabled``, ``spill_after_chars``, ``preview_chars``, ``subdir``, ``exclude_tools``; applies to every tool result (not just ``http_request``)
   * - ``[tools.apply_patch]``
     - ``ApplyPatchToolConfig``
     - ``enabled``, ``restrict_to_workspace``, ``workspace_root``, ``allow_outside_workspace``, ``max_patch_bytes``
   * - ``[tools.file_storage]``
     - ``FileStorageToolConfig``
     - ``enabled``, ``root_dir``, ``max_write_bytes``, ``allow_outside_root``, upload paths
   * - ``[tools.grep]``
     - ``GrepToolConfig``
     - ``enabled``, ``max_matches``, ``max_file_size_bytes``
   * - ``[tools.browser]``
     - ``BrowserToolConfig``
     - ``output_dir`` for browser/MCP-generated artifacts
   * - ``[tools.audio_transcription]``
     - ``AudioTranscriptionToolConfig``
     - ``enabled``, ``model``, ``device``, ``compute_type``, ``beam_size``, VAD and auto-transcription settings
   * - ``[tools.mcp]``
     - ``MCPToolConfig``
     - ``enabled``, ``name_prefix``, ``timeout_seconds``, ``servers``
   * - ``[[tools.mcp.servers]]``
     - ``MCPServerConfig``
     - ``name``, ``transport``, stdio command fields, HTTP fields, tool allow/deny filters
   * - ``[tools.skills]``
     - ``SkillsToolConfig``
     - ``enabled``, ``paths``, ``preload_catalog``
   * - ``[tools.rag]``
     - ``RagToolConfig``
     - ``enabled``, ``backend``, ``sqlite_url``/``qdrant_url``, ``collection_name``, ``embedding``, ``rerank``, chunk/search settings; see :doc:`rag`
   * - ``[tasks]``
     - ``TasksConfig``
     - ``enabled``, ``backend``, ``worker_timeout_seconds``, ``worker_max_steps``, ``worker_max_tool_calls``, ``max_concurrent_workers``, ``sqlite``
   * - ``[tasks.sqlite]``
     - ``SqliteTaskQueueConfig``
     - ``sqlite_url``, ``poll_interval_seconds``, ``lease_timeout_seconds``, ``batch_size``, ``max_attempts``, ``done_retention_seconds``; terminal rows and compact event history are retained for 30 days by default

.. note::

   Every owner-scoped tool — key/value memory, the relation graph, scheduled jobs and the RAG
   corpus — is owned by ``[runtime].owner_id`` (default ``"primary"``). MiniBot assists exactly
   one person, so that value is a constant of the deployment and is never derived from a message
   or a task payload.

   Conversation history is scoped separately, per channel and chat. One owner, many chat sessions
   — a private Telegram chat, a group and the console do not share history, but they do share
   that owner's memory, graph and schedules.

   Multi-user or multi-tenant isolation is deliberately not supported; it would arrive as an
   opt-in extension with its own identity model, not by reusing a channel's sender id.

Tool Config Models
------------------

.. autoclass:: minibot.adapters.config.schema.KeyValueMemoryConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.HTTPClientToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.TimeToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.WaitToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.CalculatorToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.PythonExecToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.PythonExecRLimitConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.PythonExecCgroupConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.PythonExecJailConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.BashToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.ToolOutputSpillConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.ApplyPatchToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.FileStorageToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.GrepToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.BrowserToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.AudioTranscriptionToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.MCPToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.MCPServerConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.SkillsToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.RagToolConfig
   :no-members:
