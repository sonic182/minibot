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
    if xai_cfg is None:
        return ()

    tools: list[dict[str, Any]] = []

    if bool(getattr(xai_cfg, "web_search_enabled", False)):
        web_search_cfg = getattr(xai_cfg, "web_search", None)
        web_search_tool: dict[str, Any] = {"type": "web_search"}
        if web_search_cfg is not None:
            if getattr(web_search_cfg, "allowed_domains", None):
                web_search_tool["allowed_domains"] = list(web_search_cfg.allowed_domains)
            if getattr(web_search_cfg, "excluded_domains", None):
                web_search_tool["excluded_domains"] = list(web_search_cfg.excluded_domains)
            if bool(getattr(web_search_cfg, "enable_image_understanding", False)):
                web_search_tool["enable_image_understanding"] = True
        tools.append(web_search_tool)

    if bool(getattr(xai_cfg, "x_search_enabled", False)):
        x_search_cfg = getattr(xai_cfg, "x_search", None)
        x_search_tool: dict[str, Any] = {"type": "x_search"}
        if x_search_cfg is not None:
            if getattr(x_search_cfg, "allowed_x_handles", None):
                x_search_tool["allowed_x_handles"] = list(x_search_cfg.allowed_x_handles)
            if getattr(x_search_cfg, "excluded_x_handles", None):
                x_search_tool["excluded_x_handles"] = list(x_search_cfg.excluded_x_handles)
            if getattr(x_search_cfg, "from_date", None):
                x_search_tool["from_date"] = x_search_cfg.from_date
            if getattr(x_search_cfg, "to_date", None):
                x_search_tool["to_date"] = x_search_cfg.to_date
            if bool(getattr(x_search_cfg, "enable_image_understanding", False)):
                x_search_tool["enable_image_understanding"] = True
            if bool(getattr(x_search_cfg, "enable_video_understanding", False)):
                x_search_tool["enable_video_understanding"] = True
        tools.append(x_search_tool)

    return tuple(tools)


def build_provider_capability_hints(config: LLMMConfig) -> tuple[str, ...]:
    tools = build_provider_native_tools(config)
    hints: list[str] = []
    if any(tool.get("type") == "web_search" for tool in tools):
        hints.append("Provider-native web search is available for web/current-info tasks.")
    if any(tool.get("type") == "x_search" for tool in tools):
        hints.append("Provider-native X search is available for X/Twitter posts and discussion.")
    return tuple(hints)
