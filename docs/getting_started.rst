Getting Started
===============

Quickstart (Docker)
-------------------

1. ``cp config.example.toml config.toml``
2. Populate secrets: ``channels.telegram.bot_token``, allowlists, and provider credentials under ``[providers.<name>]``.
3. ``mkdir -p logs data``
4. ``docker compose up --build -d``
5. ``docker compose logs -f minibot``

``docker-compose.yml`` mounts ``config.toml`` by default.
``config.yolo.toml`` is a reference template with all tools enabled (file storage, STT, HTTP/KV tools, MCP bridge, unrestricted Python runtime, unrestricted Bash, and patch-based file editing).

The Docker image includes:

- Python deps with all MiniBot extras (``stt``, ``mcp``)
- Node.js/npm (v24 from official tarball)
- Playwright + Chromium
- ffmpeg
- additional Python packages from ``docker-requirements.txt``

Quickstart (Poetry)
-------------------

1. ``poetry install --all-extras``
2. ``cp config.example.toml config.toml``
3. Populate secrets: bot token, allowed chat IDs, provider credentials under ``[providers.<name>]``.
4. ``poetry run minibot``

Up & Running with Telegram
--------------------------

1. Open `@BotFather <https://t.me/BotFather>`_ on Telegram and create a bot to obtain a token.
2. Update ``config.toml``:

   - set ``channels.telegram.bot_token``
   - add your Telegram ID to ``allowed_chat_ids`` or ``allowed_user_ids``
   - configure ``[llm]`` (``provider``, ``model``) and ``[providers.<provider>]`` credentials

3. Run ``poetry run minibot`` and send a message to your bot.
4. Monitor ``logs/`` (logfmt via ``logfmter``) for structured output.

Console Test Channel
--------------------

Use the built-in console channel to test through the same dispatcher pipeline without Telegram.

.. code-block:: bash

   # Interactive REPL
   poetry run minibot-console

   # One-shot
   poetry run minibot-console --once "hello"

   # Read from stdin
   echo "hello" | poetry run minibot-console --once -

Using Ollama (OpenAI-Compatible API)
-------------------------------------

MiniBot works with Ollama via its OpenAI-compatible endpoints.

1. Start Ollama and pull a model::

    ollama serve
    ollama pull qwen3.5:35b

2. Configure ``config.toml`` — ``openai`` provider example:

.. code-block:: toml

   [llm]
   provider = "openai"
   model = "qwen3.5:35b"

   [providers.openai]
   api_key = "dummy"
   base_url = "http://localhost:11434/v1"

``openai_responses`` provider example:

.. code-block:: toml

   [llm]
   provider = "openai_responses"
   model = "qwen3.5:35b"

   [providers.openai_responses]
   api_key = "dummy"
   base_url = "http://localhost:11434/v1"

Notes:

- Use ``/v1`` as the base path; trailing slash is normalized automatically.
- When ``base_url`` uses ``http://``, HTTP/2 is disabled automatically.
- ``api_key`` must be non-empty (use ``"dummy"`` for Ollama); an empty key triggers echo mode.
- If a model fails under ``openai_responses``, switch to ``openai`` first.
