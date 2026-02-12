from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ByteSize, ConfigDict, Field, PositiveInt, TypeAdapter


_BYTE_SIZE_ADAPTER = TypeAdapter(ByteSize)


def _coerce_byte_size(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("byte size must be a positive integer or size string")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError("byte size numeric values must be whole numbers")
    try:
        return int(_BYTE_SIZE_ADAPTER.validate_python(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid byte size value") from exc


ByteSizeValue = Annotated[int, BeforeValidator(_coerce_byte_size), Field(gt=0)]


class RuntimeConfig(BaseModel):
    log_level: str = "INFO"
    environment: str = "development"
    agent_timeout_seconds: int = Field(default=120, ge=120)


class TelegramChannelConfig(BaseModel):
    enabled: bool = True
    bot_token: str = ""
    allowed_chat_ids: List[int] = Field(default_factory=list)
    allowed_user_ids: List[int] = Field(default_factory=list)
    mode: str = Field(default="long_polling")
    webhook_url: Optional[str] = None
    require_authorized: bool = True
    media_enabled: bool = True
    max_photo_bytes: ByteSizeValue = 5242880
    max_document_bytes: ByteSizeValue = 10485760
    max_total_media_bytes: ByteSizeValue = 12582912
    max_attachments_per_message: PositiveInt = 3
    allowed_document_mime_types: List[str] = Field(default_factory=list)
    format_repair_enabled: bool = True
    format_repair_max_attempts: PositiveInt = 1


class OpenRouterProviderRoutingConfig(BaseModel):
    order: List[str] | None = None
    allow_fallbacks: bool | None = None
    require_parameters: bool | None = None
    data_collection: Literal["allow", "deny"] | None = None
    zdr: bool | None = None
    enforce_distillable_text: bool | None = None
    only: List[str] | None = None
    ignore: List[str] | None = None
    quantizations: List[str] | None = None
    sort: str | Dict[str, Any] | None = None
    preferred_min_throughput: float | Dict[str, float] | None = None
    preferred_max_latency: float | Dict[str, float] | None = None
    max_price: Dict[str, Any] | None = None
    provider_extra: Dict[str, Any] = Field(default_factory=dict)


class OpenRouterLLMConfig(BaseModel):
    models: List[str] = Field(default_factory=list)
    provider: OpenRouterProviderRoutingConfig | None = None
    plugins: List[Dict[str, Any]] = Field(default_factory=list)


class LLMMConfig(BaseModel):
    provider: str = "openai"
    api_key: str = ""
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    temperature: float | None = None
    max_new_tokens: PositiveInt | None = None
    max_tool_iterations: PositiveInt = 15
    request_timeout_seconds: int = Field(default=45, ge=45)
    sock_connect_timeout_seconds: PositiveInt = 10
    sock_read_timeout_seconds: PositiveInt = 45
    retry_attempts: PositiveInt = 3
    retry_delay_seconds: float = Field(default=2.0, gt=0)
    system_prompt: str = "You are Minibot, a helpful assistant."
    prompts_dir: str = "./prompts"
    reasoning_effort: str | None = None
    openrouter: OpenRouterLLMConfig = OpenRouterLLMConfig()


class MemoryConfig(BaseModel):
    backend: str = "sqlite"
    sqlite_url: str = "sqlite+aiosqlite:///./data/minibot.db"
    max_history_messages: int | None = Field(default=None, ge=1)


class KeyValueMemoryConfig(BaseModel):
    enabled: bool = False
    sqlite_url: str = "sqlite+aiosqlite:///./data/kv_memory.db"
    pool_size: PositiveInt = 5
    echo: bool = False
    default_limit: PositiveInt = 20
    max_limit: PositiveInt = 100
    default_owner_id: str | None = "primary"


class HTTPClientToolConfig(BaseModel):
    enabled: bool = False
    timeout_seconds: PositiveInt = 10
    max_bytes: ByteSizeValue = 16384
    response_processing_mode: Literal["none", "auto"] = "auto"
    max_chars: PositiveInt | None = None
    normalize_whitespace: bool = True


class TimeToolConfig(BaseModel):
    enabled: bool = True
    default_format: str = "%Y-%m-%dT%H:%M:%SZ"


class CalculatorToolConfig(BaseModel):
    enabled: bool = True
    default_scale: PositiveInt = 28
    max_expression_length: PositiveInt = 200
    max_exponent_abs: PositiveInt = 1000


class PythonExecRLimitConfig(BaseModel):
    enabled: bool = False
    cpu_seconds: PositiveInt | None = 2
    memory_mb: PositiveInt | None = 256
    fsize_mb: PositiveInt | None = 16
    nproc: PositiveInt | None = 64
    nofile: PositiveInt | None = 256


class PythonExecCgroupConfig(BaseModel):
    enabled: bool = False
    driver: Literal["systemd"] = "systemd"
    cpu_quota_percent: PositiveInt | None = 100
    memory_max_mb: PositiveInt | None = 256


class PythonExecJailConfig(BaseModel):
    enabled: bool = False
    command_prefix: List[str] = Field(default_factory=list)


class PythonExecToolConfig(BaseModel):
    enabled: bool = True
    backend: Literal["host"] = "host"
    python_path: str | None = None
    venv_path: str | None = None
    sandbox_mode: Literal["none", "basic", "rlimit", "cgroup", "jail"] = "basic"
    default_timeout_seconds: PositiveInt = 8
    max_timeout_seconds: PositiveInt = 20
    max_output_bytes: ByteSizeValue = 64000
    max_code_bytes: ByteSizeValue = 32000
    artifacts_enabled: bool = True
    artifacts_default_subdir: str = "generated"
    artifacts_allowed_extensions: List[str] = Field(
        default_factory=lambda: [".png", ".jpg", ".jpeg", ".pdf", ".csv", ".txt", ".json", ".svg"]
    )
    artifacts_max_files: PositiveInt = 5
    artifacts_max_file_bytes: ByteSizeValue = 5000000
    artifacts_max_total_bytes: ByteSizeValue = 20000000
    artifacts_allow_in_jail: bool = False
    artifacts_jail_shared_dir: str | None = None
    pass_parent_env: bool = False
    env_allowlist: List[str] = Field(default_factory=lambda: ["PATH", "LANG", "LC_ALL", "PYTHONUTF8"])
    rlimit: PythonExecRLimitConfig = PythonExecRLimitConfig()
    cgroup: PythonExecCgroupConfig = PythonExecCgroupConfig()
    jail: PythonExecJailConfig = PythonExecJailConfig()


class PlaywrightToolConfig(BaseModel):
    enabled: bool = False
    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    launch_channel: str | None = "chrome"
    chromium_executable_path: str | None = None
    headless: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    viewport_width: PositiveInt = 1920
    viewport_height: PositiveInt = 1080
    locale: str = "en-US"
    timezone_id: str = "America/New_York"
    permissions: List[str] = Field(default_factory=lambda: ["geolocation"])
    geolocation_latitude: float = 40.7128
    geolocation_longitude: float = -74.0060
    screen_width: PositiveInt = 1920
    screen_height: PositiveInt = 1080
    extra_http_headers: Dict[str, str] = Field(
        default_factory=lambda: {
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        }
    )
    navigation_timeout_seconds: PositiveInt = 20
    action_timeout_seconds: PositiveInt = 10
    max_text_chars: PositiveInt = 6000
    max_screenshot_bytes: ByteSizeValue = 2000000
    postprocess_outputs: bool = True
    postprocess_expose_raw: bool = False
    postprocess_snapshot_ttl_seconds: PositiveInt = 30
    session_ttl_seconds: PositiveInt = 600
    allowed_domains: List[str] = Field(default_factory=list)
    allow_http: bool = False
    block_private_networks: bool = True
    launch_args: List[str] = Field(
        default_factory=lambda: [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-extensions",
            "--lang=en-US,en",
            "--disable-notifications",
        ]
    )


class FileStorageToolConfig(BaseModel):
    enabled: bool = False
    root_dir: str = "./data/files"
    max_write_bytes: ByteSizeValue = 64000
    save_incoming_uploads: bool = False
    uploads_subdir: str = "uploads"
    incoming_temp_subdir: str = "uploads/temp"


class ToolsConfig(BaseModel):
    kv_memory: KeyValueMemoryConfig = KeyValueMemoryConfig()
    http_client: HTTPClientToolConfig = HTTPClientToolConfig()
    time: TimeToolConfig = TimeToolConfig()
    calculator: CalculatorToolConfig = CalculatorToolConfig()
    python_exec: PythonExecToolConfig = PythonExecToolConfig()
    playwright: PlaywrightToolConfig = PlaywrightToolConfig()
    file_storage: FileStorageToolConfig = FileStorageToolConfig()


class ScheduledPromptsConfig(BaseModel):
    enabled: bool = True
    sqlite_url: str = "sqlite+aiosqlite:///./data/scheduled_prompts.db"
    poll_interval_seconds: PositiveInt = 60
    lease_timeout_seconds: PositiveInt = 120
    batch_size: PositiveInt = 10
    max_attempts: PositiveInt = 3
    min_recurrence_interval_seconds: PositiveInt = 60
    pool_size: PositiveInt = 5
    echo: bool = False


class SchedulerConfig(BaseModel):
    prompts: ScheduledPromptsConfig = ScheduledPromptsConfig()


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
    tools: ToolsConfig = ToolsConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
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
