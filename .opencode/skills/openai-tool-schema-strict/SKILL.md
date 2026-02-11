---
name: openai-tool-schema-strict
description: Enforce strict OpenAI function tool JSON schema rules when defining tool parameters
compatibility: opencode
metadata:
  audience: ai-agents
  scope: tool-schema
---

## What I do
- Ensure every tool schema uses `parameters` with `type: object`, `properties`, `required`, and `additionalProperties: false`.
- Enforce OpenAI strict rule: `required` must include every key declared in `properties`, even nullable ones.
- Keep optional behavior via nullable types (for example `"type": ["string", "null"]`), not by omitting from `required`.
- Prevent common 400 errors like `invalid_function_parameters` and `Missing '<field>'`.

## Rules to apply
- For each property in `parameters.properties`, add the same key to `parameters.required`.
- If a field is logically optional, make it nullable and handle `null` in runtime validation.
- Always set `additionalProperties: false` unless there is an explicit requirement to allow arbitrary keys.
- Keep schema and handler aligned: if schema allows `null`, runtime coercion must accept `None`.

## Quick checklist before finishing
- Compare `set(properties.keys())` with `set(required)`; they must match exactly.
- Verify no extra names appear in `required`.
- Verify each tool has consistent defaults/coercion for nullable inputs.
- Run lint/tests relevant to tool files.

## Minimal pattern
```json
{
  "type": "object",
  "properties": {
    "input": { "type": "string" },
    "limit": { "type": ["integer", "null"], "minimum": 1 }
  },
  "required": ["input", "limit"],
  "additionalProperties": false
}
```

## Anti-pattern
```json
{
  "type": "object",
  "properties": {
    "input": { "type": "string" },
    "limit": { "type": ["integer", "null"] }
  },
  "required": ["input"]
}
```
