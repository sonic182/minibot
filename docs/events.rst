Events
======

MiniBot runs on an in-process ``asyncio`` pub/sub event bus. Channel services publish
inbound messages, the dispatcher drives turns, and outbound events are consumed by
the channel adapters. Extensions observe any of it with ``@mb.on(EventType)``.

Subscription semantics:

- Handlers are ``async def handler(event)`` and **lossy**: a slow handler drops events
  rather than blocking the pipeline. Use them for telemetry, logging, and side effects —
  never as a delivery guarantee.
- You can subscribe with ``@mb.on(EventType)`` as a decorator or ``mb.on(EventType, handler)``
  explicitly.
- Task-worker entrypoints do not start event subscriptions; ``@mb.on`` is effectively
  available on the ``daemon`` and ``console`` entrypoints (check ``mb.entrypoint``).

Turn lifecycle
--------------

A normal turn flows through these events in order:

.. mermaid::

   flowchart LR
       A["MessageEvent (inbound)"] --> B["TurnStartedEvent"]
       B --> C["ToolCallEvent (started / completed / failed) ×N"]
       C --> D["OutboundEvent(s)"]
       D --> E["TurnCompletedEvent"]
       B --> F["TurnFailedEvent (turn raised before a response)"]
       D --> G["OutboundFileEvent (sending files)"]

Event reference
---------------

MessageEvent
~~~~~~~~~~~~

**Fires when**: an inbound message enters the system. Published by channel services
(console, Telegram), the daemon for synthetic messages, and by ``[scheduler]`` /
async-task workers that post results through the pipeline. The dispatcher consumes it
to start a turn.

Payload:

- ``message`` — the ``ChannelMessage``: ``channel``, ``user_id``, ``chat_id``,
  ``message_id``, ``text``, ``attachments``, ``metadata``.

Typical use: audit inbound traffic, or inject synthetic messages to start a turn.

TurnStartedEvent
~~~~~~~~~~~~~~~~

**Fires when**: the dispatcher begins processing an inbound message.

Payload:

- ``turn_id`` — the originating ``MessageEvent`` id.
- ``channel``, ``chat_id``, ``user_id``.

TurnCompletedEvent
~~~~~~~~~~~~~~~~~~

**Fires when**: a turn is fully done — after the response was published (and thus handed
to the channel), or when the reply was deliberately suppressed (``should_reply`` false).
Never before the response is out.

Payload:

- ``turn_id``, ``channel``, ``chat_id``.
- ``should_reply`` — whether a response was emitted.
- ``llm_provider``, ``llm_model`` — what produced the response.
- ``token_trace`` — token accounting dict (e.g. ``turn_total_tokens``).
- ``compaction_performed`` — whether history compaction ran this turn.

Typical use: per-turn metrics, token budget tracking. See
`examples/minibot_ext_demo.py <https://github.com/sonic182/minibot/blob/main/examples/minibot_ext_demo.py>`_.

TurnFailedEvent
~~~~~~~~~~~~~~~

**Fires when**: a turn raised before producing a response.

Payload:

- ``turn_id``, ``channel``, ``chat_id``.
- ``error`` — the exception message.

ToolCallEvent
~~~~~~~~~~~~~

**Fires when**: around every tool handler invocation — ``phase`` is ``started`` first,
then ``completed`` or ``failed``.

Payload:

- ``phase`` — ``"started"`` | ``"completed"`` | ``"failed"``.
- ``tool_name`` — the tool name.
- ``turn_id``, ``owner_id``, ``channel``, ``chat_id``.
- ``argument_keys`` — the argument *keys* only. Values are deliberately omitted: they can
  be large and can hold credentials.
- ``error`` — set when ``phase`` is ``failed``.

Typical use: audit which tools ran with which arguments, without seeing their values.

OutboundEvent
~~~~~~~~~~~~~

**Fires when**: anything is sent toward the user — the main turn reply, in-turn
continuation updates, compaction notices, format-repair fallbacks, and async-task
progress updates.

Payload:

- ``response`` — the ``ChannelResponse``: ``channel``, ``chat_id``, ``text``,
  ``render`` (kind/text/meta), ``metadata``.
- ``metadata`` may carry ``should_reply``, ``continuation_update``,
  ``compaction_update``, or ``format_repair_failed``.

OutboundFileEvent
~~~~~~~~~~~~~~~~~

**Fires when**: a managed file is sent to the user — from the ``self_insert_artifact``
tool or async-task attachment delivery.

Payload:

- ``response`` — the ``ChannelFileResponse``: ``channel``, ``chat_id``, ``file_path``,
  ``caption``, ``metadata``.

OutboundFormatRepairEvent
~~~~~~~~~~~~~~~~~~~~~~~~~

**Fires when**: the Telegram adapter detects a malformed rich-text response and asks the
dispatcher to repair it (``format_repair_enabled``). The dispatcher re-publishes an
``OutboundEvent`` with the repaired text, or a fallback marked ``format_repair_failed``.

Payload:

- ``response`` — the original ``ChannelResponse``.
- ``parse_error`` — what failed to parse.
- ``attempt`` — repair attempt number (1-based).
- ``chat_id``, ``channel``, ``user_id``.

SystemEvent
~~~~~~~~~~~

Reserved for system-level notifications; nothing in the current code publishes it.