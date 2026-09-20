from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path
from typing import Any, cast

import tomlkit
from prompt_toolkit import Application, choice, prompt
from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.widgets import CheckboxList, Label
from pydantic import ValidationError

from minibot.adapters.config.environment import expand_environment
from minibot.adapters.config.loader import resolve_config_path
from minibot.adapters.config.schema import (
    ENVIRONMENT_CHOICES,
    LLMMConfig,
    ProviderConfig,
    Settings,
    TelegramChannelConfig,
)
from minibot.app.llm_client_factory import available_providers
from minibot.llm.services.client_bootstrap import create_provider

_logger = logging.getLogger(__name__)
_MANUAL_MODEL = "Type a model name manually…"
_MODEL_FETCH_TIMEOUT_SECONDS = 10
_MODEL_FILTER_THRESHOLD = 25


@dataclass(frozen=True, slots=True)
class _ConfiguredProvider:
    """One provider the wizard just wrote, as the main-agent pick needs to see it."""

    label: str
    api_format: str
    models: list[str]


# Targets whose endpoint serves both Chat Completions and Responses on the same base URL.
_RESPONSES_OPTIONAL_TARGETS = frozenset({"opencode_zen", "opencode_go"})
# Each target is (label, default api_format, default base_url); the target key is the section name.
_LLM_TARGETS = {
    "openai": ("OpenAI API", "openai", ""),
    "openai_responses": ("OpenAI Responses API", "openai_responses", ""),
    "xai": ("xAI", "openai_responses", "https://api.x.ai/v1"),
    "zai": ("z.ai GLM Coding Plan", "openai", "https://api.z.ai/api/coding/paas/v4"),
    "opencode_zen": ("OpenCode Zen", "openai", "https://opencode.ai/zen/v1"),
    "opencode_go": ("OpenCode Go", "openai", "https://opencode.ai/zen/go/v1"),
    "chatgpt_codex": ("ChatGPT Codex subscription (OAuth)", "chatgpt_codex", ""),
}
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_ENVIRONMENTS = ENVIRONMENT_CHOICES
_TOOLS = {
    "memory": ("tools", "kv_memory"),
    "http": ("tools", "http_client"),
    "time": ("tools", "time"),
    "wait": ("tools", "wait"),
    "calculator": ("tools", "calculator"),
    "python": ("tools", "python_exec"),
    "bash": ("tools", "bash"),
    "patch": ("tools", "apply_patch"),
    "files": ("tools", "file_storage"),
    "grep": ("tools", "grep"),
    "audio": ("tools", "audio_transcription"),
    "skills": ("tools", "skills"),
    "tasks": ("tasks",),
    "rag": ("tools", "rag"),
    "mcp": ("tools", "mcp"),
    "spill": ("tools", "tool_output_spill"),
    "scheduler": ("scheduler", "prompts"),
}
_TOOL_DESCRIPTIONS = {
    "memory": "conversation history",
    "http": "fetch web pages",
    "time": "current time",
    "wait": "pause execution",
    "calculator": "arithmetic calculations",
    "python": "execute Python",
    "bash": "execute shell commands",
    "patch": "modify files safely",
    "files": "managed file storage",
    "grep": "search file contents",
    "audio": "transcribe Telegram audio",
    "skills": "load agent skills",
    "tasks": "background task queue",
    "rag": "semantic document search",
    "mcp": "external MCP tools",
    "spill": "save large outputs",
    "scheduler": "scheduled prompts",
}
# Opt-in extension rather than a [tools.*] section, so it is toggled in extensions.modules.
# Listing it there instead of bundling it is also what makes the tool reach task workers.
_GRAPH_MODULE = "minibot.extensions.tools.graph"

_KEEP = object()
_CLEAR = object()


def configure(path: Path) -> bool:
    document, profile = _load_document(path)
    settings = _settings_for_document(document)
    _write("\nMinibot configuration\n\n")
    _configure_runtime(document, settings)
    settings = _settings_for_document(document)
    _configure_telegram(document, settings.channels.telegram)
    settings = _settings_for_document(document)
    _configure_llm(document, settings)
    settings = _settings_for_document(document)
    _configure_tools(document, settings)
    settings = _settings_for_document(document)
    _configure_vault(document, settings)
    text = tomlkit.dumps(document)
    settings = Settings.from_dict(tomllib.loads(text))
    _write_summary(path, profile, settings)
    if not _ask_bool("Write this configuration", False):
        _write("No changes written.\n")
        return False
    _write_config(path, text)
    _write(f"Wrote {path}\n")
    _provision_prompts(path)
    return True


