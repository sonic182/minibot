# read_file

## Purpose

Reads a managed text file.

## Availability

Enabled by `[tools.file_storage].enabled = true`.

## Configuration

Relevant config: `[tools.file_storage]`, especially `root_dir` and `allow_outside_root`.

## Interface

Inputs:

- `path`: relative file path under managed root.

The result includes the file content and canonical path metadata.

## Safety Notes

Use this for text files in managed storage. For bounded line reads, use `code_read`.
