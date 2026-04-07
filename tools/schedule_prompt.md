# schedule_prompt

## Purpose

Creates a future prompt job for the current owner/chat context.

## Availability

Enabled when `[scheduler.prompts].enabled = true` and a prompt scheduler service is available.

## Configuration

Relevant config: `[scheduler.prompts]`, especially `min_recurrence_interval_seconds`.

## Interface

Inputs:

- `content`: message text to inject when due.
- `run_at`: optional ISO 8601 timestamp.
- `delay_seconds`: optional delay from now; required when `run_at` is absent.
- `role`: optional prompt role; allowed values are `user`, `system`, `developer`, and `agent`; defaults to `user`.
- `metadata`: optional metadata object.
- `recurrence_type`: optional recurrence mode.
- `recurrence_interval_seconds`: interval for recurring jobs.
- `recurrence_end_at`: optional ISO 8601 recurrence end timestamp.

The result includes `scheduled`, `job_id`, status, run time, and channel when successful.

## Safety Notes

Requires owner and channel context. Interval recurrence must respect the configured minimum interval.
