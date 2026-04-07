# MCP Dynamic Tools

## Purpose

Expose remote Model Context Protocol server tools as local LLM tool bindings.

## Availability

Enabled by `[tools.mcp].enabled = true` with one or more `[[tools.mcp.servers]]` entries.

## Configuration

Relevant config: `[tools.mcp]` and `[[tools.mcp.servers]]`.

Important fields include `name_prefix`, `timeout_seconds`, server `name`, `transport`, `command`, `args`, `env`, `cwd`, `url`, `headers`, `enabled_tools`, and `disabled_tools`.

For server name `playwright-cli`, MiniBot injects `--output-dir` from `[tools.browser].output_dir`.

## Interface

Generated local names follow:

```text
<name_prefix>_<server_name>__<remote_tool_name>
```

With default prefix `mcp`, names look like `mcp_playwright-cli__browser_snapshot`.

Remote parameters and descriptions come from the server's `tools/list` response.

## Safety Notes

MCP tools expose external capabilities. Use `enabled_tools` and `disabled_tools` to enforce least privilege and restrict specialist exposure with `mcp_servers` or tool allow/deny rules.
