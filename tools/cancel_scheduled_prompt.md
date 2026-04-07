# cancel_scheduled_prompt

## Purpose

Cancels a scheduled prompt job for the current owner/chat context.

## Availability

Enabled when `[scheduler.prompts].enabled = true` and a prompt scheduler service is available.

## Configuration

Relevant config: `[scheduler.prompts]`.

## Interface

Inputs:

- `job_id`: scheduled prompt job id.

The result includes the job id, cancellation flag, status, and run time when found.

## Safety Notes

Cancellation is scoped by owner, channel, chat, and user context.
