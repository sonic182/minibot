Writing extensions
==================

.. meta::
   :description: Write Python extensions for Minibot — add LLM tools, subscribe to events, and register lifecycle services via register(mb).
   :keywords: minibot extension, Python AI agent plugin, custom LLM tools, ExtensionContext

An extension is an importable Python module with a module-level ``register(mb)``
function. It can add LLM tools, observe MiniBot events, and register services.
Configure its import name in ``config.toml``; a local module only needs to be on
``PYTHONPATH``.

Minimal useful extension
------------------------

This extension gives the model a validated ``github_repository`` tool. It reads a
repository through GitHub's API and returns only useful repository metadata.

.. code-block:: toml

   [extensions]
   modules = ["my_github_extension"]

   [extensions.config.my_github_extension]
   token_env = "GITHUB_TOKEN"

Set the token outside ``config.toml`` before starting MiniBot. A fine-grained token with
read-only access to the repositories you need is enough:

.. code-block:: console

   $ export GITHUB_TOKEN=github_pat_...

.. code-block:: python

   # my_github_extension.py
   from __future__ import annotations

   import json
   import os
   from typing import Any
   from urllib.parse import quote

   import aiosonic
   from pydantic import BaseModel, Field

   from minibot.app.extensions import ExtensionContext
   from minibot.llm.tools.base import ToolContext


   class GitHubRepositoryArgs(BaseModel):
       owner: str = Field(min_length=1, description="GitHub organization or user name.")
       repository: str = Field(min_length=1, description="Repository name.")


   def register(mb: ExtensionContext) -> None:
       token_env = str(mb.config.get("token_env", "GITHUB_TOKEN"))
       token = os.environ.get(token_env, "").strip()
       if not token:
           raise ValueError(f"GitHub token is missing from ${token_env}")
       # One async client per extension; every tool call reuses its connection pool.
       client = aiosonic.HTTPClient()

       @mb.tool
       async def github_repository(args: GitHubRepositoryArgs, _: ToolContext) -> dict[str, Any]:
           """Read metadata for a GitHub repository. Use this to check a repository's
           description, visibility, default branch, issue count, and last update time.
           """
           owner = quote(args.owner, safe="")
           repository = quote(args.repository, safe="")
           response = await client.request(
               f"https://api.github.com/repos/{owner}/{repository}",
               method="GET",
               headers={
                   "Accept": "application/vnd.github+json",
                   "Authorization": f"Bearer {token}",
                   "X-GitHub-Api-Version": "2022-11-28",
               },
           )
           if response.status_code != 200:
               raise RuntimeError(f"GitHub repository request failed: HTTP {response.status_code}")
           repository_data = json.loads((await response.content()).decode("utf-8"))
           return {
               "ok": True,
               "full_name": repository_data.get("full_name"),
               "description": repository_data.get("description"),
               "private": repository_data.get("private"),
               "default_branch": repository_data.get("default_branch"),
               "open_issues_count": repository_data.get("open_issues_count"),
               "updated_at": repository_data.get("updated_at"),
               "html_url": repository_data.get("html_url"),
           }

Run it from the directory containing ``my_github_extension.py``:

.. code-block:: console

   $ PYTHONPATH=. poetry run minibot console --once "What is the default branch of sonic182/minibot?"

``@mb.tool`` uses the function name as the tool name, the docstring as the model-facing
description, and the first argument's Pydantic model as the JSON schema. Invalid arguments
return ``invalid_tool_arguments`` to the model instead of reaching the handler.

Extension API
-------------

``mb.config``
   The extension's ``[extensions.config.<module>]`` dictionary. Prefer it for extension
   settings; ``mb.settings`` exposes the complete validated application configuration.

``mb.tool`` / ``mb.add_tool()``
   Add a typed decorator-based tool or explicit ``ToolBinding`` instances. Use ``add_tool``
   for a custom tool name or hand-written JSON schema.