def _configure_runtime(document: Any, settings: Settings) -> None:
    log_level = _ask_single_select("Log level", _LOG_LEVELS, settings.runtime.log_level.upper())
    environment = _ask_single_select("Environment", _ENVIRONMENTS, settings.runtime.environment)
    _set_value(document, ("runtime", "log_level"), log_level)
    _set_value(document, ("runtime", "environment"), environment)
    _set_value(document, ("logging", "log_level"), log_level)


def _configure_telegram(document: Any, telegram: TelegramChannelConfig) -> None:
    enabled = _ask_bool("Enable Telegram", telegram.enabled)
    _set_value(document, ("channels", "telegram", "enabled"), enabled)
    if not enabled:
        return
    _set_value(
        document,
        ("channels", "telegram", "bot_token"),
        _ask_required_secret("Telegram bot token", telegram.bot_token),
    )
    _set_value(
        document,
        ("channels", "telegram", "allowed_chat_ids"),
        _ask_list_value("Allowed chat IDs", telegram.allowed_chat_ids, int),
    )
    _set_value(
        document,
        ("channels", "telegram", "allowed_user_ids"),
        _ask_list_value("Allowed user IDs", telegram.allowed_user_ids, int),
    )


def _configure_llm(document: Any, settings: Settings) -> None:
    selected = _ask_multiselect(
        "Providers to configure",
        [(target, f"{target} — {label}") for target, (label, _, _) in _LLM_TARGETS.items()],
        _configured_targets(settings),
    )
    if not selected:
        _write("No provider selected; leaving the LLM configuration unchanged.\n")
        return
    _release_legacy_sections(document, settings, selected)
    configured: dict[str, _ConfiguredProvider] = {}
    for target in _LLM_TARGETS:
        if target not in selected:
            continue
        provider = _configure_provider(document, settings, target)
        if provider is not None:
            configured[target] = provider
    if not configured:
        return
    main_target = _ask_main_provider(configured, _current_llm_target(settings))
    main = configured[main_target]
    _set_value(document, ("llm", "provider"), main_target)
    _set_value(document, ("llm", "model"), _choose_model(main.models, settings.llm.model))
    # Responses providers keep turn state server-side, so a tool loop can send just the delta instead
    # of resending the whole history every step. Chat Completions (openai, openrouter) is stateless and
    # resends regardless, and Codex forces store=False, so it can never reference a prior response.
    state_mode = "previous_response_id" if main.api_format == "openai_responses" else "full_messages"
    _set_value(document, ("llm", "main_responses_state_mode"), state_mode)
    _set_value(document, ("llm", "agent_responses_state_mode"), state_mode)


def _configure_provider(document: Any, settings: Settings, target: str) -> _ConfiguredProvider | None:
    """Write one ``[providers.<target>]`` section and return what the main-agent pick needs."""
    label, api_format, base_url = _LLM_TARGETS[target]
    _write(f"\n{label} → [providers.{target}]\n")
    if target == "chatgpt_codex":
        return _configure_chatgpt_codex(document, settings)
    provider_config = settings.providers.get(target, ProviderConfig())
    if target in _RESPONSES_OPTIONAL_TARGETS and _ask_bool(
        "Use Responses API", provider_config.api_format == "openai_responses"
    ):
        api_format = "openai_responses"
    base_url = provider_config.base_url or base_url
    api_key = _ask_secret("API key", provider_config.api_key)
    _set_value(document, ("providers", target, "api_format"), api_format)
    _set_value(document, ("providers", target, "api_key"), api_key)
    _set_value(document, ("providers", target, "base_url"), base_url)
    # OpenCode Go rejects requests without a session id (MissingSessionID); it only routes on it.
    session_header = ("providers", target, "headers", "x-opencode-session")
    if target == "opencode_go":
        _set_value(document, session_header, "minibot")
    else:
        _unset_value(document, session_header)
    resolved_api_key = provider_config.api_key
    if api_key != resolved_api_key:
        resolved_api_key = cast(str, expand_environment(api_key, os.environ, path=f"providers.{target}.api_key"))
    models = _provider_models(api_format, base_url, resolved_api_key)
    if models:
        _set_value(document, ("providers", target, "models"), _ask_models(models, provider_config.models))
    return _ConfiguredProvider(label=label, api_format=api_format, models=models)


