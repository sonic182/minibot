CLI Reference
=============

.. meta::
   :description: Minibot command-line interface — run the daemon, chat through the console TUI, configure MiniBot, manage the credential vault, and sign in to ChatGPT Codex.
   :keywords: minibot CLI, self-hosted AI assistant commands, console TUI, config wizard, credential vault, ChatGPT Codex

MiniBot installs a single ``minibot`` entry point that dispatches to five commands.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Command
     - What it does
   * - ``minibot``
     - Start the daemon — the long-running Telegram channel service.
   * - ``minibot console``
     - Open the local console channel (no Telegram bot required).
   * - ``minibot configure``
     - Run the interactive wizard that writes ``config.toml``.
   * - ``minibot vault``
     - Create, edit, and list the encrypted credential vault.
   * - ``minibot codex login``
     - Authenticate a ChatGPT Codex subscription; use ``--device-code`` on a headless host.

Configuration file
------------------

Every command resolves ``config.toml`` the same way: an explicit ``--config PATH`` when the command
accepts one, otherwise the ``MINIBOT_CONFIG`` environment variable, otherwise ``./config.toml``.
If no file is found, built-in defaults are used.

Run the daemon
--------------

.. code-block:: bash

   minibot

Runs in the foreground and logs to ``logs/``. ``SIGINT``/``SIGTERM`` trigger a graceful shutdown
that stops the dispatcher and extensions. On boot it replays turns that were interrupted by a
previous shutdown or crash. There is no ``--config`` flag: set ``MINIBOT_CONFIG`` to point at a
different file.

Console channel
---------------

.. code-block:: bash

   minibot console                # interactive Textual TUI
   minibot console --plain        # plain prompt loop
   minibot console --once "hello" # send one message and exit
   echo "hello" | minibot console --once -   # read the message from stdin

.. list-table::
   :header-rows: 1
   :widths: 26 74

   * - Flag
     - Description
   * - ``--once TEXT``
     - Send one message and exit. Use ``-`` to read the message from stdin.
   * - ``--plain``
     - Use the plain prompt loop instead of the Textual TUI.
   * - ``--chat-id INT``
     - Chat id for the console session (default ``1``).
   * - ``--user-id INT``
     - User id for the console session (default ``1``).
   * - ``--timeout-seconds FLOAT``
     - Per-turn timeout; values below ``120`` are clamped to ``120``.
   * - ``--config PATH``
     - Path to a ``config.toml`` file.
   * - ``--verbose``
     - Also log to stdout in addition to the log file.

Interactive TUI keys: ``Enter`` sends, ``Ctrl+J`` inserts a newline, and ``Ctrl+T`` toggles display
of model thinking.

Configuration wizard
--------------------

.. code-block:: bash

   minibot configure
   minibot configure --config path/to/config.toml

Walks through Telegram, providers, models, tool selection, the HTTP server (dashboard and browser
chat), and — when tasks are enabled — the task queue backend. The provider step takes as many targets as
you want, writes each one as its own ``[providers.<name>]`` section, and then asks which of them the main
agent runs on; the others stay available for delegation (see :doc:`providers` and :doc:`agents`). Model
lists longer than a couple of dozen entries are filtered by a search prompt before the picker opens.
New files start from the ``Example`` profile; ``YOLO`` enables broad host execution
and integrations — file storage, HTTP/KV tools, MCP bridge, unrestricted Python and Bash, and
patch-based file editing, all on with nothing gated behind a confirmation step.

What makes ``YOLO`` a good idea isn't whether MiniBot can reach personal accounts — email,
social media, whatever else — it's the model behind it. Point it at something you'd actually
trust to act on those accounts unsupervised: a capable local model through Ollama, or a frontier
provider under a zero-data-retention agreement, or one you otherwise trust with that level of
access (OpenAI, Anthropic, Google, ...). A weak or untrusted model with the full toolset is the
combination to avoid, not personal data as such.

Secrets are written in plain text, so keep ``config.toml`` private. The wizard also seeds
``prompts/`` next to the config file when that directory does not already exist.

Credential vault
----------------

.. code-block:: bash

   minibot vault init               # create ./secrets.vault.yml
   minibot vault edit               # decrypt into $EDITOR, re-encrypt on exit
   minibot vault list               # print secret names, never values

Each command takes an optional vault path (default ``secrets.vault.yml``) and
``--password-file PATH``. Without it the password comes from ``MINIBOT_VAULT_PASSWORD``, and
failing that an interactive prompt — the recommended method, since no password material reaches
disk or the process environment.

``edit`` decrypts into a ``0600`` temporary file, runs ``$EDITOR`` (falling back to ``$VISUAL``
then ``vi``), and re-encrypts on exit. The edited document is parsed *before* it is encrypted, so
a syntax error leaves the existing vault untouched. Its format is one ``name: value`` per line;
values are never type-coerced, and a value needing whitespace, newlines, or a leading ``#``/``-``
is written JSON-quoted:

.. code-block:: yaml

   github: ghp_example
   numeric_key: 12345
   private_key: "-----BEGIN KEY-----\nabc\n-----END KEY-----"

See :doc:`security` for how a stored secret is bound to a destination, and ``[vault]`` in
:doc:`config` for the daemon-side settings.
