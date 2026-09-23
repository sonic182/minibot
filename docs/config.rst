Configuration Reference
=======================

.. meta::
   :description: Complete Minibot config.toml reference — runtime, channels, LLM providers, memory, orchestration, scheduler, logging, tasks, tools, and extensions.
   :keywords: minibot config, config.toml, AI assistant configuration, self-hosted Telegram bot config

MiniBot is configured via a ``config.toml`` file, most easily created with the interactive
wizard (see :doc:`getting_started`)::

    minibot configure

To hand-write or inspect the file directly instead, start from the provided example::

    cp config.example.toml config.toml

Values follow standard TOML syntax. Byte-size fields accept human-readable
strings (e.g. ``"64KB"``, ``"5MB"``) in addition to raw integers.

.. _config-environment-variables:

Environment variables
---------------------

Use ``${NAME}`` in any string value to read a variable from MiniBot's process environment.
References work in nested tables and lists, including provider headers and extension configuration:

.. code-block:: toml

   [providers.openai]
   api_key = "${OPENAI_API_KEY}"
   base_url = "${API_BASE_URL}/v1"

   [providers.openai.headers]
   Authorization = "Bearer ${PROXY_TOKEN}"

   [runtime]
   agent_timeout_seconds = "${AGENT_TIMEOUT_SECONDS}"

   [channels.telegram]
   enabled = "${TELEGRAM_ENABLED}"
   allowed_chat_ids = ["${OWNER_CHAT_ID}"]

Names follow ``[A-Za-z_][A-Za-z0-9_]*``. Multiple references in one value are supported.
Write ``$${NAME}`` to produce literal ``${NAME}``. Bare ``$NAME`` and unsupported expressions such as
``${NAME:-default}`` remain unchanged. Table names and keys are never expanded.

TOML is parsed before substitution, so quotes, backslashes, and newlines in environment values are
inserted verbatim without changing the TOML structure. Both TOML basic and literal strings are
expanded. Inserted values are not expanded again. Non-string TOML values stay unchanged; quoted
references for numeric, boolean, and byte-size fields use the existing schema's conversions and
validation. Environment values are not parsed as TOML lists or tables.

An unset variable raises an error naming the variable and configuration path, including list indexes,
without showing its value. All sections are expanded, including disabled providers and tools.
A variable set to an empty string produces ``""`` and follows the setting's existing validation.

Supply variables to each process that loads configuration: the daemon, console, task workers, and
``minibot configure``. MiniBot does not load ``.env`` files or execute shell expressions. Changes to
the environment take effect the next time settings are loaded. ``MINIBOT_CONFIG`` still selects the
configuration file when no explicit path is supplied.

``minibot configure`` retains the original references for unchanged values, including list entries,
and uses resolved credentials for model discovery. References entered in credential prompts are
saved literally. Loading settings never rewrites the file; literal secrets remain plain text on disk.

For Python callers, expansion occurs in ``Settings.from_dict()`` and ``Settings.from_file()`` before
normalization and validation, without mutating the input dictionary. Direct ``Settings(...)`` and
``Settings.model_validate(...)`` calls do not expand references.

Vault references
~~~~~~~~~~~~~~~~

``${secret:NAME}`` resolves to an entry in the encrypted credential vault, so a credential never has
to sit in plain text on disk. This matters because ``config.toml`` is readable by the ``bash`` tool,
which has no filesystem jail — the vault file is not.

.. code-block:: toml

   [providers.openai]
   api_key = "${secret:OPENAI_API_KEY}"

   [channels.telegram]
   bot_token = "${secret:telegram}"

   [[tools.mcp.servers]]
   name = "linear"
   headers = { Authorization = "Bearer ${secret:linear}" }

Names follow ``[A-Za-z_][A-Za-z0-9_.-]*``, matching what the vault document accepts as a key.
``$${secret:NAME}`` produces a literal ``${secret:NAME}``. The resolved value is injected into the
in-memory ``Settings`` instance only; nothing is written back to the file.

Resolution happens in a **second pass**, after ``${ENV_VAR}`` expansion and after the vault is
unlocked at startup — the vault's location comes from configuration, so it cannot be known before
the first pass. Consequences worth knowing:

- ``[vault]`` settings themselves cannot use ``${secret:}``; doing so is an error.
- ``[vault] enabled`` must be ``true`` wherever a reference appears, or startup fails naming the
  reference.
- A configuration with no references never reads the vault for expansion at all.
- Task workers reload configuration in their own process and receive the vault contents over the
  in-memory pipe from the daemon, so references resolve there too.
