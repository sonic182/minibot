Async Tasks
===========

.. meta::
   :description: Offload long-running work in Minibot to a background task worker queue backed by SQLite or RabbitMQ, with progress, results, and retention.
   :keywords: AI agent background tasks, async task queue, SQLite task queue, RabbitMQ AI agent

MiniBot can hand long-running work to a background worker instead of blocking the turn. The
``[tasks]`` section gates both the consumer service and the task tools: when it is disabled, the
model never sees them.

.. note::

   Async tasks are disabled by default (``[tasks].enabled = false``). The default SQLite backend
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
     - Queue a worker task from a ``prompt``. Optionally target a specialist with ``agent_name``,
       pass structured ``context_json``, and cap ``timeout_seconds``, ``max_steps``, and
       ``max_tool_calls`` (each may not exceed the configured worker ceiling).
   * - ``cancel_task``
     - Cancel an active task by ``task_id``.
   * - ``list_tasks``
     - List the owner's tasks, optionally filtered by ``status`` (up to 100).
   * - ``get_task``
     - Retrieve one task's result, structured progress, and compact event history
       (``include_events`` defaults to ``true``).

Statuses
--------

A task moves through ``pending``, ``leased``, ``running``, and a terminal state: ``done``,
``failed``, ``cancelled``, or ``timed_out``. A ``retrying`` task was redelivered after a failed
attempt. Each record also carries a ``stop_reason`` (for example ``completed``, ``max_steps``,
``max_tool_calls``, ``timeout``, or ``provider_error``) that explains why the worker stopped.

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

.. warning::

   For the SQLite backend, ``[tasks.sqlite].lease_timeout_seconds`` must be greater than
   ``[tasks].worker_timeout_seconds``. A shorter lease expires while the worker is still running
   and the task is handed to a second worker, running it twice. Startup rejects that combination.

Operational notes
-----------------

- Task workers build their own tool registry: configured extension tools are available, but worker
  registries do not start extension services or event subscriptions.
- ``spawn_task`` requires channel context so the worker knows where to post progress and results.
- ``spawn_task`` returns a ``task_id`` immediately with ``status = "queued"``; poll it with
  ``get_task`` rather than waiting in the turn.
