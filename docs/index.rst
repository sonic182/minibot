Minibot — Self-Hosted AI Assistant for Telegram
================================================

.. meta::
   :description: Minibot is a lightweight, open-source, self-hosted AI assistant for Telegram, built in Python — with persistent memory, tools, skills, scheduling, MCP, and multi-agent orchestration.
   :keywords: self-hosted AI assistant, Telegram AI assistant, Python AI agent, open-source AI bot, Telegram bot, MCP, persistent memory, multi-agent, AI automation

**A lightweight Python AI agent with persistent memory, tools, skills, scheduling, MCP,
and multi-agent orchestration.**

Minibot is an open-source, self-hosted AI assistant for Telegram. It runs on your own
infrastructure — Telegram-first, SQLite-first, async-first — so your chats, memory, and
scheduled automations stay in your instance, not a third-party SaaS.

Project repository: `sonic182/minibot <https://github.com/sonic182/minibot>`_.

What Minibot can do
-------------------

- **Persistent memory** — durable facts and preferences, stored in SQLite.
- **Agent tools** — Python, Bash, HTTP, file storage, grep, and patch-based editing.
- **Skills and agents** — file-based skills plus specialist agents with delegation.
- **Scheduling** — one-shot, interval, and cron prompts for Telegram automations.
- **MCP integration** — connect external Model Context Protocol servers.
- **RAG** — index documents into SQLite (or Qdrant) for retrieval-augmented answers.
- **Browser automation** — Playwright-driven browsing and screenshots.
- **Voice** — optional speech-to-text for audio messages.

Why self-host
-------------

- **Privacy & ownership**: transcripts, KV notes, and scheduled prompts live in your
  instance (SQLite files), not a third-party service.
- **Cost & provider control**: pick where to route LLM calls and manage API usage independently.
- **Runtime control**: deploy behind your firewall, restrict outbound access, and run the
  daemon as an unprivileged user.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   getting_started

.. toctree::
   :maxdepth: 2
   :caption: Extending MiniBot

   extending
   extensions
   events
   agents
   prompts
   mcp

.. toctree::
   :maxdepth: 2
   :caption: Reference

   architecture
   config
   tools
   scheduler
   audio
   rag
   graph
   security
