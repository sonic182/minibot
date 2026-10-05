Events
======

.. meta::
   :description: Minibot event-bus reference — the lifecycle of a turn and the events extensions can subscribe to with @mb.on.
   :keywords: AI agent events, async event bus, minibot extensions, TurnCompletedEvent

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
       B --> H["ReasoningEvent (per provider step)"]
       D --> G["OutboundFileEvent (sending files)"]

Turns run one at a time. A message from the chat whose turn is running does not start a
second turn: it is stored in the history and handed to that turn, which shows it to the
model before its next call — after the current tool returns, in place of the tool calls that
model response still had queued (they are reported to the model as skipped), or right after
a final answer, which is then replaced by a new one. A tool that is already running is never
interrupted by a message. A message that arrives after the turn's last check, or with a final
answer when the turn has no step left, becomes the next turn. Task results and scheduled
prompts always get a turn of their own, and a message that arrives while one of them runs
waits for its own turn too.

``TurnStopRequestedEvent`` (``/stop`` on Telegram) cancels the running turn of that chat
instead, whether it answers the owner, a task result or a scheduled prompt. The dispatcher then publishes ``TurnFailedEvent`` with ``error = "stopped by user"``
and a ``Stopped.`` reply, and records the stop in the history. Messages still waiting for that
turn are saved to the history without being run. A tool call cancelled midway is not undone:
an email already sent stays sent. Background tasks keep running; use ``cancel_task`` for them.

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

TurnStopRequestedEvent
~~~~~~~~~~~~~~~~~~~~~~

**Fires when**: an authorized Telegram user sends ``/stop``. The dispatcher cancels that chat's
running turn, or answers ``Nothing is running.`` when there is none or the model has already
produced its answer.

Payload:

- ``channel``, ``chat_id``, ``user_id``.

ReasoningEvent
~~~~~~~~~~~~~~

**Fires when**: a provider step returns reasoning, before the turn finishes. Reasoning is
also attached to the final response metadata; this event lets a channel show thinking while
the turn is still running.

Payload:

- ``text`` — the reasoning text for this step.
- ``step`` — the 1-based provider step that produced it.
- ``turn_id``, ``owner_id``, ``channel``, ``chat_id``.

ToolCallEvent
~~~~~~~~~~~~~

**Fires when**: around every tool handler invocation — ``phase`` is ``started`` first,
then ``completed`` or ``failed``.

Payload:

- ``phase`` — ``"started"`` | ``"completed"`` | ``"failed"``.
- ``call_id`` — a per-invocation id shared by that call's ``started`` and its
  ``completed``/``failed`` event, so a consumer can pair the lifecycle phases.
- ``tool_name`` — the tool name.
- ``turn_id``, ``owner_id``, ``channel``, ``chat_id``.
- ``detail`` — a redacted, size-clipped human-readable summary of the call, produced by
  ``minibot/llm/tools/tool_call_display.py`` before the event is published. Raw argument values
  never leave the tool-execution layer because they can be large and can hold credentials.
- ``error`` — set when ``phase`` is ``failed``.

Typical use: audit which tools ran, safely, without exposing argument values.

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
