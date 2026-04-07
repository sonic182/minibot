# chat_history_trim

## Purpose

Trims stored chat history for the active channel/chat/user session.

## Availability

Always available when MiniBot builds tools. It uses the configured `[memory]` backend, not `[tools.kv_memory]`.

## Configuration

Relevant config lives under `[memory]`, especially `max_history_messages` and the selected history backend.

## Interface

Inputs:

- `keep_latest`: non-negative integer count of latest messages to keep; `0` clears all messages for the current conversation.

The result includes `session_id`, `keep_latest`, `removed_messages`, `remaining_messages`, and `max_history_messages`.

## Safety Notes

This mutates conversation history for the current context only. Requires channel context.
