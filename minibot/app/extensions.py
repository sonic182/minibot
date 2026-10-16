from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, get_type_hints

from pydantic import BaseModel, ValidationError

from minibot.app.agent_registry import AgentRegistry
from minibot.app.event_bus import EventBus, EventSubscription
from minibot.config.schema import Settings
from minibot.core.events import BaseEvent
from minibot.core.secrets import SecretVault
from minibot.core.tools import ToolContext, ToolPayload
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.extension import build_extension_tool_binding
from minibot.shared.errors import ToolInputError

if TYPE_CHECKING:
    from minibot.app.agent_management import AgentManagementService

EventHandler = Callable[[Any], Awaitable[None]]
ToolFunc = Callable[[Any, ToolContext], Awaitable[Any]]
type ToolDeriver = Callable[[Sequence[ToolBinding]], Sequence[ToolBinding]]
type ExtensionEntrypoint = Literal["daemon", "console", "worker"]
type RouteSpec = tuple[str, Callable[[Any], Awaitable[Any]], tuple[str, ...]]


WORKER_OPT_IN_MODULES = {
    "kv_memory": "minibot.extensions.tools.memory",
    "rag": "minibot.extensions.integrations.rag",
    "codemode": "minibot.extensions.tools.codemode",
}


def _bundled_modules(entrypoint: ExtensionEntrypoint, worker_extensions: Sequence[str] = ()) -> tuple[str, ...]:
    common = (
        "minibot.extensions.integrations.decision",
        "minibot.extensions.integrations.rag",
        "minibot.extensions.integrations.mcp",
        "minibot.extensions.integrations.rabbitmq",
        "minibot.extensions.services.scheduler",
        "minibot.extensions.services.tasks",
        "minibot.extensions.tools.agent_management",
        "minibot.extensions.tools.codemode",
        "minibot.extensions.tools.execution",
        "minibot.extensions.tools.media",
        "minibot.extensions.tools.memory",
        "minibot.extensions.tools.network",
        "minibot.extensions.tools.utility",
        "minibot.extensions.tools.vault",
        "minibot.extensions.tools.workspace",
    )
    if entrypoint == "daemon":
        return ("minibot.extensions.channels.telegram", *common)
    if entrypoint == "console":
        return common
    # Workers retain their deliberately narrower tool assembly: the scheduler and utility tools, the
    # bundles opted in through [tasks].worker_extensions, plus user-configured extensions.
    return (
        "minibot.extensions.services.scheduler",
        "minibot.extensions.tools.utility",
        *(WORKER_OPT_IN_MODULES[name] for name in worker_extensions),
    )


