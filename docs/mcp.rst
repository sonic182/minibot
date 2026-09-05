MCP Bridge
==========

MiniBot can discover and expose remote `Model Context Protocol <https://modelcontextprotocol.io>`_
tools as local tool bindings at startup.

.. note::

   Requires the ``mcp`` extra::

      poetry install --extras mcp

Tool Naming
-----------

For each configured server, MiniBot calls ``tools/list`` and exposes tool names as::

    <name_prefix>_<server_name>__<remote_tool_name>

Example: prefix ``mcp``, server ``dice_cli``, remote tool ``roll_dice`` → ``mcp_dice_cli__roll_dice``.

Configuration
-------------

.. code-block:: toml

   [tools.mcp]
   enabled = true
   name_prefix = "mcp"
   timeout_seconds = 10

Stdio transport example:

.. code-block:: toml

   [[tools.mcp.servers]]
   name = "dice_cli"
   transport = "stdio"
   command = "python"
   args = ["tests/fixtures/mcp/stdio_dice_server.py"]
   env = {}
   cwd = "."

HTTP transport example:

.. code-block:: toml

   [[tools.mcp.servers]]
   name = "dice_http"
   transport = "http"
   url = "http://127.0.0.1:8765/mcp"
   headers = {}

.. note::

   Browser automation no longer goes through an MCP server. It now drives the
   ``playwright-cli`` binary directly through the ``bash`` tool via a dedicated
   agent skill — see ``agents/browser_agent.md`` for the canonical setup
   (``tools_allow`` includes ``bash``, ``filesystem``, ``grep``, ``http_request``,
   ``pre_response``, ``wait``; no ``mcp_servers`` entry needed).

Tool Filtering
--------------

- ``enabled_tools`` — if empty, all discovered tools are allowed; if set, only listed remote tool names are exposed.
- ``disabled_tools`` — always excluded, even if also present in ``enabled_tools``.

Troubleshooting
---------------

- If discovery fails, startup logs include ``failed to load mcp tools`` with the server name.
- If the main agent answers without using tools (common with some OpenRouter models), set
  ``[orchestration].main_tool_use_guardrail = "llm_classifier"`` to enforce a tool-routing
  classification step before each final answer.
