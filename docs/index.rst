minibot
=======

Your personal AI assistant for Telegram — self-hosted, auditable, and intentionally opinionated.

MiniBot is a lightweight personal AI assistant you run on your own infrastructure.
It is built for people who want reliable automation and chat assistance without a giant
platform footprint: Telegram-first, SQLite-first, async-first.

Why self-host
-------------

- **Privacy & ownership**: transcripts, KV notes, and scheduled prompts live in your
  instance (SQLite files), not a third-party service.
- **Cost & provider control**: pick where to route LLM calls and manage API usage independently.
- **Runtime control**: deploy behind your firewall, restrict outbound access, and run the
  daemon as an unprivileged user.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   getting_started
   agents
   architecture
   config
   tools
   scheduler
   audio
   mcp
   security
   prompts
