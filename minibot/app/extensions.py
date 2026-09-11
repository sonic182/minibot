from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_type_hints

from llm_async.models import Tool
from pydantic import BaseModel, ValidationError

from minibot.adapters.config.schema import Settings
from minibot.app.event_bus import EventBus, EventSubscription
from minibot.core.events import BaseEvent
from minibot.llm.tools.base import ToolBinding, ToolContext, ToolPayload
from minibot.shared.errors import ToolInputError

EventHandler = Callable[[Any], Awaitable[None]]
ToolFunc = Callable[[Any, ToolContext], Awaitable[Any]]
type ExtensionEntrypoint = Literal["daemon", "console", "worker"]


def _bundled_modules(entrypoint: ExtensionEntrypoint) -> tuple[str, ...]:
    common = (
        "minibot.extensions.integrations.rag",
        "minibot.extensions.integrations.mcp",
        "minibot.extensions.integrations.rabbitmq",
        "minibot.extensions.services.scheduler",
        "minibot.extensions.services.tasks",
        "minibot.extensions.tools.execution",
        "minibot.extensions.tools.media",
        "minibot.extensions.tools.memory",
        "minibot.extensions.tools.network",
        "minibot.extensions.tools.utility",
        "minibot.extensions.tools.workspace",
    )
    if entrypoint == "daemon":
        return ("minibot.extensions.channels.telegram", *common)
    if entrypoint == "console":
        return common
    # Workers retain their deliberately narrower assembly in adapters.tasks.worker.
    # Only user-configured extensions are loaded there.
    return ()


