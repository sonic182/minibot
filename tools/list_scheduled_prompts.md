# list_scheduled_prompts

## Purpose

Lists scheduled prompt jobs for the current owner/chat context.

## Availability

Enabled when `[scheduler.prompts].enabled = true` and a prompt scheduler service is available.

## Configuration

Relevant config: `[scheduler.prompts]`.

## Interface

Inputs:

- `active_only`: optional filter for active jobs; defaults to true.
- `limit`: optional page size; defaults to 20.
- `offset`: optional page offset; defaults to 0.

The result includes `active_only`, count, and job summaries.

## Safety Notes

Results are scoped by owner, channel, chat, and user context.
