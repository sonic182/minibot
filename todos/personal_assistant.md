# Personal-first MiniBot

Status: **proposed**

## Product decision

MiniBot is a self-hosted personal AI assistant for one trusted owner profile. It is
not a multi-tenant service. A channel's `user_id` remains the raw sender ID used for
channel authorization and audit context; it is not an application account or a data
namespace. The existing `default_owner_id = "primary"` is the one profile that owns
long-term memory, graph data, and scheduled work.

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
  -> minibot/shared/utils.py:13                     session = channel + chat_id (or user_id)
  -> minibot/app/handlers/services/turn_service.py:377
                                                    owner = configured "primary"
  -> minibot/llm/tools/base.py:14                   tool context carries raw IDs
```

Future extension seam (not work for these phases):

```
minibot/app/extensions.py:197                       load_extensions()
  -> minibot/app/extensions.py:218                  ExtensionContext
  -> minibot/app/extensions.py:136                  mb.add_service(...)
```

## Phase 1 — Make the personal contract explicit

- [ ] Document `primary` as the single personal profile in `config.example.toml:243`
      and the KV-memory configuration model in `minibot/adapters/config/schema.py:428`.
- [ ] Document channel terminology next to `ChannelMessage` in
      `minibot/core/channels.py:8`: `chat_id` identifies a conversation/delivery
      target; `user_id` is the channel's sender identifier, not MiniBot ownership.
- [ ] Keep Telegram access deny-by-default:
      `minibot/adapters/messaging/telegram/authorization.py:8` must require an
      allowlisted owner user or chat whenever `require_authorized` is enabled.
- [ ] Add one functional regression proving that two turns in a configured personal
      chat retain history while another chat does not share it. Land it with the
      session behavior in `minibot/shared/utils.py:13` and the turn flow in
      `minibot/app/handlers/services/turn_service.py:91`.

Done when a fresh operator can configure one trusted Telegram user/chat and knows
exactly which data is shared across that owner's chats.

## Phase 2 — Keep personal state boring

- [ ] Preserve the existing stable profile owner (`primary`) in
      `minibot/app/handlers/services/turn_service.py:377`; do not derive ownership
      from a caller-supplied API `user` field.
- [ ] Keep history session ownership in `minibot/shared/utils.py:13` separate from
      long-term tool ownership in `minibot/llm/tools/base.py:14`. Do not add tenant,
      account, or user tables.
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