class ExtensionService(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass
class ExtensionContext:
    """Handed to an extension's ``register(mb)`` function.

    Deliberately narrow: the container is a class-level singleton, and passing it
    whole would make every one of its fields public API.

    ``entrypoint`` is ``"daemon"``, ``"console"``, or ``"worker"``. Channel extensions must
    contribute nothing outside the daemon: those entrypoints do not drive channel services.
    """

    name: str
    config: dict[str, Any]
    settings: Settings
    event_bus: EventBus
    logger: logging.Logger
    entrypoint: ExtensionEntrypoint = "daemon"
    tools: list[ToolBinding] = field(default_factory=list)
    subscriptions: list[tuple[type[BaseEvent], EventHandler]] = field(default_factory=list)
    services: list[ExtensionService] = field(default_factory=list)

    def on(self, event_type: type[BaseEvent], handler: EventHandler | None = None) -> Any:
        """Subscribe to ``event_type``. Usable directly or as a decorator."""
        if handler is not None:
            self.subscriptions.append((event_type, handler))
            return None

        def decorator(func: EventHandler) -> EventHandler:
            self.subscriptions.append((event_type, func))
            return func

        return decorator

    def tool(self, func: ToolFunc) -> ToolFunc:
        """Register ``func`` as a tool: name from ``__name__``, description from its docstring,
        parameters from its first argument's pydantic model.

        For anything this does not cover — a name that is not a Python identifier, a
        hand-written schema, a description loaded from a file — build the ``ToolBinding``
        yourself and pass it to ``add_tool``.
        """
        first = next(iter(inspect.signature(func).parameters), None)
        try:
            model = get_type_hints(func).get(first) if first else None
        except NameError as exc:
            # ``from __future__ import annotations`` makes every annotation a string that
            # get_type_hints resolves against module globals, so a model defined inside
            # register() cannot be found. Say that, instead of a bare "name X is not defined".
            raise ValueError(
                f"tool {func.__name__!r}: could not resolve its argument annotation ({exc}). "
                "Define the pydantic model at module level, not inside register()."
            ) from exc
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise ValueError(f"tool {func.__name__!r}: first argument must be annotated with a pydantic model")
        description = inspect.getdoc(func)
        if not description:
            raise ValueError(f"tool {func.__name__!r}: needs a docstring, it becomes the tool description")

        async def handler(payload: ToolPayload, context: ToolContext) -> Any:
            try:
                args = model.model_validate(payload)
            except ValidationError as exc:
                # Typed code, so the model is told to fix its arguments rather than
                # getting an opaque tool_execution_failed.
                raise ToolInputError(str(exc), error_code="invalid_tool_arguments") from exc
            return await func(args, context)

        self.add_tool(
            ToolBinding(
                tool=Tool(name=func.__name__, description=description, parameters=model.model_json_schema()),
                handler=handler,
            )
        )
        return func

    def add_tool(self, bindings: ToolBinding | Sequence[ToolBinding]) -> None:
        if isinstance(bindings, ToolBinding):
            self.tools.append(bindings)
            return
        self.tools.extend(bindings)

    def add_service(self, service: ExtensionService) -> None:
        self.services.append(service)


class ExtensionRegistry:
    """Everything the loaded extensions contributed, with a service-shaped lifecycle."""

    def __init__(self, contexts: Sequence[ExtensionContext], logger: logging.Logger) -> None:
        self._contexts = list(contexts)
        self._logger = logger
        self._subscriptions: list[EventSubscription] = []
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def tools(self) -> list[ToolBinding]:
        return [binding for context in self._contexts for binding in context.tools]

    def names(self) -> list[str]:
        return [context.name for context in self._contexts]

    def is_empty(self) -> bool:
        return not self._contexts

    async def start(self) -> None:
        for context in self._contexts:
            for event_type, handler in context.subscriptions:
                subscription = context.event_bus.subscribe(types=(event_type,), lossy=True)
                self._subscriptions.append(subscription)
                self._tasks.append(asyncio.create_task(self._drive(context.name, subscription, handler)))
        for context in self._contexts:
            for service in context.services:
                await service.start()

    async def stop(self) -> None:
        for context in self._contexts:
            for service in context.services:
                with contextlib.suppress(Exception):
                    await service.stop()
        for subscription in self._subscriptions:
            with contextlib.suppress(Exception):
                await subscription.close()
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._subscriptions.clear()
        self._tasks.clear()

    async def _drive(self, name: str, subscription: EventSubscription, handler: EventHandler) -> None:
        async for event in subscription:
            try:
                await handler(event)
            except Exception as exc:  # noqa: BLE001
                # One bad handler must not kill the subscription or the extension.
                self._logger.exception(
                    "extension event handler failed",
                    exc_info=exc,
                    extra={"extension": name, "event_type": event.event_type},
                )


def load_extensions(
    settings: Settings,
    event_bus: EventBus,
    logger: logging.Logger | None = None,
    entrypoint: ExtensionEntrypoint = "daemon",
) -> ExtensionRegistry:
    """Import and register the bundled extensions, then every ``[extensions] modules`` entry.

    Failures are fatal by design: a tool that silently fails to load leaves the agent
    quietly unable to do something, which is far harder to diagnose than a boot crash.
    """
    log = logger or logging.getLogger("minibot.extensions")
    contexts: list[ExtensionContext] = []
    for name in (*_bundled_modules(entrypoint), *settings.extensions.modules):
        try:
            module = importlib.import_module(name)
        except Exception as exc:
            raise ValueError(f"extension {name!r} could not be imported: {exc}") from exc
        register = getattr(module, "register", None)
        if not callable(register):
            raise ValueError(f"extension {name!r} does not define a callable register(mb)")
        context = ExtensionContext(
            name=name,
            config=dict(settings.extensions.config.get(name, {})),
            settings=settings,
            event_bus=event_bus,
            logger=logging.getLogger(f"minibot.extensions.{name}"),
            entrypoint=entrypoint,
        )
        try:
            register(context)
        except Exception as exc:
            raise ValueError(f"extension {name!r} failed during register(): {exc}") from exc
        contexts.append(context)
        log.info(
            "extension loaded",
            extra={
                "extension": name,
                "tools": [binding.tool.name for binding in context.tools],
                "subscriptions": len(context.subscriptions),
                "services": len(context.services),
            },
        )
    return ExtensionRegistry(contexts, log)
