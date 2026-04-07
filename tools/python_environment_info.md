# python_environment_info

## Purpose

Inspects the Python runtime used by `python_execute`.

## Availability

Available whenever `[tools.python_exec].enabled = true`.

## Configuration

Relevant config: `[tools.python_exec]`, especially interpreter selection (`python_path`, `venv_path`) and sandbox settings.

## Interface

Inputs:

- `include_packages`: when true, list installed packages.
- `limit`: maximum packages to return.
- `name_prefix`: optional case-insensitive package prefix filter.

The result includes Python version, executable path, sandbox mode, and optionally package metadata.

## Safety Notes

This tool executes a bounded probe through the same configured Python runtime. It is read-oriented but still depends on host Python process execution.
