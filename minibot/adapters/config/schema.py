from __future__ import annotations

import tomllib
import types
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    BeforeValidator,
    ByteSize,
    ConfigDict,
    Field,
    PositiveInt,
    TypeAdapter,
    ValidationError,
    model_validator,
)

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


def _load_file_data(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        with path.open("rb") as fp:
            data = tomllib.load(fp)
        return data
    raise ValueError(f"unsupported config file type: {path.suffix or '<none>'}")


def _normalize_for_annotation(value: Any, annotation: Any) -> Any:
    if annotation is Any:
        return value

    origin = get_origin(annotation)
    if origin is Annotated:
        return _normalize_for_annotation(value, get_args(annotation)[0])

    if origin in (Union, types.UnionType):
        non_none_args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if value is None or not non_none_args:
            return value
        return _normalize_for_annotation(value, non_none_args[0])

    if origin in (list, list):
        item_annotation = get_args(annotation)[0] if get_args(annotation) else Any
        if value == {}:
            return []
        if isinstance(value, list):
            return [_normalize_for_annotation(item, item_annotation) for item in value]
        return value

    if origin in (dict, dict):
        args = get_args(annotation)
        value_annotation = args[1] if len(args) == 2 else Any
        if isinstance(value, dict):
            return {key: _normalize_for_annotation(item, value_annotation) for key, item in value.items()}
        return value

    if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            field = annotation.model_fields.get(key)
            normalized[key] = _normalize_for_annotation(item, field.annotation) if field is not None else item
        return normalized

    return value


ByteSizeValue = Annotated[int, BeforeValidator(_coerce_byte_size), Field(gt=0)]


class RuntimeConfig(BaseModel):
    """Top-level runtime settings. TOML section: ``[runtime]``

    - ``log_level`` — root log level (default: ``"INFO"``).
    - ``environment`` — label used in log context (default: ``"development"``).
    - ``agent_timeout_seconds`` — hard wall-clock timeout for any agent turn (min/default: ``120``).
    """

    log_level: str = "INFO"
    environment: str = "development"
    agent_timeout_seconds: int = Field(default=120, ge=120)


class TelegramChannelConfig(BaseModel):
    """Telegram channel settings. TOML section: ``[channels.telegram]``

    - ``bot_token`` — BotFather token (required).
    - ``allowed_chat_ids`` / ``allowed_user_ids`` — access control lists.
    - ``mode`` — ``"long_polling"`` (default) or ``"webhook"``.
    - ``webhook_url`` — required when ``mode = "webhook"``.
    - ``require_authorized`` — reject messages from unlisted IDs (default: ``true``).
    - ``media_enabled`` — accept photo/document attachments (default: ``true``).
    - ``max_photo_bytes`` / ``max_document_bytes`` / ``max_total_media_bytes`` — per-message size caps.
    - ``max_attachments_per_message`` — attachment count cap (default: ``3``).
    - ``allowed_document_mime_types`` — MIME whitelist; empty means all types are allowed.
    - ``format_repair_enabled`` — auto-repair malformed Markdown before sending (default: ``true``).
    """

    enabled: bool = True
    bot_token: str = ""
    allowed_chat_ids: list[int] = Field(default_factory=list)
    allowed_user_ids: list[int] = Field(default_factory=list)
    mode: str = Field(default="long_polling")
    webhook_url: str | None = None
    require_authorized: bool = True
    media_enabled: bool = True
    max_photo_bytes: ByteSizeValue = 5242880
    max_document_bytes: ByteSizeValue = 10485760
    max_total_media_bytes: ByteSizeValue = 12582912
    max_attachments_per_message: PositiveInt = 3
    allowed_document_mime_types: list[str] = Field(default_factory=list)
    format_repair_enabled: bool = True
    format_repair_max_attempts: PositiveInt = 1


class OpenRouterProviderRoutingConfig(BaseModel):
    order: list[str] | None = None
    allow_fallbacks: bool | None = None
    require_parameters: bool | None = None
    data_collection: Literal["allow", "deny"] | None = None
    zdr: bool | None = None
    enforce_distillable_text: bool | None = None
    only: list[str] | None = None
    ignore: list[str] | None = None
    quantizations: list[str] | None = None
    sort: str | dict[str, Any] | None = None
    preferred_min_throughput: float | dict[str, float] | None = None
    preferred_max_latency: float | dict[str, float] | None = None
    max_price: dict[str, Any] | None = None
    provider_extra: dict[str, Any] = Field(default_factory=dict)


class OpenRouterLLMConfig(BaseModel):
    models: list[str] = Field(default_factory=list)
    provider: OpenRouterProviderRoutingConfig | None = None
    reasoning_enabled: bool | None = None
    plugins: list[dict[str, Any]] = Field(default_factory=list)


def _parse_iso8601_datetime(value: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise ValueError("datetime value must not be empty")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("datetime value must be a valid ISO8601 string") from exc


class XAIWebSearchConfig(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    excluded_domains: list[str] = Field(default_factory=list)
    enable_image_understanding: bool = False

    @model_validator(mode="after")
    def _validate_limits(self) -> XAIWebSearchConfig:
        if len(self.allowed_domains) > 5:
            raise ValueError("allowed_domains supports at most 5 entries")
        if len(self.excluded_domains) > 5:
            raise ValueError("excluded_domains supports at most 5 entries")
        return self


class XAIXSearchConfig(BaseModel):
    allowed_x_handles: list[str] = Field(default_factory=list)
    excluded_x_handles: list[str] = Field(default_factory=list)
    from_date: str | None = None
    to_date: str | None = None
    enable_image_understanding: bool = False
    enable_video_understanding: bool = False

    @model_validator(mode="after")
    def _validate_limits(self) -> XAIXSearchConfig:
        if len(self.allowed_x_handles) > 10:
            raise ValueError("allowed_x_handles supports at most 10 entries")
        if len(self.excluded_x_handles) > 10:
            raise ValueError("excluded_x_handles supports at most 10 entries")
        from_dt = _parse_iso8601_datetime(self.from_date) if self.from_date else None
        to_dt = _parse_iso8601_datetime(self.to_date) if self.to_date else None
        if from_dt and to_dt:
            if (from_dt.tzinfo is None) != (to_dt.tzinfo is None):
                raise ValueError("from_date and to_date must both be timezone-aware or both timezone-naive")
            if from_dt > to_dt:
                raise ValueError("from_date must be less than or equal to to_date")
        return self


class XAILLMConfig(BaseModel):
    web_search_enabled: bool = False
    x_search_enabled: bool = False
    web_search: XAIWebSearchConfig = XAIWebSearchConfig()
    x_search: XAIXSearchConfig = XAIXSearchConfig()


class LLMMConfig(BaseModel):
    """Main LLM settings. TOML section: ``[llm]``

    - ``provider`` — provider name: ``"openai"``, ``"anthropic"``, ``"openrouter"``, ``"xai"``, etc.
    - ``api_key`` — provider API key.
    - ``base_url`` — optional base URL (for proxies or OpenAI-compatible local servers).
    - ``model`` — model identifier (default: ``"gpt-4o-mini"``).
    - ``temperature`` — sampling temperature (``null`` uses provider default).
    - ``max_new_tokens`` — max tokens to generate per turn.
    - ``max_tool_iterations`` — maximum tool-call rounds before forcing a final answer (default: ``15``).
    - ``request_timeout_seconds`` — HTTP timeout per LLM request (min: ``45``).
    - ``system_prompt`` — inline system prompt (overridden by ``system_prompt_file``).
    - ``system_prompt_file`` — path to the main system prompt markdown file.
    - ``prompts_dir`` — directory for runtime prompt fragments.
    - ``reasoning_effort`` — reasoning budget hint for supported models (e.g. ``"high"``).
    - ``main_responses_state_mode`` — how conversation state is passed for the main agent
      (``"full_messages"`` or ``"previous_response_id"``).
    - ``prompt_cache_enabled`` — enable provider-side prompt caching (default: ``true``).
    - ``openrouter`` — OpenRouter-specific routing overrides (``[llm.openrouter]``).
    - ``xai`` — xAI web/X search integration (``[llm.xai]``).
    """

    provider: str = "openai"
    api_key: str = ""
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    http2: bool = False
    temperature: float | None = None
    max_new_tokens: PositiveInt | None = None
    max_tool_iterations: PositiveInt = 15
    request_timeout_seconds: int = Field(default=45, ge=45)
    sock_connect_timeout_seconds: PositiveInt = 10
    sock_read_timeout_seconds: PositiveInt = 45
    retry_attempts: PositiveInt = 3
    retry_delay_seconds: float = Field(default=2.0, gt=0)
    system_prompt: str = "You are Minibot, a helpful assistant."
    system_prompt_file: str | None = "./prompts/main_agent_system.md"
    prompts_dir: str = "./prompts"
    reasoning_effort: str | None = None
    main_responses_state_mode: Literal["full_messages", "previous_response_id"] = "full_messages"
    agent_responses_state_mode: Literal["full_messages", "previous_response_id"] = "previous_response_id"
    responses_state_mode: Literal["full_messages", "previous_response_id"] = "full_messages"
    prompt_cache_enabled: bool = True
    prompt_cache_retention: Literal["in-memory", "24h"] | None = None
    openrouter: OpenRouterLLMConfig = OpenRouterLLMConfig()
    xai: XAILLMConfig = XAILLMConfig()


class ProviderConfig(BaseModel):
    """Named LLM provider credentials. TOML section: ``[providers.<name>]``

    Used to supply API keys and base URLs for secondary providers referenced
    by agent definitions (``model_provider``).

    - ``api_key`` — provider API key.
    - ``base_url`` — optional base URL override (e.g. for proxies or local endpoints).
    """

    api_key: str = ""
    base_url: str | None = None


class AgentDefinitionConfig(BaseModel):
    name: str
    description: str = ""
    mode: Literal["agent"] = "agent"
    enabled: bool = True
    model_provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_new_tokens: PositiveInt | None = None
    reasoning_effort: str | None = None
    max_tool_iterations: PositiveInt | None = None
    tools_allow: list[str] = Field(default_factory=list)
    tools_deny: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    openrouter_provider_overrides: dict[str, Any] = Field(default_factory=dict)
    openrouter_reasoning_enabled: bool | None = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _validate_tool_policy(self) -> AgentDefinitionConfig:
        if self.tools_allow and self.tools_deny:
            raise ValueError("only one of tools_allow or tools_deny can be set")
        extras = dict(self.model_extra or {})
        invalid_extra_keys: list[str] = []
        overrides: dict[str, Any] = {}
        valid_provider_keys = set(OpenRouterProviderRoutingConfig.model_fields)
        prefix = "openrouter_provider_"
        for key, value in extras.items():
            if not key.startswith(prefix):
                invalid_extra_keys.append(key)
                continue
            provider_key = key[len(prefix) :]
            if provider_key not in valid_provider_keys:
                invalid_extra_keys.append(key)
                continue
            overrides[provider_key] = value
        if invalid_extra_keys:
            invalid_keys = ", ".join(sorted(invalid_extra_keys))
            raise ValueError(f"unknown frontmatter keys: {invalid_keys}")
        try:
            provider_cfg = OpenRouterProviderRoutingConfig.model_validate(overrides)
        except ValidationError as exc:
            raise ValueError(f"invalid openrouter provider overrides: {exc}") from exc
        self.openrouter_provider_overrides = provider_cfg.model_dump(
            mode="python",
            exclude_none=True,
            exclude_defaults=True,
        )
        return self


class MainAgentConfig(BaseModel):
    name: str = "minibot"
    tools_allow: list[str] = Field(default_factory=list)
    tools_deny: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_tool_policy(self) -> MainAgentConfig:
        if self.tools_allow and self.tools_deny:
            raise ValueError("only one of tools_allow or tools_deny can be set")
        return self


class OrchestrationConfig(BaseModel):
    """Multi-agent orchestration settings. TOML section: ``[orchestration]``

    - ``directory`` — path to agent definition files (default: ``"./agents"``).
    - ``default_timeout_seconds`` — per-agent-call timeout (default: ``90``).
    - ``tool_ownership_mode`` — how tools are shared between agents:
      ``"shared"`` (default), ``"exclusive"``, or ``"exclusive_mcp"``.
    - ``delegated_tool_call_policy`` — whether delegated agents may call tools:
      ``"auto"`` (default), ``"always"``, or ``"never"``.
    - ``main_tool_use_guardrail`` — optional guardrail before tool execution:
      ``"disabled"`` (default) or ``"llm_classifier"``.
    - ``main_agent`` — tool allow/deny policy for the main agent (``[orchestration.main_agent]``).
    """

    directory: str = "./agents"
    default_timeout_seconds: PositiveInt = 90
    tool_ownership_mode: Literal["shared", "exclusive", "exclusive_mcp"] = "shared"
    delegated_tool_call_policy: Literal["auto", "always", "never"] = "auto"
    main_tool_use_guardrail: Literal["disabled", "llm_classifier"] = "disabled"
    main_agent: MainAgentConfig = MainAgentConfig()


class MemoryConfig(BaseModel):
    """Conversation history memory settings. TOML section: ``[memory]``

    - ``backend`` — storage backend (currently only ``"sqlite"``).
    - ``sqlite_url`` — SQLite database URL (default: ``"sqlite+aiosqlite:///./data/minibot.db"``).
    - ``max_history_messages`` — hard cap on stored messages per conversation (``null`` = unlimited).
    - ``max_history_tokens`` — token budget for history sent to the LLM (``null`` = unlimited).
    - ``context_ratio_before_compact`` — fraction of context window used before triggering
      automatic compaction (default: ``0.95``).
    - ``notify_compaction_updates`` — send a user-visible message when history is compacted
      (default: ``false``).
    """

    backend: str = "sqlite"
    sqlite_url: str = "sqlite+aiosqlite:///./data/minibot.db"
    max_history_messages: int | None = Field(default=None, ge=1)
    max_history_tokens: int | None = Field(default=None, ge=1)
    context_ratio_before_compact: float = Field(default=0.95, gt=0, le=1)
    notify_compaction_updates: bool = False


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
    spill_to_managed_file: bool = False
    spill_after_chars: PositiveInt = 16000
    spill_preview_chars: PositiveInt = 2000
    max_spill_bytes: ByteSizeValue = 5000000
    spill_subdir: str = "http_responses/tmp"


class TimeToolConfig(BaseModel):
    enabled: bool = True
    default_format: str = "%Y-%m-%dT%H:%M:%SZ"


class WaitToolConfig(BaseModel):
    enabled: bool = False
    max_milliseconds: PositiveInt = 30000


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
    command_prefix: list[str] = Field(default_factory=list)


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
    artifacts_allowed_extensions: list[str] = Field(
        default_factory=lambda: [".png", ".jpg", ".jpeg", ".pdf", ".csv", ".txt", ".json", ".svg"]
    )
    artifacts_max_files: PositiveInt = 5
    artifacts_max_file_bytes: ByteSizeValue = 5000000
    artifacts_max_total_bytes: ByteSizeValue = 20000000
    artifacts_allow_in_jail: bool = False
    artifacts_jail_shared_dir: str | None = None
    pass_parent_env: bool = False
    env_allowlist: list[str] = Field(default_factory=lambda: ["PATH", "LANG", "LC_ALL", "PYTHONUTF8"])
    rlimit: PythonExecRLimitConfig = PythonExecRLimitConfig()
    cgroup: PythonExecCgroupConfig = PythonExecCgroupConfig()
    jail: PythonExecJailConfig = PythonExecJailConfig()


class BashToolConfig(BaseModel):
    enabled: bool = False
    default_timeout_seconds: PositiveInt = 15
    max_timeout_seconds: PositiveInt = 120
    max_output_bytes: ByteSizeValue = 128000
    pass_parent_env: bool = True
    env_allowlist: list[str] = Field(default_factory=lambda: ["PATH", "HOME", "USER", "LANG", "LC_ALL", "SHELL"])


class ApplyPatchToolConfig(BaseModel):
    enabled: bool = False
    restrict_to_workspace: bool = True
    workspace_root: str = "."
    allow_outside_workspace: bool = False
    preserve_trailing_newline: bool = True
    max_patch_bytes: ByteSizeValue = 262144


class MCPServerConfig(BaseModel):
    name: str
    transport: Literal["stdio", "http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    enabled_tools: list[str] = Field(default_factory=list)
    disabled_tools: list[str] = Field(default_factory=list)


class MCPToolConfig(BaseModel):
    enabled: bool = False
    name_prefix: str = "mcp"
    timeout_seconds: PositiveInt = 10
    servers: list[MCPServerConfig] = Field(default_factory=list)


class FileStorageToolConfig(BaseModel):
    enabled: bool = False
    root_dir: str = "./data/files"
    max_write_bytes: ByteSizeValue = 64000
    allow_outside_root: bool = False
    save_incoming_uploads: bool = False
    uploads_subdir: str = "uploads"
    incoming_temp_subdir: str = "uploads/temp"


class GrepToolConfig(BaseModel):
    enabled: bool = False
    max_matches: PositiveInt = 200
    max_file_size_bytes: ByteSizeValue = 1000000


class BrowserToolConfig(BaseModel):
    output_dir: str = "./data/files/browser"


class AudioTranscriptionToolConfig(BaseModel):
    enabled: bool = False
    model: str = "small"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "int8"
    beam_size: PositiveInt = 5
    vad_filter: bool = True
    auto_transcribe_short_incoming: bool = True
    auto_transcribe_max_duration_seconds: PositiveInt = 45


class SkillsToolConfig(BaseModel):
    enabled: bool = True
    paths: list[str] = Field(default_factory=list)
    preload_catalog: bool = False


class TaskToolConfig(BaseModel):
    enabled: bool = False


class ToolsConfig(BaseModel):
    kv_memory: KeyValueMemoryConfig = KeyValueMemoryConfig()
    http_client: HTTPClientToolConfig = HTTPClientToolConfig()
    time: TimeToolConfig = TimeToolConfig()
    wait: WaitToolConfig = WaitToolConfig()
    calculator: CalculatorToolConfig = CalculatorToolConfig()
    python_exec: PythonExecToolConfig = PythonExecToolConfig()
    bash: BashToolConfig = BashToolConfig()
    apply_patch: ApplyPatchToolConfig = ApplyPatchToolConfig()
    file_storage: FileStorageToolConfig = FileStorageToolConfig()
    grep: GrepToolConfig = GrepToolConfig()
    browser: BrowserToolConfig = BrowserToolConfig()
    audio_transcription: AudioTranscriptionToolConfig = AudioTranscriptionToolConfig()
    mcp: MCPToolConfig = MCPToolConfig()
    skills: SkillsToolConfig = Field(default_factory=SkillsToolConfig)
    tasks: TaskToolConfig = Field(default_factory=TaskToolConfig)


class RabbitMQConsumerConfig(BaseModel):
    """RabbitMQ task consumer settings. TOML section: ``[rabbitmq]``

    Requires the ``rabbitmq`` extra: ``poetry install --extras rabbitmq``.

    - ``enabled`` — enable the RabbitMQ consumer (default: ``false``).
    - ``broker_url`` — AMQP connection URL (default: ``"amqp://guest:guest@localhost:5672/"``).
    - ``queue_name`` — queue to consume from (default: ``"minibot"``).
    - ``exchange_name`` — fanout exchange name (default: ``"minibot.tasks"``).
    - ``prefetch_count`` — max unacknowledged messages per worker (default: ``1``).
    - ``worker_timeout_seconds`` — per-task processing timeout (default: ``60``).
    - ``max_concurrent_workers`` — maximum parallel task handlers (default: ``4``).
    """

    enabled: bool = False
    broker_url: str = "amqp://guest:guest@localhost:5672/"
    queue_name: str = "minibot"
    exchange_name: str = "minibot.tasks"
    prefetch_count: PositiveInt = 1
    worker_timeout_seconds: PositiveInt = 60
    max_concurrent_workers: PositiveInt = 4


class ScheduledPromptsConfig(BaseModel):
    """Scheduler persistence and polling settings. TOML section: ``[scheduler.prompts]``

    - ``enabled`` — enable the scheduler (default: ``true``).
    - ``sqlite_url`` — SQLite database URL for job storage.
    - ``poll_interval_seconds`` — how often due jobs are checked (default: ``60``).
    - ``lease_timeout_seconds`` — lease duration before a stalled job is retried (default: ``120``).
    - ``batch_size`` — max jobs processed per poll cycle (default: ``10``).
    - ``max_attempts`` — retries before a job is marked failed (default: ``3``).
    - ``min_recurrence_interval_seconds`` — floor for recurring job intervals (default: ``60``).
    """

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
    """Structured logging settings. TOML section: ``[logging]``

    - ``structured`` — enable structured (JSON-like) log output (default: ``true``).
    - ``logfmt_enabled`` — use logfmt key=value format instead of JSON (default: ``true``).
    - ``log_level`` — log level for the logging subsystem (default: ``"INFO"``).
    - ``kv_separator`` — separator between key and value in logfmt (default: ``"="``).
    - ``record_separator`` — separator between fields in logfmt (default: ``" "``).
    """

    structured: bool = True
    logfmt_enabled: bool = True
    log_level: str = "INFO"
    kv_separator: str = "="
    record_separator: str = " "


class Settings(BaseModel):
    runtime: RuntimeConfig = RuntimeConfig()
    channels: dict[str, TelegramChannelConfig] = Field(
        default_factory=lambda: {"telegram": TelegramChannelConfig(bot_token="")}
    )
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    llm: LLMMConfig = LLMMConfig()
    orchestration: OrchestrationConfig = OrchestrationConfig()
    memory: MemoryConfig = MemoryConfig()
    tools: ToolsConfig = ToolsConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    logging: LoggingConfig = LoggingConfig()
    rabbitmq: RabbitMQConsumerConfig = RabbitMQConsumerConfig()

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        return cls.model_validate(_normalize_for_annotation(data, cls))

    @classmethod
    def from_file(cls, path: Path | None = None) -> Settings:
        if path is None:
            raise ValueError("config file path is required")
        return cls.from_dict(_load_file_data(path))
