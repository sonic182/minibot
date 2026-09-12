# Personal-first MiniBot

Status: **phases 1 and 2 done**

## Product decision

MiniBot is a self-hosted personal AI assistant for one trusted owner profile. It is
not a multi-tenant service. A channel's `user_id` remains the raw sender ID used for
channel authorization and audit context; it is not an application account or a data
namespace. `[runtime].owner_id` (default `"primary"`) is the one profile that owns
long-term memory, graph data, scheduled work and the RAG corpus.

The multi-user concept is now removed from the code, not merely discouraged. Ownership
is a constant of the deployment: it is read once from config and is never derived from
a message, a task payload or any caller-supplied field, on any entrypoint. Sessions are
chat sessions — `channel` + `chat_id`, with `user_id` no longer part of the key — and
every one of them belongs to that same single owner.

Conversation state remains per channel/chat. This is intentional for a personal
assistant: a private Telegram chat, a chosen group, and the console are distinct
conversations; they all serve the same owner profile.

Non-goals: user registration, tenant records, per-user quotas, public API keys,
billing, or a `multi_user` mode.

## Existing call stacks

Inbound turn:

```
minibot/adapters/messaging/telegram/service.py:65  TelegramService._handle_message()
  -> minibot/core/events.py:16                     MessageEvent(ChannelMessage)
  -> minibot/app/dispatcher.py:162                 Dispatcher._run()
  -> minibot/app/dispatcher.py:181                 Dispatcher._handle_message()
  -> minibot/app/handlers/llm_handler.py:12        LLMMessageHandler.handle()
  -> minibot/app/handlers/services/turn_service.py:89  LLMTurnService.handle()
```

The identity facts become behavior here:

```
minibot/core/channels.py:8                          channel, user_id, chat_id
  -> minibot/shared/utils.py:13                     session = channel + chat_id
  -> minibot/adapters/config/schema.py              owner = [runtime].owner_id, a constant
  -> minibot/llm/tools/base.py:14                   tool context carries owner + raw IDs
```

Both entrypoints read that same constant: `dispatcher.py` passes it to the turn service,
and `adapters/tasks/worker.py` reads it directly for forked task workers. Nothing derives
ownership from `user_id` any more.

Future extension seam (not work for these phases):

```
minibot/app/extensions.py:197                       load_extensions()
  -> minibot/app/extensions.py:218                  ExtensionContext
  -> minibot/app/extensions.py:136                  mb.add_service(...)
```

## Phase 1 — Make the personal contract explicit

- [x] Document `primary` as the single personal profile in `config.example.toml:243`
      and the KV-memory configuration model in `minibot/adapters/config/schema.py:428`.
      Superseded by phase 2: the setting moved to `[runtime].owner_id`, because it
      owns graph data, scheduled jobs and the RAG corpus, not just this tool.
      `docs/config.rst` autoclasses both models, so the docstrings render in the docs.
- [x] Document channel terminology next to `ChannelMessage` in
      `minibot/core/channels.py:8`: `chat_id` identifies a conversation/delivery
      target; `user_id` is the channel's sender identifier, not MiniBot ownership.
- [x] Keep Telegram access deny-by-default:
      `minibot/adapters/messaging/telegram/authorization.py:8` must require an
      allowlisted owner user or chat whenever `require_authorized` is enabled.
      **Verified, not changed** — `authorization.py:29` already returns `False` when
      `require_authorized` is set with empty allowlists, and all four cases are covered
      by `tests/test_telegram_authorization.py`.
- [x] Add one functional regression proving that two turns in a configured personal
      chat retain history while another chat does not share it. Land it with the
      session behavior in `minibot/shared/utils.py:13` and the turn flow in
      `minibot/app/handlers/services/turn_service.py:91`.
      `tests/test_llm_turn_service.py::test_turn_service_keeps_history_per_chat_for_one_owner`.

Done when a fresh operator can configure one trusted Telegram user/chat and knows
exactly which data is shared across that owner's chats.

## Phase 2 — Keep personal state boring

- [x] Preserve the existing stable profile owner (`primary`); do not derive ownership
      from a caller-supplied field. Ownership now comes only from `[runtime].owner_id`:
      `TurnService._resolve_owner_id` and its message-derived fallbacks are gone, and
      `[tools.kv_memory].default_owner_id` is rejected at load time with a message
      naming the new key, so an old config fails loudly instead of silently reverting.
      **This fixed a live bug.** `worker._resolve_owner_id(task)` never read config at
      all and returned `str(task["user_id"])`, so a `spawn_task` worker wrote under the
      Telegram sender id while the main agent used `"primary"`. The relation graph is
      reachable from workers by design (see the module docstring in
      `minibot/extensions/tools/graph.py`) and `adapters/graph/sqlite.py` filters every
      query on `owner_id`, so edges a task recorded were invisible to the main agent —
      silently, since `require_owner()` was satisfied by either value. kv_memory, RAG
      and the scheduler escaped it only because they early-return for `entrypoint ==
      "worker"`.
- [x] Keep history session ownership separate from long-term tool ownership. Session
      helpers in `minibot/shared/utils.py` no longer take `user_id` at all: a session is
      `channel` + `chat_id`. For every real channel `chat_id` is always set, so existing
      history keys are unchanged. No tenant, account or user tables were added.
- [ ] Review the enabled tools in the personal deployment for local-machine safety
      (filesystem, shell, network, and MCP), using their existing configuration
      switches rather than inventing per-user permissions.

Done when MiniBot has one clear owner and predictable per-chat conversational
history, with no dormant multi-user policy.

## Phase 3 — Optional local OpenAI-compatible control API

Only start this when a local client actually needs it.

- [ ] Implement it as an optional channel extension, parallel to
      `minibot/extensions/channels/telegram.py:6`; it must use
      `ExtensionContext.add_service()` in `minibot/app/extensions.py:136`.
- [ ] Bind locally by default and require one operator-managed secret before accepting
      requests off-host. This identifies the one trusted owner; it is not a tenant API.
- [ ] Support only non-streaming `/v1/chat/completions` first. Treat submitted
      `messages` as authoritative. Use an explicit custom conversation ID for
      server-held history; do not reinterpret OpenAI's caller-supplied `user` as
      authorization or a MiniBot account.
- [ ] Give each HTTP request a direct, correlated turn result. Do not wait for an
      uncorrelated `OutboundEvent` from `minibot/app/dispatcher.py:240`, because
      concurrent requests can share a chat ID.
- [ ] Add one end-to-end request test with authentication and two conversation IDs.

Done when a trusted local client can call MiniBot without changing its personal-owner
model. Streaming, client-supplied tools, public exposure, and OpenAI Responses API
compatibility stay out of scope.

## Phase 4 — Future: a genuinely multi-user extension

Do not begin this because a client happens to send `user`. Begin only when a real
untrusted or independently owned deployment needs isolation.

- [ ] Introduce authenticated `tenant_id`, opaque `actor_id`, and explicit
      `conversation_id` as a coherent identity model before exposing data.
- [ ] Scope history by tenant + conversation, long-term memory by tenant + actor,
      and scheduled/task data by their required owner scope.
- [ ] Change the integer transport-ID assumptions in `minibot/core/channels.py:8`,
      `minibot/core/tasks.py:17`, and the SQLite task/scheduler models before accepting
      opaque external identifiers.
- [ ] Keep that behavior in an opt-in extension or separate deployment profile; the
      personal default remains one trusted owner.

Trigger: a second person needs a private memory namespace that the first person's
credentials or a fabricated request field cannot access.
