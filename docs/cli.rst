CLI Reference
=============

.. meta::
   :description: Minibot command-line interface — run the daemon, chat through the console TUI, and run the interactive configuration wizard.
   :keywords: minibot CLI, self-hosted AI assistant commands, console TUI, config wizard

MiniBot installs a single ``minibot`` entry point that dispatches to three commands.

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

Walks through Telegram, provider, model, tool selection, and — when tasks are enabled — the task
queue backend. New files start from the ``Example`` profile; ``YOLO`` enables broad host execution
and integrations. Secrets are written in plain text, so keep ``config.toml`` private. The wizard
also seeds ``prompts/`` next to the config file when that directory does not already exist.
