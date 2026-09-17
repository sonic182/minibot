Getting Started
===============

.. meta::
   :description: Install and run Minibot — Docker, pip, and Poetry quickstarts, Telegram setup, the console test channel, and Ollama configuration.
   :keywords: install AI assistant Telegram, self-hosted AI bot, minibot quickstart, Ollama OpenAI compatible

Quickstart (Docker)
-------------------

1. Create ``config.toml`` with the interactive wizard. It has to run on the host, not in the
   container: ``docker-compose.yml`` bind-mounts ``config.toml`` read-only, so the file must
   already exist before ``docker compose up`` starts.

   .. code-block:: bash

      pipx run minibot configure
      # no pipx? pip install minibot into a throwaway venv and run `minibot configure` there instead.

   Same wizard as `Quickstart (pip)`_ / `Quickstart (Poetry)`_ below — Telegram token and
   allowlists, provider/model, and tool selection.
2. ``mkdir -p logs data``
3. ``docker compose up --build -d``
4. ``docker compose logs -f minibot``

``config.yolo.toml`` is the all-capabilities-enabled reference template — file storage, STT,
HTTP/KV tools, MCP bridge, unrestricted Python runtime, unrestricted Bash, and patch-based file
editing, all on with nothing gated behind a confirmation step. ``minibot configure`` offers the
same profile interactively when creating a new file; see :doc:`cli` for what it actually takes to
run it safely. The static file itself is there if you'd rather copy or hand-edit it directly.

The Docker image includes:

- Python deps with all MiniBot extras (``telegram``, ``stt``, ``rag``, ``rabbitmq``, ``graph``)
- Node.js/npm (v24 from official tarball)
- Playwright + Chromium
- ffmpeg
- additional Python packages from ``docker-requirements.txt``

The ``minibot-rabbitmq`` and ``minibot-qdrant`` services are commented out by default: both
``[tasks].backend`` and ``[tools.rag].backend`` default to ``"sqlite"``, which needs no extra
service. Uncomment one (and its ``depends_on`` entry on the ``minibot`` service) only if you set
the matching backend to ``"rabbitmq"`` or ``"qdrant"``.

No Telegram bot yet? Run ``docker compose run --rm minibot minibot console`` instead of
``up`` to chat with MiniBot in your terminal — see `Console Test Channel`_ below.

Quickstart (pip)
----------------

1. ``pip install minibot``, adding extras as needed: ``pip install "minibot[telegram,stt,rag,rabbitmq,graph]"``.
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

``orchestration.directory`` (default ``./agents``) and skill directories (``.minibot/skills``,
``.agents/skills``) are optional in the other direction: if missing, minibot just runs with no
custom agents — and with only the skills bundled in the package.

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

Provider setup
--------------

See :doc:`providers` for OpenAI, Anthropic, Google, OpenRouter, ChatGPT Codex, OpenCode, xAI, Z.AI,
Ollama, and other OpenAI-compatible endpoint configuration.
