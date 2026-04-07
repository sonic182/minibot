# python_execute

## Purpose

Executes Python source code on the MiniBot host with configurable timeout, output limits, sandbox controls, and optional artifact export.

## Availability

Enabled by default through `[tools.python_exec].enabled = true`.

## Configuration

Relevant config: `[tools.python_exec]`, `[tools.python_exec.rlimit]`, `[tools.python_exec.cgroup]`, and `[tools.python_exec.jail]`.

Important fields include `python_path`, `venv_path`, `sandbox_mode`, `default_timeout_seconds`, `max_timeout_seconds`, `max_output_bytes`, `max_code_bytes`, `artifacts_enabled`, artifact limits, `pass_parent_env`, and `env_allowlist`.

Artifact export requires `[tools.file_storage].enabled = true`. In `sandbox_mode = "jail"`, export also requires `artifacts_allow_in_jail = true` and `artifacts_jail_shared_dir`.

## Interface

Inputs:

- `code`: Python source code to execute.
- `stdin`: optional stdin text.
- `timeout_seconds`: optional per-call timeout.
- `save_artifacts`: whether to save generated files into managed storage.
- `artifact_globs`: optional glob patterns selecting generated files.
- `artifact_subdir`: destination subdirectory under managed storage.
- `max_artifacts`: optional per-run artifact count cap.

The result includes execution status, exit information, stdout/stderr, timeout/truncation details, duration, sandbox mode, and Python executable path.

## Safety Notes

This is host code execution. Keep the safest practical sandbox mode enabled and restrict environment inheritance unless the deployment explicitly needs broader access.
