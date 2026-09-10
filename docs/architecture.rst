Architecture
============

MiniBot uses a lightweight hexagonal layout:

- ``core/`` — domain contracts and models
- ``app/`` — orchestration (dispatcher, event bus, agent runtime, extension loader)
- ``adapters/`` — infrastructure (config, messaging, memory, scheduler, tasks)
- ``llm/`` — provider factory and tool schemas/handlers
- ``extensions/`` — bundled extensions that compose channels, integrations, services, and tools
- ``rag/`` — chunking, embeddings, reranking, and retrieval for the Qdrant-backed RAG tools

Dependency direction points inward: ``app``/``adapters`` depend on ``core``, never the reverse.

.. seealso::

   ``ARCHITECTURE.md`` in the repository root is the full architecture reference (repository
   layout, message flow, task/extension subsystems). This page is a summary.

Entry Point
-----------

``minibot.app.daemon`` bootstraps ``AppContainer``, wires dependencies, starts
the dispatcher and channel services, and handles graceful shutdown via signal
handlers.

Message Flow
------------

1. Inbound event arrives (Telegram webhook or console input)
2. Published to the internal ``asyncio`` event bus
3. Dispatcher routes to LLM pipeline
4. Agent runtime calls tools as needed
5. Response emitted back to the channel adapter

Subsystems
----------

- **Extensions** — ``[extensions].modules`` boot Python modules that contribute tools, event
  subscribers, and services via an ``ExtensionContext``; see :doc:`extensions`.
- **Tasks** — long-running work is queued to the ``[tasks]`` backend (SQLite by default) and
  drained by a background consumer service.
- **RAG** — optional Qdrant-backed indexing and semantic retrieval; see :doc:`rag`.
