from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class RuntimeConfig(BaseModel):
    log_level: str = "INFO"
    environment: str = "development"


class TelegramChannelConfig(BaseModel):
    enabled: bool = True
    bot_token: str = ""
    allowed_chat_ids: List[int] = Field(default_factory=list)
    allowed_user_ids: List[int] = Field(default_factory=list)
    mode: str = Field(default="long_polling")
    webhook_url: Optional[str] = None
    require_authorized: bool = False


class LLMMConfig(BaseModel):
    provider: str = "openai"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.4
    max_new_tokens: PositiveInt = 512
    system_prompt: str = "You are Minibot, a helpful assistant."


class MemoryConfig(BaseModel):
    backend: str = "sqlite"
    sqlite_url: str = "sqlite+aiosqlite:///./data/minibot.db"


class KeyValueMemoryConfig(BaseModel):
    enabled: bool = False
    sqlite_url: str = "sqlite+aiosqlite:///./data/kv_memory.db"
    pool_size: PositiveInt = 5
    echo: bool = False
    default_limit: PositiveInt = 20
    max_limit: PositiveInt = 100
    default_owner_id: str | None = "primary"


class LoggingConfig(BaseModel):
    structured: bool = True
    logfmt_enabled: bool = True
    log_level: str = "INFO"
    kv_separator: str = "="
    record_separator: str = " "


class Settings(BaseModel):
    runtime: RuntimeConfig = RuntimeConfig()
    channels: Dict[str, TelegramChannelConfig] = Field(
        default_factory=lambda: {"telegram": TelegramChannelConfig(bot_token="")}
    )
    llm: LLMMConfig
    memory: MemoryConfig = MemoryConfig()
    kv_memory: KeyValueMemoryConfig = KeyValueMemoryConfig()
    logging: LoggingConfig = LoggingConfig()

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        return cls.model_validate(data)

    @classmethod
    def from_file(cls, path: Path | None = None) -> "Settings":
        if path is None:
            raise ValueError("config file path is required")
        with path.open("rb") as fp:
            data = tomllib.load(fp)
        return cls.from_dict(data)
