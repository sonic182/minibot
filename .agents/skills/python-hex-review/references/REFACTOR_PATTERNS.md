# Refactor Patterns

## Thin handler

Before:
- A handler parses input, decides policy, loads persistence details, and calls tools or providers directly.

After:
- The handler normalizes input and delegates to an app service.
- The app service coordinates the flow.
- Concrete side effects stay in adapters or `minibot.llm`.

## Introduce or narrow a contract

Before:
- An app service imports a concrete SQLAlchemy, Telegram, or provider implementation.

After:
- The app service depends on a protocol or small contract.
- The concrete implementation stays under `minibot.adapters` or `minibot.llm`.

## Contain external payloads

Before:
- Raw provider or transport payloads move through multiple modules.

After:
- Map external payloads at the boundary into internal models, DTOs, or normalized structures.

## Split a mixed module

Before:
- One module contains orchestration, persistence, and formatting.

After:
- Keep orchestration in `minibot.app`.
- Keep persistence or transport code in `minibot.adapters`.
- Keep provider/tool-specific request handling in `minibot.llm`.

## Protect `shared`

Before:
- Generic helper modules absorb app or adapter policy because they are easy to import.

After:
- Move policy back to the owning layer.
- Leave only truly generic helpers in `minibot.shared`.
