# memory

## Purpose

Stores and retrieves durable user memory entries such as preferences, project facts, important dates, and user-requested remembered information.

## Availability

Enabled by `[tools.kv_memory].enabled = true`. The tool is omitted if the key-value memory backend is not configured.

## Configuration

Relevant config: `[tools.kv_memory]`.

Important fields include `sqlite_url`, `pool_size`, `echo`, `default_limit`, `max_limit`, and `default_owner_id`.

## Interface

Inputs:

- `action`: one of `save`, `get`, `search`, `delete`, or `list_titles`.
- `entry_id`: entry id for `get` or `delete`.
- `title`: entry title for `save`, `get`, or `delete`.
- `data`: entry content for `save`.
- `query`: search query for `search` or title filter for `list_titles`.
- `metadata`: optional JSON metadata for `save`.
- `source`: optional source string for `save`.
- `expires_at`: optional ISO datetime for `save`.
- `limit`, `offset`: pagination controls where applicable.

## Safety Notes

Memory is scoped by `owner_id` from tool context. This tool is not for transcript storage; use chat-history tools for conversation history.