- ``minibot configure`` preserves references rather than resolving them, exactly as it does for
  ``${ENV_VAR}``.

``${secret:NAME}`` and the typed ``[[tools.mcp.servers]] auth_secret`` field both read the vault and
both remain supported; ``auth_secret`` is the shorthand for the common ``Authorization: Bearer``
case. Setting both on the same server is an error.

This protects the credential **at rest**. Once resolved, the value lives in the daemon's memory just
as an ``${ENV_VAR}`` one does; see :doc:`security` for the full threat model.

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

See :doc:`providers` for setup examples and compatible endpoint guidance.

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

Vault
-----

An optional encrypted store for credentials, unlocked once at startup. Needs the ``vault`` extra
(``poetry install --extras vault``). Secrets are written with ``minibot vault edit`` (see
:doc:`cli`) and bound to a destination in configuration — ``[[tools.mcp.servers]] auth_secret``
names a vault entry that the MCP client sends as its own ``Authorization`` header. The LLM can
list secret *names* and nothing more; there is no tool that reads a value and no ``secret://``
substitution it can write into a tool argument. See :doc:`security` for the threat model.

.. autoclass:: minibot.adapters.config.schema.VaultConfig
   :no-members:

HTTP server
-----------

An optional HTTP server running on the daemon's own event loop. Needs the ``http`` extra
(``poetry install --extras http``). It serves ``/health`` — open, so a container healthcheck needs
no credential — and an authenticated dashboard at ``/`` showing the running model, channels,
extensions, tools, routes, uptime, and pending turns. Its stylesheet and scripts are served beneath
``/static``, gzip-compressed when the browser accepts it; pages are not compressed, since they
carry tokens next to reflected input such as the search query.
Conversation history is available at ``/history`` and can contain sensitive data. The browser chat at ``/chat``
uses the ``web:1`` history session and can contain the same sensitive data. The ``/history``,
``/memory`` and ``/scheduled`` pages each have a search box and load more entries as you scroll;
history search matches message text across every conversation. Extensions can
also contribute routes through ``mb.add_route``, or ``mb.add_page`` for one with a menu entry (see
:doc:`extensions`). All routes except
``/health`` require a bearer token or HTTP Basic credentials; one is mandatory unless ``host`` is
the literal ``127.0.0.1`` or ``::1``. When the key-value memory, scheduled prompts, graph and MCP
extensions are enabled, their pages are available at ``/memory``, ``/scheduled``, ``/graph`` and
``/mcp``. TLS is out of scope: run it behind
a reverse proxy when it is reachable from outside the host. The chat WebSocket authenticates with a
per-boot token sent through ``Sec-WebSocket-Protocol``, not a URL query parameter. A TLS-terminating
proxy must forward the original ``X-Forwarded-Proto`` and ``X-Forwarded-Host`` headers so browser
origin validation compares against the public origin.

The browser chat can accept image and audio uploads stored temporarily under ``uploads/temp/web``.
This requires ``[tools.file_storage] enabled``; audio additionally requires automatic
transcription. ``chat_upload_max_attachments``, ``chat_upload_max_image_bytes``,
``chat_upload_max_audio_bytes`` and ``chat_upload_max_total_bytes`` bound a browser session's
uploads, and ``chat_upload_retention_hours`` controls how long completed uploads are kept before
cleanup (``0`` disables it).

.. autoclass:: minibot.adapters.config.schema.HTTPServerConfig
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
   * - ``[vault]``
     - ``VaultConfig``
     - ``enabled``, ``path``, ``password_file``; see `Vault`_ above
   * - ``[http]``
     - ``HTTPServerConfig``
     - ``enabled``, ``host``, ``port``, ``auth_token``, ``basic_auth_user``, ``basic_auth_password``, ``chat_upload_*``; see `HTTP server`_ above
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
     - ``enabled``, ``model``, ``device``, ``compute_type``, ``beam_size``, VAD and auto-transcription settings, ``server_url`` (remote whisper.cpp server)
   * - ``[tools.mcp]``
     - ``MCPToolConfig``
     - ``enabled``, ``name_prefix``, ``timeout_seconds``, ``servers``
   * - ``[[tools.mcp.servers]]``
     - ``MCPServerConfig``
     - ``name``, ``transport``, stdio command fields, HTTP fields, tool allow/deny filters
   * - ``[tools.skills]``
     - ``SkillsToolConfig``
     - ``enabled``, ``paths``, ``preload_catalog``, ``native``, ``native_disabled``, ``write_path``,
       ``install``
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
