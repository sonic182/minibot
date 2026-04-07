# http_request

## Purpose

Fetches an HTTP or HTTPS resource and returns a bounded response payload.

## Availability

Enabled by `[tools.http_client].enabled = true`.

## Configuration

Relevant config: `[tools.http_client]`.

Important fields include `timeout_seconds`, `max_bytes`, `response_processing_mode`, `max_chars`, `normalize_whitespace`, `spill_to_managed_file`, `spill_after_chars`, `spill_preview_chars`, `max_spill_bytes`, and `spill_subdir`.

If response spillover is enabled, managed-file spillover also requires `[tools.file_storage].enabled = true`.

## Interface

Inputs:

- `method`: HTTP method, defaulting to `GET`; supported values are `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, and `OPTIONS`.
- `url`: absolute `http://` or `https://` URL.
- `headers`: optional string-valued header object.
- `body`: optional UTF-8 request body.
- `json`: optional JSON payload encoded as a string; mutually exclusive with `body`.

The result includes `status`, `headers`, `body`, truncation flags, processor metadata, content type, and optional managed-file fields for spilled bodies.

## Safety Notes

This tool performs outbound network requests from the MiniBot host. The hidden legacy alias `http_client` is normalized to this tool at execution time.