class ExtensionService(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(frozen=True)
class _PromptFragment:
    text: str
    tool_names: frozenset[str] = frozenset()


@dataclass
class ExtensionContext:
    """Handed to an extension's ``register(mb)`` function.

    Deliberately narrow: the container is a class-level singleton, and passing it
    whole would make every one of its fields public API.

    ``entrypoint`` is ``"daemon"``, ``"console"``, or ``"worker"``. Channel extensions must
    contribute nothing outside the daemon: those entrypoints do not drive channel services.

    ``vault`` is the unlocked credential vault, or ``None`` when ``[vault] enabled`` is false.
    Resolve a secret only into a destination the admin configured; never expose one to the LLM.
    """

    name: str
    config: dict[str, Any]
    settings: Settings
    event_bus: EventBus
    logger: logging.Logger
    entrypoint: ExtensionEntrypoint = "daemon"
    vault: SecretVault | None = None
    # The container's own registry, updated in place by token auto-config after registration —
    # so read specs off it when they are needed, not at register() time.
    agent_registry: AgentRegistry | None = None
    # Present only when [orchestration.agent_management] turns on reload and/or writes.
    agent_management: AgentManagementService | None = None
    refresh_tools: Callable[[], Awaitable[None]] | None = None
    tools: list[ToolBinding] = field(default_factory=list)
    tool_derivers: list[ToolDeriver] = field(default_factory=list)
    subscriptions: list[tuple[type[BaseEvent], EventHandler]] = field(default_factory=list)
    services: list[ExtensionService] = field(default_factory=list)
    routes: list[RouteSpec] = field(default_factory=list)
    prompt_fragments: list[_PromptFragment] = field(default_factory=list)
    pages: list[tuple[str, str, str]] = field(default_factory=list)

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
            build_extension_tool_binding(
                name=func.__name__,
                description=description,
                parameters=model.model_json_schema(),
                handler=handler,
            )
        )
        return func

    def add_tool(self, bindings: ToolBinding | Sequence[ToolBinding]) -> None:
        if isinstance(bindings, ToolBinding):
            self.tools.append(bindings)
            return
        self.tools.extend(bindings)

    def add_tool_deriver(self, deriver: ToolDeriver) -> None:
        """Contribute tools built from the tool list an agent ends up with.

        ``add_tool`` registers before any agent exists, so a tool that must act on other tools (call
        them, describe them) cannot be built there. ``deriver`` runs once per agent, with the final
        list: after approval, event and output-spill wrapping, ``tools_allow``/``tools_deny`` and the
        removal of the delegation tools. It returns the extra bindings, which are then approval- and
        event-wrapped like any tool and filtered by the same agent policy. The input list does not
        contain them, and it is rebuilt whenever the tool list is, so a deriver must be a pure function
        of its argument. Runs in the daemon, the console and task workers.
        """
        self.tool_derivers.append(deriver)

    def add_service(self, service: ExtensionService) -> None:
        self.services.append(service)

    def add_route(
        self, path: str, handler: Callable[[Any], Awaitable[Any]], methods: Sequence[str] = ("GET",)
    ) -> None:
        """Serve ``handler`` at ``path`` on the built-in HTTP server (``[http] enabled``).

        The handler takes a ``starlette.requests.Request`` and returns a ``Response``. Routes are
        collected once every extension has registered, so ordering between extensions is irrelevant.
        """
        self.routes.append((path, handler, tuple(methods)))

    def add_prompt_fragment(self, text: str, *, tool_names: Sequence[str] = ()) -> None:
        """Append ``text`` to the main agent's system prompt.

        For guidance the model needs before it picks a tool and that no single tool description can
        carry -- an MCP server's ``instructions``, a backend's conventions. Tool-specific wording
        belongs in that tool's description, not here: this is paid for on every single turn. When
        ``tool_names`` is set, the fragment is included only if one of those tools is attached.
        """
        text = text.strip()
        if text:
            self.prompt_fragments.append(_PromptFragment(text=text, tool_names=frozenset(tool_names)))

    def add_page(
        self,
        path: str,
        label: str,
        handler: Callable[[Any], Awaitable[Any]],
        methods: Sequence[str] = ("GET",),
        *,
        icon: str = "file",
    ) -> None:
        """Like ``add_route``, plus an entry in the web UI's navigation menu under ``label``.

        ``icon`` is a Lucide icon name (https://lucide.dev/icons), shown next to the label.
        An extension that bails out of ``register()`` never calls this, so a disabled extension
        drops out of the menu on its own — the menu needs no separate notion of what is enabled.
        """
        self.add_route(path, handler, methods)
        self.pages.append((path, label, icon))


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

    def tools_outside(self, names: Collection[str]) -> list[ToolBinding]:
        return [binding for context in self._contexts if context.name not in names for binding in context.tools]

    def derive_tools(self, final_tools: Sequence[ToolBinding]) -> list[ToolBinding]:
        """Run every ``add_tool_deriver`` function over ``final_tools`` and return only what they add.

        A derived tool whose name already exists (say an MCP server exposing ``run_tools_script``) is
        skipped with a warning and the existing tool is kept: this runs on every tool refresh and in
        workers, so raising would break turns at runtime.
        """
        existing = {binding.tool.name for binding in final_tools}
        derived: list[ToolBinding] = []
        for context in self._contexts:
            for deriver in context.tool_derivers:
                for binding in deriver(final_tools):
                    if binding.tool.name in existing:
                        self._logger.warning(
                            "extension %r derived tool %r, which already exists; keeping the existing tool",
                            context.name,
                            binding.tool.name,
                        )
                        continue
                    existing.add(binding.tool.name)
                    derived.append(binding)
        return derived

    @property
    def routes(self) -> list[RouteSpec]:
        return [route for context in self._contexts for route in context.routes]

    @property
    def prompt_fragments(self) -> list[str]:
        return [fragment.text for context in self._contexts for fragment in context.prompt_fragments]

    def prompt_fragments_for(self, tools: Sequence[ToolBinding]) -> list[str]:
        tool_names = {binding.tool.name for binding in tools}
        return [
            fragment.text
            for context in self._contexts
            for fragment in context.prompt_fragments
            if not fragment.tool_names or fragment.tool_names & tool_names
        ]

    def pages(self) -> list[tuple[str, str, str]]:
        return [page for context in self._contexts for page in context.pages]

    def names(self) -> list[str]:
        return [context.name for context in self._contexts]

    def summaries(self) -> list[dict[str, Any]]:
        """What each extension actually contributed. A disabled extension still imports and runs
        ``register()``, so being loaded says nothing: only these counts tell active from idle.
        """
        return [
            {
                "name": context.name,
                "tools": len(context.tools),
                "tool_derivers": len(context.tool_derivers),
                "services": len(context.services),
                "subscriptions": len(context.subscriptions),
                "routes": len(context.routes),
            }
            for context in self._contexts
        ]

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
    vault: SecretVault | None = None,
    agent_registry: AgentRegistry | None = None,
    agent_management: AgentManagementService | None = None,
    refresh_tools: Callable[[], Awaitable[None]] | None = None,
) -> ExtensionRegistry:
    """Import and register the bundled extensions, then every ``[extensions] modules`` entry.

    Failures are fatal by design: a tool that silently fails to load leaves the agent
    quietly unable to do something, which is far harder to diagnose than a boot crash.
    """
    log = logger or logging.getLogger("minibot.extensions")
    contexts: list[ExtensionContext] = []
    for name in (*_bundled_modules(entrypoint, settings.tasks.worker_extensions), *settings.extensions.modules):
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
            vault=vault,
            agent_registry=agent_registry,
            agent_management=agent_management,
            refresh_tools=refresh_tools,
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
                "routes": len(context.routes),
            },
        )
    return ExtensionRegistry(contexts, log)
