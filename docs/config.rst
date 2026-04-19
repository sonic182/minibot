Configuration Reference
=======================

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

RabbitMQ
--------

.. autoclass:: minibot.adapters.config.schema.RabbitMQConsumerConfig
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
     - ``enabled``, ``sqlite_url``, ``default_limit``, ``max_limit``, ``default_owner_id``
   * - ``[tools.http_client]``
     - ``HTTPClientToolConfig``
     - ``enabled``, ``timeout_seconds``, ``max_bytes``, ``response_processing_mode``, ``max_chars``, spillover settings
   * - ``[tools.time]``
     - ``TimeToolConfig``
     - ``enabled``, ``default_format``
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
   * - ``[tools.tasks]``
     - ``TaskToolConfig``
     - ``enabled``; requires ``[rabbitmq].enabled = true``

Tool Config Models
------------------

.. autoclass:: minibot.adapters.config.schema.KeyValueMemoryConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.HTTPClientToolConfig
   :no-members:

.. autoclass:: minibot.adapters.config.schema.TimeToolConfig
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

.. autoclass:: minibot.adapters.config.schema.TaskToolConfig
   :no-members:
