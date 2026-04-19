Getting Started
===============

Installation
------------

Using Poetry::

    pip install poetry
    poetry install --all-extras

Using Docker::

    docker compose up

Configuration
-------------

Copy the example config and fill in your credentials::

    cp config.example.toml config.toml

Key fields in ``config.toml``:

- ``[telegram]`` — bot token and allowed user IDs
- ``[llm]`` — provider and model settings
- ``[memory]`` — SQLite path for conversation history

Running
-------

Daemon (Telegram)::

    poetry run python -m minibot.app.daemon

Console (local test)::

    poetry run python -m minibot.app.console
