from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path
from typing import Any

import tomlkit
from prompt_toolkit import Application, choice
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.widgets import CheckboxList, Label
from pydantic import ValidationError

from minibot.adapters.config.loader import resolve_config_path
from minibot.adapters.config.schema import LLMMConfig, ProviderConfig, Settings, TelegramChannelConfig
from minibot.llm.services.client_bootstrap import create_provider

_logger = logging.getLogger(__name__)
_MANUAL_MODEL = "Type a model name manually…"
_MODEL_FETCH_TIMEOUT_SECONDS = 10

_LLM_TARGETS = {
    "openai": ("OpenAI API", "openai", ""),
    "openai_responses": ("OpenAI Responses API", "openai_responses", ""),
    "xai": ("xAI", "openai_responses", "https://api.x.ai/v1"),
    "zai": ("z.ai GLM Coding Plan", "openai", "https://api.z.ai/api/coding/paas/v4"),
    "opencode_zen": ("OpenCode Zen", "openai", "https://opencode.ai/zen/v1"),
    "opencode_go": ("OpenCode Go", "openai", "https://opencode.ai/zen/go/v1"),
}
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_ENVIRONMENTS = ("development", "production")
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
    "tasks": ("tools", "tasks"),
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
_KEEP = object()
_CLEAR = object()


def configure(path: Path) -> bool:
    document, profile = _load_document(path)
    settings = _settings_for_document(document)
    _write("\nMinibot configuration\n\n")
    _configure_runtime(document, settings)
    settings = _settings_for_document(document)
    _configure_telegram(document, settings.channels["telegram"])
    settings = _settings_for_document(document)
    _configure_llm(document, settings)
    settings = _settings_for_document(document)
    _configure_tools(document, settings)
    text = tomlkit.dumps(document)
    settings = Settings.from_dict(tomllib.loads(text))
    _write_summary(path, profile, settings)
    if not _ask_bool("Write this configuration", False):
        _write("No changes written.\n")
        return False
    _write_config(path, text)
    _write(f"Wrote {path}\n")
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
    current_target = _current_llm_target(settings)
    target = _ask_llm_target(current_target)
    _, provider, base_url = _LLM_TARGETS[target]
    if target in {"opencode_zen", "opencode_go"} and _ask_bool(
        "Use Responses API", target == current_target and settings.llm.provider == "openai_responses"
    ):
        provider = "openai_responses"
    provider_config = settings.providers.get(provider, ProviderConfig())
    api_key = _ask_secret("API key", provider_config.api_key)
    _set_value(document, ("llm", "provider"), provider)
    _set_value(document, ("providers", provider, "api_key"), api_key)
    _set_value(document, ("providers", provider, "base_url"), base_url)
    _set_value(document, ("llm", "model"), _ask_model(provider, base_url, api_key, settings.llm.model))


def _configure_tools(document: Any, settings: Settings) -> None:
    defaults = [name for name, tool_path in _TOOLS.items() if _tool_enabled(settings, tool_path)]
    _write("python, bash and patch can execute or modify files; grep enables files automatically.\n")
    selected = _ask_multiselect(
        "Enabled tools",
        [(name, f"{name} — {_TOOL_DESCRIPTIONS[name]}") for name in _TOOLS],
        defaults,
    )
    if "grep" in selected:
        selected.add("files")
    for name, tool_path in _TOOLS.items():
        _set_value(document, (*tool_path, "enabled"), name in selected)
    _set_value(document, ("rabbitmq", "enabled"), "tasks" in selected)
    # The wizard keeps rerank tied to rag for simplicity; edit config.toml directly to decouple them.
    _set_value(document, ("tools", "rag", "rerank", "enabled"), "rag" in selected)


def _ask_llm_target(default: str) -> str:
    return choice(
        "LLM provider",
        options=[(key, label) for key, (label, _, _) in _LLM_TARGETS.items()],
        default=default,
    )


def _ask_model(provider: str, base_url: str, api_key: str, current: str) -> str:
    models = asyncio.run(_fetch_models(provider, base_url, api_key))
    if not models:
        return _ask_required("Model", current)
    options = [*models, _MANUAL_MODEL]
    default = current if current in models else models[0]
    selected = choice("Model", options=[(value, value) for value in options], default=default)
    return _ask_required("Model", current) if selected == _MANUAL_MODEL else selected


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
    template = Path(__file__).resolve().parents[3] / f"config.{profile}.toml"
    return tomlkit.parse(template.read_text(encoding="utf-8")), profile


def _settings_for_document(document: Any) -> Settings:
    return Settings.from_dict(tomllib.loads(tomlkit.dumps(document)))


def _tool_enabled(settings: Settings, path: tuple[str, ...]) -> bool:
    if path[0] == "scheduler":
        return settings.scheduler.prompts.enabled
    return bool(getattr(getattr(settings.tools, path[-1]), "enabled", False))


def _set_value(document: Any, path: tuple[str, ...], value: object) -> None:
    target = document
    for key in path[:-1]:
        if key not in target:
            target[key] = tomlkit.table()
        target = target[key]
    target[path[-1]] = value


def _write_summary(path: Path, profile: str | None, settings: Settings) -> None:
    tools = [name for name, tool_path in _TOOLS.items() if _tool_enabled(settings, tool_path)]
    provider = settings.providers.get(settings.llm.provider)
    _write(
        "\nSummary\n"
        f"  File: {path}\n"
        f"  Profile: {profile or 'existing'}\n"
        f"  Provider: {settings.llm.provider}\n"
        f"  Model: {settings.llm.model}\n"
        f"  API key: {'configured' if provider and provider.api_key else 'empty'}\n"
        f"  Telegram: {'enabled' if settings.channels['telegram'].enabled else 'disabled'}\n"
        f"  Tools: {', '.join(tools) or 'none'}\n"
        "  Secrets are stored in plain text.\n\n"
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
