Configuration Reference
=======================

MiniBot is configured via a ``config.toml`` file.
Start from the provided example::

    cp config.example.toml config.toml

Values follow standard TOML syntax. Byte-size fields accept human-readable
strings (e.g. ``"64KB"``, ``"5MB"``) in addition to raw integers.

.. note::

   Tool-specific configuration (``[tools.*]``) is documented on the
   :doc:`tools` page.

----

Runtime
-------

.. autoclass:: minibot.adapters.config.schema.RuntimeConfig
   :no-members:

Channels
--------

.. autoclass:: minibot.adapters.config.schema.TelegramChannelConfig
   :no-members:

LLM
---

.. autoclass:: minibot.adapters.config.schema.LLMMConfig
   :no-members:

Providers
---------

.. autoclass:: minibot.adapters.config.schema.ProviderConfig
   :no-members:

Memory
------

.. autoclass:: minibot.adapters.config.schema.MemoryConfig
   :no-members:

Orchestration
-------------

.. autoclass:: minibot.adapters.config.schema.OrchestrationConfig
   :no-members:

Scheduler
---------

.. autoclass:: minibot.adapters.config.schema.ScheduledPromptsConfig
   :no-members:

Logging
-------

.. autoclass:: minibot.adapters.config.schema.LoggingConfig
   :no-members:

RabbitMQ
--------

.. autoclass:: minibot.adapters.config.schema.RabbitMQConsumerConfig
   :no-members:
