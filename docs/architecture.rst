Architecture
============

MiniBot uses a lightweight hexagonal layout:

- ``core/`` — domain contracts and models
- ``app/`` — orchestration (dispatcher, event bus, agent runtime)
- ``adapters/`` — infrastructure (config, messaging, memory, scheduler)
- ``llm/`` — provider factory and tool schemas/handlers

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
