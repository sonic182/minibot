from __future__ import annotations

from typing import Any

from minibot.adapters.config.schema import LLMMConfig
from minibot.llm.services.provider_target import resolve_target_provider


def build_provider_native_tools(config: LLMMConfig) -> tuple[dict[str, Any], ...]:
    if config.provider.strip().lower() != "openai_responses":
        return ()
    if resolve_target_provider(provider_name=config.provider, base_url=config.base_url) != "xai":
        return ()

    xai_cfg = config.xai
    tools: list[dict[str, Any]] = []

    if xai_cfg.web_search_enabled:
        web_cfg = xai_cfg.web_search
        web_tool: dict[str, Any] = {"type": "web_search"}
        if web_cfg.allowed_domains:
            web_tool["allowed_domains"] = list(web_cfg.allowed_domains)
        if web_cfg.excluded_domains:
            web_tool["excluded_domains"] = list(web_cfg.excluded_domains)
        if web_cfg.enable_image_understanding:
            web_tool["enable_image_understanding"] = True
        tools.append(web_tool)

    if xai_cfg.x_search_enabled:
        x_cfg = xai_cfg.x_search
        x_tool: dict[str, Any] = {"type": "x_search"}
        if x_cfg.allowed_x_handles:
            x_tool["allowed_x_handles"] = list(x_cfg.allowed_x_handles)
        if x_cfg.excluded_x_handles:
            x_tool["excluded_x_handles"] = list(x_cfg.excluded_x_handles)
        if x_cfg.from_date:
            x_tool["from_date"] = x_cfg.from_date
        if x_cfg.to_date:
            x_tool["to_date"] = x_cfg.to_date
        if x_cfg.enable_image_understanding:
            x_tool["enable_image_understanding"] = True
        if x_cfg.enable_video_understanding:
            x_tool["enable_video_understanding"] = True
        tools.append(x_tool)

    return tuple(tools)


def build_provider_capability_hints(config: LLMMConfig) -> tuple[str, ...]:
    tools = build_provider_native_tools(config)
    hints: list[str] = []
    if any(tool.get("type") == "web_search" for tool in tools):
        hints.append("Provider-native web search is available for web/current-info tasks.")
    if any(tool.get("type") == "x_search" for tool in tools):
        hints.append("Provider-native X search is available for X/Twitter posts and discussion.")
    return tuple(hints)