def _release_legacy_sections(document: Any, settings: Settings, selected: set[str]) -> None:
    """Hand a third-party endpoint over from its format-named section to its own named section.

    Before named sections existed the wizard wrote, say, z.ai's ``base_url`` into
    ``[providers.openai]``. Left alone that section keeps a third-party key on a first-party name and
    the delegation roster offers it as a provider in its own right.
    """
    for target in selected:
        _, _, base_url = _LLM_TARGETS[target]
        if not base_url:
            continue
        for name, provider_config in settings.providers.items():
            if name == target or name in selected or provider_config.base_url != base_url:
                continue
            _unset_value(document, ("providers", name, "api_key"))
            _unset_value(document, ("providers", name, "base_url"))
            _unset_value(document, ("providers", name, "headers", "x-opencode-session"))
            _write(f"[providers.{name}] held {base_url}; moved it to [providers.{target}].\n")


def _configured_targets(settings: Settings) -> set[str]:
    targets = {
        name
        for name, provider_config in settings.providers.items()
        if name in _LLM_TARGETS and (provider_config.api_key or name == "chatgpt_codex")
    }
    targets.add(_current_llm_target(settings))
    return targets


def _ask_main_provider(configured: dict[str, _ConfiguredProvider], default: str) -> str:
    if len(configured) == 1:
        return next(iter(configured))
    return choice(
        "Main agent provider",
        options=[(target, provider.label) for target, provider in configured.items()],
        default=default if default in configured else next(iter(configured)),
    )


def _configure_tools(document: Any, settings: Settings) -> None:
    defaults = [name for name, tool_path in _TOOLS.items() if _tool_enabled(settings, tool_path)]
    if _GRAPH_MODULE in settings.extensions.modules:
        defaults.append("graph")
    _write("python, bash and patch can execute or modify files; grep enables files automatically.\n")
    _write("graph needs its extra installed: poetry install --extras graph\n")
    selected = _ask_multiselect(
        "Enabled tools",
        [
            *((name, f"{name} — {_TOOL_DESCRIPTIONS[name]}") for name in _TOOLS),
            ("graph", "graph — relations between entities, queried by traversal"),
        ],
        defaults,
    )
    if "grep" in selected:
        selected.add("files")
    for name, tool_path in _TOOLS.items():
        _set_value(document, (*tool_path, "enabled"), name in selected)
    _configure_graph_module(document, settings, enabled="graph" in selected)
    if "tasks" in selected:
        backend = _ask_single_select("Task queue backend", ("sqlite", "rabbitmq"), settings.tasks.backend)
        _set_value(document, ("tasks", "backend"), backend)
    if "rag" in selected:
        rag = settings.tools.rag
        backend = _ask_single_select("RAG vector backend", ("sqlite", "qdrant"), rag.backend)
        _set_value(document, ("tools", "rag", "backend"), backend)
        if backend == "qdrant":
            _set_value(document, ("tools", "rag", "qdrant_url"), _ask_required("Qdrant URL", rag.qdrant_url))
        else:
            _set_value(document, ("tools", "rag", "sqlite_url"), _ask_required("RAG SQLite URL", rag.sqlite_url))
    # Skills the model cannot see are skills it will not use; see SkillsToolConfig.
    if "skills" in selected:
        _set_value(document, ("tools", "skills", "preload_catalog"), True)
        _write("Installed skills are third-party instructions the assistant will follow.\n")
        install = _ask_bool("Let the assistant install published skills", settings.tools.skills.install)
        _set_value(document, ("tools", "skills", "install"), install)
    # The wizard keeps rerank tied to rag for simplicity; edit config.toml directly to decouple them.
    _set_value(document, ("tools", "rag", "rerank", "enabled"), "rag" in selected)


def _configure_vault(document: Any, settings: Settings) -> None:
    _write("\nThe vault stores credentials encrypted; the assistant can list their names, never read a value.\n")
    _write("Needs its extra installed: poetry install --extras vault\n")
    enabled = _ask_bool("Enable the credential vault", settings.vault.enabled)
    _set_value(document, ("vault", "enabled"), enabled)
    if not enabled:
        return
    path = _ask_required("Vault file", settings.vault.path)
    _set_value(document, ("vault", "path"), path)
    if not Path(path).expanduser().exists():
        _write(f"\nNo vault exists at {path}. Create it before starting minibot:\n")
        _write(f"  minibot vault init {path}\n")
        _write(f"  minibot vault edit {path}\n")
    _write("\nMinibot prompts for the vault password on startup. To run unattended, set\n")
    _write("[vault] password_file or MINIBOT_VAULT_PASSWORD — both weaker than the prompt.\n")


