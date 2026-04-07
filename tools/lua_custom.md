# Lua Custom Tools

## Purpose

Loads custom tool manifests from Lua files.

## Availability

Enabled by `[tools.lua_custom].enabled = true`. Requires the Lua extra to be installed.

## Configuration

Relevant config: `[tools.lua_custom]`.

Important field: `directory`, defaulting to `./lua_tools`.

## Interface

Each `*.lua` file in the configured directory must return one manifest with:

- `name`: public tool name.
- `description`: tool description.
- `parameters`: JSON Schema object.
- `handler(args)`: function that returns JSON-like result data.

The concrete tool names are defined by the Lua manifests and are not known statically by MiniBot docs.

## Safety Notes

Lua tool code is local extension code. Treat the configured directory as trusted code and document custom tools near their Lua manifests if they are deployment-specific.
