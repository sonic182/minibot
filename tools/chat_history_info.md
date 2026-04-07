# chat_history_info

## Purpose

Reports the current conversation history state for the active channel/chat/user session.

## Availability

Always available when MiniBot builds tools. It uses the configured `[memory]` backend, not `[tools.kv_memory]`.

## Configuration

Relevant config lives under `[memory]`, especially `max_history_messages`. If `max_history_messages` is unset, the tool reports it as null.

## Interface

Input is an empty object. The result includes `session_id`, `total_messages`, and `max_history_messages`.

## Safety Notes

Requires channel context. It does not expose message contents and does not mutate history.
