# self_insert_artifact

## Purpose

Injects a managed file or image into the runtime context so the model can analyze it in-loop.

## Availability

Enabled by `[tools.file_storage].enabled = true`.

## Configuration

Relevant config: `[tools.file_storage]`, especially `root_dir` and `allow_outside_root`.

## Interface

Inputs:

- `path`: relative managed file path.
- `as`: either `image` or `file`.
- `role`: optional target role, `user` or `system`; defaults to `user`.
- `text`: optional text prepended before the injected part.
- `mime`: optional MIME hint.
- `filename`: optional display filename for file mode.

The result includes a status payload and append-message directives when insertion succeeds.

## Safety Notes

Image mode rejects non-image MIME types. The hidden legacy alias `artifact_insert` is normalized to this tool at execution time.
