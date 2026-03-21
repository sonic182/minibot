from __future__ import annotations

from typing import Any

from minibot.adapters.config.schema import LLMMConfig
from minibot.llm.services.provider_target import resolve_target_provider


def build_provider_native_tools(config: LLMMConfig) -> tuple[dict[str, Any], ...]:
    if config.provider.strip().lower() != "openai_responses":
        return ()
    if resolve_target_provider(provider_name=config.provider, base_url=config.base_url) != "xai":
        return ()
    xai_cfg = getattr(config, "xai", None)
    if not bool(getattr(xai_cfg, "web_search_enabled", False)):
        return ()
    return ({"type": "web_search"},)


def build_provider_capability_hints(config: LLMMConfig) -> tuple[str, ...]:
    tools = build_provider_native_tools(config)
    if any(tool.get("type") == "web_search" for tool in tools):
        return ("Provider-native web search is available for web/current-info tasks.",)
    return ()
