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

- Python deps with all MiniBot extras (``stt``, ``mcp``, ``rabbitmq``)
- Node.js/npm (v24 from official tarball)
- Playwright + Chromium
- ffmpeg
- additional Python packages from ``docker-requirements.txt``

``docker-compose.yml`` also starts Qdrant (used by the RAG tool). The ``minibot-rabbitmq`` service
is commented out by default: ``[tasks].backend`` defaults to ``"sqlite"``, which needs no broker.
Uncomment it (and its ``depends_on`` entry on the ``minibot`` service) only if you set
``[tasks].backend = "rabbitmq"``.

No Telegram bot yet? Run ``docker compose run --rm minibot minibot console`` instead of
``up`` to chat with MiniBot in your terminal — see `Console Test Channel`_ below.

Quickstart (pip)
----------------

1. ``pip install minibot``, adding extras as needed: ``pip install "minibot[mcp,stt,rabbitmq]"``.
2. ``minibot configure`` to create ``config.toml`` interactively (see below).
3. ``minibot``

No Telegram bot yet? Run ``minibot console`` instead to chat with MiniBot in your terminal —
see `Console Test Channel`_ below.

Quickstart (Poetry)
-------------------

1. ``poetry install --all-extras``
2. Run ``poetry run minibot configure`` to create or update ``config.toml`` interactively.
   New files start from the ``Example`` profile by default; ``YOLO`` enables broad host execution and integrations.
3. Configure Telegram, choose an LLM provider/model/API key, then select enabled tools with arrows and Space.
   If ``tasks`` is enabled, you'll also be asked for the task queue backend (``sqlite`` or ``rabbitmq``).
4. The configurator stores tokens and API keys in plain text; keep ``config.toml`` private.
5. ``poetry run minibot``

No Telegram bot yet? Run ``poetry run minibot console`` instead to chat with MiniBot in your
terminal — see `Console Test Channel`_ below.

Auto-Created Files & Directories
---------------------------------

Running ``minibot`` creates what it needs on first use — no manual ``mkdir`` required for:

- ``logs/`` — log output directory.
- ``data/*.db`` — SQLite storage for memory, KV notes, tasks, and scheduled prompts.
- ``[tools.file_storage].root_dir`` (default ``./data/files``) — managed file storage.

(The Docker Quickstart's ``mkdir -p logs data`` step above is about host-directory *ownership*
when Docker bind-mounts a path that doesn't exist yet — it isn't something minibot itself needs.)

One directory is the exception: ``./prompts`` (the system prompt and prompt fragments referenced
by ``llm.system_prompt_file`` / ``llm.prompts_dir``) is only provisioned by ``minibot configure``,
which seeds it next to the config file it writes (skipped if a ``prompts/`` directory already
exists there). If you hand-write ``config.toml`` instead of running the wizard, copy ``prompts/``
yourself — from the repo, or from wherever ``pip`` installed the package (the same place
``config.example.toml`` lands).

``orchestration.directory`` (default ``./agents``) and skill directories (``.agents/skills``,
``.claude/skills``) are optional in the other direction: if missing, minibot just runs with no
custom agents or skills — no error, nothing to create.

Up & Running with Telegram
--------------------------

If you'd rather test without Telegram first, skip to `Console Test Channel`_ below.

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
Interactive mode opens a minimal Textual TUI (markdown transcript on top, input pinned to
the bottom; Enter sends, Ctrl+J inserts a newline, Ctrl+T toggles model thinking); pass ``--plain`` for the original prompt loop.

.. code-block:: bash

   # Interactive TUI
   poetry run minibot console

   # Plain prompt loop
   poetry run minibot console --plain

   # One-shot
   poetry run minibot console --once "hello"

   # Read from stdin
   echo "hello" | poetry run minibot console --once -

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