``mb.add_tool_deriver(deriver)``
   Contribute tools built from the tool list an agent ends up with. ``add_tool`` registers before any agent
   exists, so it cannot build a tool that acts on other tools. ``deriver`` is a plain function that takes the
   agent's final ``Sequence[ToolBinding]`` (after approval, event and output-spill wrapping, ``tools_allow`` /
   ``tools_deny`` and the removal of the delegation tools) and returns the extra bindings. It runs once per agent
   and again whenever the tool list is rebuilt, so keep it a pure function of its argument. The returned tools get
   approval and event wrapping and are then filtered by the same agent policy (the main agent's allow/deny, a
   specialist's ``tools_allow``/``tools_deny``); they are not part of the list the deriver received, and a name
   that already exists is skipped with a warning and the existing tool is kept. Runs in the daemon, the console and task workers; the bundled
   ``run_tools_script`` (:doc:`tools`) is built this way.

   To take over tools instead of adding beside them, return ``DerivedTool(binding=..., replaces=frozenset(names))``
   (from ``minibot.app.extensions``) in place of a plain binding: while the derived tool survives the agent's
   policy, the tools named in ``replaces`` are no longer offered to the model, and the derived tool keeps calling
   them itself. ``run_tools_script`` replaces every tool its scripts can call.

``mb.add_tool_wrapper(wrapper)``
   Wrap the handler of every tool an agent can call, to check or change its calls and results. ``wrapper`` is a
   plain function ``(bindings, approve) -> bindings`` that returns one binding per input binding, in the same
   order and with the same names (keep ``spill_after_chars``, e.g. with ``dataclasses.replace``); anything else
   fails the tool build. ``approve(tool_name, arguments, context, detail=...)`` asks the user in the chat the call
   came from with the ``[tools.approval]`` buttons and returns ``True`` only on an explicit approval; it denies on
   channels without buttons. Wrappers cover core, extension, MCP and derived tools in the daemon, the console and
   task workers, and run again whenever the tool list is rebuilt, so keep them pure. They sit innermost: they see
   a tool's raw result before output spill, while ``[tools.approval]`` and tool-call events stay outside them.
   Calls nested in a ``run_tools_script`` script go through them too, with ``context.parent_call_id`` set. The
   bundled ``[tools.safety_classifier]`` (:doc:`tools`) is built this way.

``mb.on(EventType)`` / ``mb.on(EventType, handler)``
   Observe internal events such as ``TurnCompletedEvent``. Handlers are async and lossy:
   slow handlers drop events rather than blocking MiniBot.

``mb.add_service(service)``
   Register an object with async ``start()`` and ``stop()`` methods for work that belongs to
   the daemon or console lifecycle.

``mb.add_route(path, handler)`` / ``mb.add_page(path, label, handler, icon="file")``
   Serve a Starlette request handler on the built-in HTTP server (``[http] enabled``).
   ``add_page`` also adds a navigation entry under ``label``, drawn with the named
   `Lucide <https://lucide.dev/icons>`_ icon. Requires the ``http`` extra.

``mb.add_prompt_fragment(text, tool_names=())``
   Append a fragment to the main agent's system prompt, optionally gated on the listed tools
   being attached. Use it for guidance no single tool description can carry.

Operational rules
-----------------

- Extension import and registration failures stop startup. Tool names must be unique.
- Extensions are trusted in-process code. Treat adding a module to ``config.toml`` like
  installing a dependency.
- Define Pydantic argument models at module scope; with postponed annotations, models defined
  inside ``register()`` cannot be resolved.
- Check ``mb.entrypoint`` before adding channel services. Only the daemon runs channels.
- Configured extension tools are available to task workers, but worker registries do not start
  extension services or event subscriptions. A worker loads the scheduler, safety and utility bundles,
  plus the ones listed in ``[tasks] worker_extensions``, and they contribute tools and tool wrappers only.

For the full type signatures behind ``mb``, see :doc:`extension-api`.

See `minibot_ext_demo.py <https://github.com/sonic182/minibot/blob/main/examples/minibot_ext_demo.py>`_ for a tool plus a `TurnCompletedEvent <https://github.com/sonic182/minibot/blob/main/minibot/core/events.py>`_ subscriber.
