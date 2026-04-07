# bash

## Purpose

Runs shell commands through `/bin/bash -lc` for CLI workflows.

## Availability

Enabled by `[tools.bash].enabled = true`.

## Configuration

Relevant config: `[tools.bash]`.

Important fields include `default_timeout_seconds`, `max_timeout_seconds`, `max_output_bytes`, `pass_parent_env`, and `env_allowlist`.

## Interface

Inputs:

- `command`: Bash command to execute.
- `timeout_seconds`: optional per-call timeout override, clamped by `max_timeout_seconds`.
- `cwd`: optional working directory; defaults to the current process directory.
- `env`: optional string-valued environment overrides.

The result includes `ok`, `exit_code`, `stdout`, `stderr`, `timed_out`, `truncated`, `duration_ms`, `cwd`, and `command`.

## Safety Notes

This is host shell execution. Enable only for deployments that need direct shell access and constrain environment inheritance where possible.
