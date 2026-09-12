Extension API Reference
=======================

.. meta::
   :description: Reference for the Minibot Python extension API — ExtensionContext, ToolBinding, ToolContext, and the event classes you can subscribe to.
   :keywords: minibot extension API, Python AI agent extension, ExtensionContext, ToolBinding

This page is the reference companion to :doc:`extensions` and :doc:`events`. It documents the
objects an extension's ``register(mb)`` function receives and the types it works with.

``register(mb)`` is called once at startup with an :class:`~minibot.app.extensions.ExtensionContext`
built from the module's ``[extensions.config.<module>]`` section. Import and registration failures
are fatal — a module that cannot load stops startup rather than leaving the agent silently unable
to use its tools.

ExtensionContext
----------------

.. autoclass:: minibot.app.extensions.ExtensionContext
   :members:
   :undoc-members:

ExtensionService
----------------

Objects passed to ``mb.add_service`` implement this protocol. They are started when the entrypoint
starts and stopped on shutdown.

.. autoclass:: minibot.app.extensions.ExtensionService
   :members:
   :undoc-members:

Tools
-----

.. autoclass:: minibot.llm.tools.base.ToolBinding
   :members:
   :undoc-members:

.. autoclass:: minibot.llm.tools.base.ToolContext
   :members:
   :undoc-members:

Events
------

Subscribe to any of these with ``@mb.on(EventType)`` or ``mb.on(EventType, handler)``. See
:doc:`events` for when each one fires and what its payload carries. Handlers are async and lossy:
a slow handler drops events rather than blocking the pipeline.

.. automodule:: minibot.core.events
   :members:
   :undoc-members:
   :exclude-members: BaseEvent