def _configure_graph_module(document: Any, settings: Settings, *, enabled: bool) -> None:
    modules = list(settings.extensions.modules)
    if enabled == (_GRAPH_MODULE in modules):
        return
    modules = [module for module in modules if module != _GRAPH_MODULE]
    if enabled:
        modules.append(_GRAPH_MODULE)
    _set_value(document, ("extensions", "modules"), modules)


def _provider_models(api_format: str, base_url: str, api_key: str) -> list[str]:
    return asyncio.run(_fetch_models(api_format, base_url, api_key))


def _filter_models(models: list[str], query: str) -> list[str]:
    terms = query.lower().split()
    if not terms:
        return list(models)
    return [model for model in models if all(term in model.lower() for term in terms)]


def _ask_model_query(models: list[str]) -> str:
    return prompt(
        f"Filter {len(models)} models (blank for all): ",
        completer=FuzzyWordCompleter(models),
        complete_while_typing=True,
    ).strip()


def _narrow_models(models: list[str]) -> list[str]:
    """Catalogs like OpenRouter's run into the hundreds; a checkbox list that long is unusable."""
    if len(models) <= _MODEL_FILTER_THRESHOLD:
        return models
    while True:
        narrowed = _filter_models(models, _ask_model_query(models))
        if narrowed:
            return narrowed
        _write("No model matched that filter.\n")


def _choose_model(models: list[str], current: str) -> str:
    if not models:
        return _ask_required("Model", current)
    narrowed = _narrow_models(models)
    options = [*narrowed, _MANUAL_MODEL]
    default = current if current in narrowed else narrowed[0]
    selected = choice("Model", options=[(value, value) for value in options], default=default)
    return _ask_required("Model", current) if selected == _MANUAL_MODEL else selected


def _ask_models(models: list[str], current: list[str]) -> list[str]:
    """Pick the advisory `models` roster the main agent may delegate to on this provider."""
    narrowed = _narrow_models(models)
    selected = _ask_multiselect(
        "Models to offer the main agent",
        [(model, model) for model in narrowed],
        [model for model in current if model in narrowed],
    )
    return sorted(selected)


def _configure_chatgpt_codex(document: Any, settings: Settings) -> _ConfiguredProvider | None:
    from minibot.app.codex_setup import CodexDependencyError

    provider_config = settings.providers.get("chatgpt_codex", ProviderConfig())
    auth_path = _resolve_codex_auth_path(provider_config.auth_path)
    try:
        credentials = _ensure_codex_login(auth_path)
    except CodexDependencyError:
        _write(
            "llm-async-codex is required for this provider. Install with "
            "`poetry install --extras codex` or `poetry install --all-extras` and rerun.\n"
        )
        return None
    _set_value(document, ("providers", "chatgpt_codex", "api_format"), "chatgpt_codex")
    models = _codex_models(credentials)
    if models:
        _set_value(document, ("providers", "chatgpt_codex", "models"), _ask_models(models, provider_config.models))
    return _ConfiguredProvider(
        label=_LLM_TARGETS["chatgpt_codex"][0],
        api_format="chatgpt_codex",
        models=models,
    )


def _ensure_codex_login(auth_path: Path) -> Any:
    from minibot.app.codex_setup import CodexCredentialsError, load_codex_credentials, login_to_codex

    try:
        return load_codex_credentials(auth_path)
    except CodexCredentialsError:
        pass
    _write(f"Not logged in to ChatGPT Codex yet (looked for {auth_path}).\n")
    device_code = _ask_bool("Use device-code login (no local browser needed)", False)
    _write("Starting Codex login" + (" (device code)...\n" if device_code else " (browser)...\n"))
    return asyncio.run(login_to_codex(device_code=device_code, auth_path=auth_path))


def _codex_models(credentials: Any) -> list[str]:
    from minibot.app.codex_setup import list_codex_model_slugs

    try:
        return asyncio.run(list_codex_model_slugs(credentials))
    except Exception:
        _logger.debug("Could not list Codex models", exc_info=True)
        _write("Could not fetch the Codex model list; enter the model name manually.\n")
        return []


def _resolve_codex_auth_path(auth_path: str | None) -> Path:
    from minibot.app.codex_setup import resolve_codex_auth_path

    return resolve_codex_auth_path(auth_path)


