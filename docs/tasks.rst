Async Tasks
===========

.. meta::
   :description: Offload long-running work in Minibot to a background task worker queue backed by SQLite or RabbitMQ, with progress, results, and retention.
   :keywords: AI agent background tasks, async task queue, SQLite task queue, RabbitMQ AI agent

MiniBot hands long-running work to a background worker instead of blocking the turn. The
``[tasks]`` section gates both the consumer service and the task tools: when it is disabled, the
model never sees them.

.. note::

   ``spawn_task`` and ``delegate`` are also the only ways to hand work to a specialist agent (see :doc:`agents`), so
   disabling this section disables multi-agent orchestration too. The reverse does not hold:
   ``[orchestration.specialists].enabled = false`` keeps the task system and its generic worker
   while removing the specialist roster and named delegation. With ``[tasks]`` off and
   ``[orchestration.specialists]`` left at its default, the roster is disabled too; setting
   ``specialists.enabled = true`` explicitly with ``[tasks].enabled = false`` is a config error.

.. note::

   Async tasks are enabled by default (``[tasks].enabled = true``). The default SQLite backend
   needs no broker and no extra; ``backend = "rabbitmq"`` requires the ``rabbitmq`` extra and a
   broker configured in ``[rabbitmq]``.

Backends
--------

.. list-table::
   :header-rows: 1
   :widths: 16 42 42

   * - ``backend``
     - Behavior
     - Requires
   * - ``"sqlite"`` (default)
     - Polled queue in a local SQLite file. Survives a crash via lease expiry, so a stalled task is
       redelivered to another worker.
     - Nothing beyond the base install; storage under ``[tasks.sqlite]``.
   * - ``"rabbitmq"``
     - Push-based queue. The API process publishes tasks to a broker and a consumer drains them.
     - The ``rabbitmq`` extra and a running broker (``[rabbitmq]``).

Tool Surface
------------

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Tool
     - Purpose
   * - ``spawn_task``
     - Queue a worker task from a ``prompt`` whose answer goes straight to the user. Optionally target a
       specialist with ``agent_name``, pass structured ``context_json``, and cap ``timeout_seconds`` (no
       greater than the configured worker timeout). Without it, a named specialist's own
       ``timeout_seconds`` applies. Steps and tool calls are bounded only by ``worker_max_steps`` /
       ``worker_max_tool_calls``. Registered only when ``[tasks] delegation_mode = "auto"`` (the default).
   * - ``delegate``
     - Same parameters as ``spawn_task``, for when the main agent needs the result: it comes back as a new
       turn instead of going to the user. Always registered. Limited to three delegations per turn and
       three chained turns deep. See :doc:`agents`.
   * - ``cancel_task``
     - Cancel an active task by ``task_id``.
   * - ``message_task``
     - Send a ``message`` to a task that is still running, without stopping it: narrow its scope, add a
       missing fact, or tell it to wrap up. The worker folds it in as an instruction between steps (a tool
       call in progress finishes first). Returns ``delivered = false`` when the task is not running in this
       process: not started yet, already finished, or leased by another consumer.
   * - ``list_tasks``
     - List the owner's tasks, optionally filtered by ``status`` (up to 100).
   * - ``get_task``
     - Retrieve one task's result, structured progress, and compact event history (by full id or a
       unique prefix of at least 8 characters)
       (``include_events`` defaults to ``true``).

Statuses
--------

A task moves through ``pending``, ``leased``, ``running``, and a terminal state: ``done``,
``failed``, ``cancelled``, or ``timed_out``. A ``retrying`` task was redelivered after a failed
attempt. These are the persisted, filterable states (``list_tasks`` accepts them); the
``status = "queued"`` returned by ``spawn_task`` and ``delegate`` is only an immediate acknowledgement that the
task was enqueued, not a stored state. Each record also carries a ``stop_reason`` (for example
``completed``, ``max_steps``, ``max_tool_calls``, ``timeout``, or ``provider_error``) that
explains why the worker stopped.

Results and history
-------------------

Worker results and their compact event history are persisted. ``get_task`` returns the result text,
attachments, metadata, and any recorded events; attachments are delivered to the user through the
same outbound pipeline as the main agent. While a task runs, the worker publishes progress updates
to the originating channel. Terminal rows and their event history are retained for
``[tasks.sqlite].done_retention_seconds`` (30 days by default) before being purged.

Configuration
-------------

See :class:`minibot.adapters.config.schema.TasksConfig` and
:class:`minibot.adapters.config.schema.SqliteTaskQueueConfig` for every option. The relevant TOML
sections are ``[tasks]`` and ``[tasks.sqlite]``; the broker settings are under ``[rabbitmq]``.

Worker limits live on ``[tasks]``:

- ``worker_timeout_seconds`` — hard per-task processing timeout (default ``1800``).
- ``worker_max_steps`` / ``worker_max_tool_calls`` — optional ceilings, or ``"unlimited"`` (the default).
- ``max_concurrent_workers`` — maximum parallel handlers (default ``4``).
- ``delegation_mode`` — ``"auto"`` (default) registers both ``delegate`` and ``spawn_task`` and the model picks
  by tool; ``"delegate"`` registers only ``delegate``, so every result returns to the main agent as a new turn,
  falling back to direct delivery past the continuation limit. It replaces ``continue_turn_mode = "always"``;
  the removed ``continue_turn_mode`` and ``continue_turn_default`` keys are rejected at config load. A
  ``[orchestration.main_agent] tools_allow`` list must name ``delegate`` as well as ``spawn_task``, or the main
  agent loses the tool that returns results to it. See :doc:`agents`.
- ``worker_extensions`` — bundled tool extensions a worker loads on top of the scheduler and utility ones
  (default ``[]``). ``"kv_memory"``, ``"rag"`` and ``"codemode"`` (the ``run_tools_script`` tool, which calls
  the tools that specialist has) are accepted, each at most once, and each needs its ``[tools.*]`` section enabled. Their storage is prepared on the first call in each worker.

  .. code-block:: toml

     [tasks]
     worker_extensions = ["rag"]

  Only a specialist that claims the tools gets them, for example with ``tools_allow: ["rag_*"]``. A
  specialist that sets only ``tools_deny`` gets every tool it does not deny, these included. A task
  without an agent never gets them. MCP servers need no entry here: a specialist claims them through
  ``mcp_servers``.

  Each worker process is separate, so a task that calls RAG loads the embedding model (and the reranker for
  a search) itself: expect that latency on its first RAG call and that memory per concurrent worker.

.. warning::

   For the SQLite backend, ``[tasks.sqlite].lease_timeout_seconds`` must be greater than
   ``[tasks].worker_timeout_seconds``. A shorter lease expires while the worker is still running
   and the task is handed to a second worker, running it twice. Startup rejects that combination.

Operational notes
-----------------

- Task workers build their own tool registry: configured extension tools and the bundles listed in
  ``worker_extensions`` are available, but worker registries do not start extension services or event
  subscriptions.
- ``spawn_task`` and ``delegate`` require channel context so the worker knows where to post progress and results.
- ``spawn_task`` and ``delegate`` return a ``task_id`` immediately with ``status = "queued"``; poll it with
  ``get_task`` rather than waiting in the turn.
