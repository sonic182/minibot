# schedule

## Purpose

Unified scheduled-prompt management facade.

## Availability

Enabled when `[scheduler.prompts].enabled = true` and a prompt scheduler service is available.

## Configuration

Relevant config: `[scheduler.prompts]`, especially `min_recurrence_interval_seconds`.

## Interface

Inputs:

- `action`: one of `create`, `list`, `cancel`, or `delete`.
- `job_id`: required for `cancel` and `delete`.
- `content`, `run_at`, `delay_seconds`, `role`, `metadata`, `recurrence_type`, `recurrence_interval_seconds`, `recurrence_end_at`: used for `create`; `role` can be `user`, `system`, `developer`, or `agent` and defaults to `user`.
- `active_only`, `limit`, `offset`: used for `list`.

## Safety Notes

Scheduling is scoped by owner, channel, chat, and user context. For single-purpose calls, the granular scheduler tools expose the same operations separately.