async def _fetch_models(provider: str, base_url: str, api_key: str) -> list[str]:
    provider_client, _ = create_provider(LLMMConfig(provider=provider, api_key=api_key, base_url=base_url or None))
    try:
        payload = await asyncio.wait_for(
            provider_client.request("GET", "/models"), timeout=_MODEL_FETCH_TIMEOUT_SECONDS
        )
    except Exception:
        _logger.debug("Could not list models for provider %s", provider, exc_info=True)
        _write("Could not fetch the model list; enter the model name manually.\n")
        return []
    finally:
        await provider_client.client.connector.cleanup()
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    return sorted({entry["id"] for entry in entries if isinstance(entry, dict) and entry.get("id")})


def _ask_single_select(label: str, values: tuple[str, ...], default: str) -> str:
    return choice(
        label,
        options=[(value, value) for value in values],
        default=default if default in values else values[0],
    )


def _current_llm_target(settings: Settings) -> str:
    provider = settings.llm.provider
    base_url = settings.providers.get(provider, ProviderConfig()).base_url or ""
    if base_url == "https://api.x.ai/v1":
        return "xai"
    if base_url.startswith("https://api.z.ai/"):
        return "zai"
    if base_url == "https://opencode.ai/zen/v1":
        return "opencode_zen"
    if base_url == "https://opencode.ai/zen/go/v1":
        return "opencode_go"
    return provider if provider in _LLM_TARGETS else "openai"


def _ask_multiselect(
    title: str, values: tuple[tuple[str, str], ...] | list[tuple[str, str]], defaults: Any
) -> set[str]:
    checklist = CheckboxList(values=values, default_values=list(defaults), select_character="x")
    bindings = KeyBindings()

    @bindings.add("enter", eager=True)
    def submit(event: Any) -> None:
        event.app.exit(result=set(checklist.current_values))

    @bindings.add("escape", eager=True)
    def cancel(event: Any) -> None:
        event.app.exit(result=None)

    _write(f"\n{title}\n")
    app = Application(
        layout=Layout(HSplit([checklist, Label("↑/↓ move · Space toggle · Enter continue · Esc cancel")])),
        key_bindings=bindings,
        full_screen=False,
    )
    selected = app.run()
    if selected is None:
        raise KeyboardInterrupt
    return selected


def _ask_secret(label: str, value: str) -> str:
    result = _ask_string_change(label, value, secret=True)
    return "" if result is _CLEAR else value if result is _KEEP else result


def _ask_required_secret(label: str, value: str) -> str:
    while True:
        result = _ask_string_change(label, value, secret=True)
        if result is _KEEP and value:
            return value
        if result not in {_KEEP, _CLEAR}:
            return result
        _write("A value is required.\n")


def _ask_required(label: str, value: str) -> str:
    while True:
        result = _ask_string_change(label, value, secret=False)
        if result is _KEEP and value:
            return value
        if result not in {_KEEP, _CLEAR}:
            return result
        _write("A value is required.\n")


