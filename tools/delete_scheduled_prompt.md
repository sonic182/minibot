# delete_scheduled_prompt

## Purpose

Deletes a scheduled prompt job for the current owner/chat context.

## Availability

Enabled when `[scheduler.prompts].enabled = true` and a prompt scheduler service is available.

## Configuration

Relevant config: `[scheduler.prompts]`.

## Interface

Inputs:

- `job_id`: scheduled prompt job id.

The result includes deletion status and whether the job was stopped before deletion.

## Safety Notes

Active jobs are cancelled before deletion. Deletion is scoped by owner, channel, chat, and user context.
