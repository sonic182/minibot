MCP Integration
===============

.. meta::
   :description: Connect Model Context Protocol (MCP) servers to Minibot and expose their remote tools to your AI agent.
   :keywords: MCP AI agent, Telegram MCP bot, Model Context Protocol, MCP tools

MiniBot can connect remote `Model Context Protocol <https://modelcontextprotocol.io>`_ servers in
either eager bridge mode or lazy mode. Bridge mode exposes every remote tool at startup; lazy mode
keeps the model's initial tool surface small and loads full remote schemas only when needed.

No extra is required: the MCP client speaks JSON-RPC to the server process directly and has no
third-party MCP SDK dependency. Only the test fixtures use the ``mcp`` package.

Tool Naming
-----------

Bridge mode (the default) calls ``tools/list`` for each configured server and exposes tool names as::

    <name_prefix>_<server_name>__<remote_tool_name>

Example: prefix ``mcp``, server ``dice_cli``, remote tool ``roll_dice`` → ``mcp_dice_cli__roll_dice``.

Lazy mode exposes two bindings per server instead::

    <name_prefix>_<server_name>__list_tools
    <name_prefix>_<server_name>__call_tool

The lazy binding descriptions include the server's MCP initialization instructions when available.
Call ``list_tools`` to retrieve the remote names, descriptions, and complete input schemas, then
call ``call_tool`` with the selected remote name and arguments. MiniBot initializes lazy servers at
startup to obtain that metadata, but defers ``tools/list`` until the catalog is requested.

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
   mode = "bridge"
   transport = "stdio"
   command = "python"
   args = ["tests/fixtures/mcp/stdio_dice_server.py"]
   env = {}
   cwd = "."

HTTP transport example:

.. code-block:: toml

   [[tools.mcp.servers]]
   name = "dice_http"
   mode = "lazy"
   transport = "http"
   url = "http://127.0.0.1:8765/mcp"
   headers = {}
   catalog_cache_ttl_seconds = 60

``mode`` defaults to ``"bridge"`` for compatibility. For ``"lazy"`` servers,
``catalog_cache_ttl_seconds`` controls automatic catalog discovery before a direct ``call_tool``;
set it to ``0`` to disable the cache. An explicit ``list_tools`` call always refreshes the catalog.

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

Lazy catalogs and lazy calls apply the same filters. A direct lazy call discovers the catalog first
when needed and rejects a disabled or unavailable remote tool before sending ``tools/call``.

Troubleshooting
---------------

- If eager discovery fails, startup logs include ``failed to load mcp tools`` with the server name.
- If lazy metadata discovery fails, MiniBot keeps the lazy bindings with a configured-server fallback
  description; catalog and call requests retry the connection later.
- If the main agent answers without using tools (common with some OpenRouter models), set
  ``[orchestration].main_tool_use_guardrail = "llm_classifier"`` to enforce a tool-routing
  classification step before each final answer.