def _ask_bool(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        _write("Answer yes or no.\n")


def _ask_string_change(label: str, value: Any, *, secret: bool) -> Any:
    state = "configured" if value else "empty"
    suffix = f" [{state}; Enter keeps it, - clears it]" if secret else f" [{value}]" if value else ""
    raw = (getpass if secret else input)(f"{label}{suffix}: ").strip()
    if raw == "-":
        return _CLEAR
    return raw if raw else _KEEP


def _ask_list_value(label: str, value: list[int], item_type: type[int]) -> list[int]:
    while True:
        raw = input(f"{label} [{', '.join(str(item) for item in value)}]: ").strip()
        if not raw:
            return value
        if raw == "-":
            return []
        try:
            return [item_type(item.strip()) for item in raw.split(",") if item.strip()]
        except ValueError:
            _write("Enter comma-separated integers.\n")


def _load_document(path: Path) -> tuple[Any, str | None]:
    if path.exists():
        if not path.is_file():
            raise ValueError(f"config path must be a file: {path}")
        return tomlkit.parse(path.read_text(encoding="utf-8")), None
    profile = _ask_single_select("Profile", ("example", "yolo"), "example")
    if profile == "yolo":
        _write("YOLO enables broad host execution and infrastructure integrations.\n")
        if not _ask_bool("Use the YOLO profile", False):
            raise KeyboardInterrupt
    template = _template_root() / f"config.{profile}.toml"
    return tomlkit.parse(template.read_text(encoding="utf-8")), profile


def _template_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _provision_prompts(config_path: Path) -> None:
    # system_prompt_file / prompts_dir default to "./prompts", resolved relative to the working
    # directory `minibot` runs from — mirror that here by seeding it next to the written config.
    prompts_dir = config_path.parent / "prompts"
    if prompts_dir.exists():
        return
    template_prompts = _template_root() / "prompts"
    if template_prompts.is_dir():
        shutil.copytree(template_prompts, prompts_dir)
        _write(f"Wrote {prompts_dir}\n")


def _settings_for_document(document: Any) -> Settings:
    return Settings.from_dict(tomllib.loads(tomlkit.dumps(document)))


def _tool_enabled(settings: Settings, path: tuple[str, ...]) -> bool:
    node: Any = settings
    for key in path:
        node = getattr(node, key)
    return bool(getattr(node, "enabled", False))


def _preserve_references(original: Any, effective: Any, value: object) -> Any:
    if effective == value:
        return original
    if isinstance(original, list) and isinstance(effective, list) and isinstance(value, list):
        remaining = list(zip(original, effective, strict=True))
        preserved = []
        for item in value:
            for index, (raw_item, effective_item) in enumerate(remaining):
                if effective_item == item:
                    preserved.append(raw_item)
                    remaining.pop(index)
                    break
            else:
                preserved.append(item)
        return preserved
    if isinstance(original, dict) and isinstance(effective, dict) and isinstance(value, dict):
        return {
            key: _preserve_references(original[key], effective[key], item)
            if key in original and key in effective
            else item
            for key, item in value.items()
        }
    return value


def _set_value(document: Any, path: tuple[str, ...], value: object) -> None:
    target = document
    for key in path[:-1]:
        if key not in target:
            target[key] = tomlkit.table()
        target = target[key]
    if path[-1] in target:
        effective = _settings_for_document(document).model_dump()
        for key in path:
            effective = effective[key]
        preserved = _preserve_references(target[path[-1]], effective, value)
        if preserved is target[path[-1]]:
            return
        value = preserved
    target[path[-1]] = value


def _unset_value(document: Any, path: tuple[str, ...]) -> None:
    target = document
    for key in path[:-1]:
        if key not in target:
            return
        target = target[key]
    target.pop(path[-1], None)


def _provider_summary(settings: Settings) -> str:
    lines: list[str] = []
    for option in available_providers(settings):
        target = f" → {option.base_url}" if option.base_url else ""
        models = f" · {len(option.models)} models" if option.models else ""
        main = " (main agent)" if option.name == settings.llm.provider.strip().lower() else ""
        lines.append(f"    {option.name} [{option.api_format}]{target}{models}{main}\n")
    return "".join(lines) or "    none with credentials\n"


def _write_summary(path: Path, profile: str | None, settings: Settings) -> None:
    tools = [name for name, tool_path in _TOOLS.items() if _tool_enabled(settings, tool_path)]
    if _GRAPH_MODULE in settings.extensions.modules:
        tools.append("graph")
    provider = settings.providers.get(settings.llm.provider)
    _write(
        "\nSummary\n"
        f"  File: {path}\n"
        f"  Profile: {profile or 'existing'}\n"
        f"  Providers:\n{_provider_summary(settings)}"
        f"  Main agent: {settings.llm.provider}\n"
        f"  Model: {settings.llm.model}\n"
        f"  API key: {'configured' if provider and provider.api_key else 'empty'}\n"
        f"  Telegram: {'enabled' if settings.channels.telegram.enabled else 'disabled'}\n"
        f"  Tools: {', '.join(tools) or 'none'}\n"
        f"  Vault: {settings.vault.path if settings.vault.enabled else 'disabled'}\n"
        "  Literal secrets are stored in plain text; ${VAR} references are preserved.\n\n"
    )


def _write_config(path: Path, text: str) -> None:
    if not path.parent.exists():
        raise OSError(f"config directory does not exist: {path.parent}")
    if path.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_name(f"{path.name}.{timestamp}.bckp"))
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minibot configure")
    parser.add_argument("--config", type=Path, default=None, help="Path to the TOML config file.")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        configure(resolve_config_path(args.config).expanduser())
    except KeyboardInterrupt:
        _write("\nCancelled.\n")
    except (OSError, ValueError, ValidationError) as exc:
        parser.error(str(exc))
