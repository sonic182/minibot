# current_datetime

## Purpose

Returns the current datetime in UTC.

## Availability

Enabled by default through `[tools.time].enabled = true`.

## Configuration

Relevant config: `[tools.time]`.

Important field: `default_format`, a Python `strftime` format string used when the call omits `format`.

## Interface

Inputs:

- `format`: optional Python `strftime` format string.

The result includes `timestamp`.

## Safety Notes

This tool is read-only. The hidden legacy alias `datetime_now` is normalized to this tool at execution time.
