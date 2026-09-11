Extending MiniBot
=================

MiniBot is built to be customized, and most changes never touch Python. Everything
below the extension layer is file- or config-driven; Python extensions exist for
the parts that need real logic.

Customization ladder
--------------------

Work top-down: use the least powerful layer that does the job.

.. list-table::
   :header-rows: 1
   :widths: 10 30 30 12

   * - Level
     - What you can do
     - Where
     - Code?
   * - 0. Config
     - Enable/disable tools, pick providers and models, set limits and timeouts.
     - ``config.toml``
     - No
   * - 1. Content
     - Rewrite the system prompt, add policy/channel prompt packs, add skills,
       add specialist agent definitions.
     - ``prompts/``, ``skills/``, ``agents/*.md``
     - No
   * - 2. External tools
     - Connect any MCP server and expose its remote tools to the model.
     - ``[[tools.mcp.servers]]``
     - No
   * - 3. Python extensions
     - Add your own tools, react to internal events, run background services,
       contribute channels.
     - a Python module + ``[extensions].modules``
     - Yes

What you can plug in
--------------------

.. list-table::
   :header-rows: 1
   :widths: 22 40 38

   * - Surface
     - Mechanism
     - Example
   * - LLM tool
     - ``@mb.tool`` (name = function name, description = docstring, schema = first
       argument's pydantic model) or ``mb.add_tool()`` for a custom schema.
     - See "Your first tool" below.
   * - Event subscription
     - ``@mb.on(EventType)`` — see :doc:`events` for the full reference: what fires
       when and what each payload carries. Handlers are async and lossy: slow handlers
       drop events rather than blocking the pipeline.
     - :doc:`events`; ``examples/minibot_ext_demo.py`` logs turn completion.
   * - Background service
     - ``mb.add_service(service)`` — an object with async ``start()``/``stop()``,
       tied to the daemon or console lifecycle.
     - Anything that must run for the process lifetime.
   * - Channel
     - A channel extension module driven by a ``[channels.<name>]`` section
       (passed through verbatim to the extension that owns it). Only the daemon
       entrypoint drives channels.
     - The bundled Telegram channel.
   * - Provider
     - ``[providers.<name>]`` — API keys, base URLs, headers; per-agent routing
       from agent frontmatter.
     - See :doc:`config`.
   * - Scheduled prompt
     - ``[scheduler.prompts]`` — one-shot, fixed-interval, and cron recurrence.
     - See :doc:`scheduler`.

Your first tool
---------------

A tool is little more than a pydantic model plus a function. This one counts
words in any text:

.. code-block:: python

   # my_tool.py — any directory on PYTHONPATH
   from pydantic import BaseModel, Field

   from minibot.app.extensions import ExtensionContext
   from minibot.llm.tools.base import ToolContext


   class WordCountArgs(BaseModel):
       text: str = Field(description="Text to count words in.")


   def register(mb: ExtensionContext) -> None:
       @mb.tool
       async def word_count(args: WordCountArgs, context: ToolContext) -> dict[str, int]:
           """Count the words in a piece of text."""
           return {"words": len(args.text.split())}

Enable it and try it:

.. code-block:: toml

   [extensions]
   modules = ["my_tool"]

.. code-block:: console

   $ PYTHONPATH=. poetry run minibot console --once "How many words in 'lazy senior dev'?"

``@mb.tool`` uses the function name as the tool name, the docstring as the model-facing
description, and the first argument's pydantic model as the JSON schema. Invalid
arguments return ``invalid_tool_arguments`` to the model instead of reaching your handler.

Where extensions plug in
------------------------

.. mermaid::

   flowchart LR
       subgraph INPUTS["What you add"]
           direction TB
           modules["[extensions].modules"]
           content["prompts/  ·  skills/  ·  agents/*.md"]
           servers["[[tools.mcp.servers]]"]
       end
       subgraph OUTPUTS["What MiniBot gains"]
           direction TB
           tools["LLM tools"]
           events["Event subscribers"]
           services["Services"]
           channels["Channels"]
           prompt["System prompt · skill catalog · specialists"]
           remote["Remote MCP tools"]
       end
       modules --> tools
       modules --> events
       modules --> services
       modules --> channels
       content --> prompt
       servers --> remote

Each extension is an importable module exposing ``register(mb)``; local modules only need
their directory on ``PYTHONPATH``. Extensions are trusted in-process code — configure one
the way you would install a dependency.

Next steps
----------

- Write a full extension with config and an event subscriber: :doc:`extensions`.
- Define specialist agents in Markdown: :doc:`agents`.
- Override the prompt packs: :doc:`prompts`.
- Point MiniBot at an MCP server: :doc:`mcp`.
