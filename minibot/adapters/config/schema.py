from __future__ import annotations

import importlib
import tomllib
import types
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel, BeforeValidator, ByteSize, ConfigDict, Field, PositiveInt, TypeAdapter, ValidationError
from pydantic import model_validator


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


def _load_file_data(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        with path.open("rb") as fp:
            data = tomllib.load(fp)
        return data
    if suffix == ".lua":
        return _load_lua_file_data(path)
    raise ValueError(f"unsupported config file type: {path.suffix or '<none>'}")


def _load_lua_file_data(path: Path) -> Dict[str, Any]:
    try:
        lupa = importlib.import_module("lupa")
    except ModuleNotFoundError as exc:
        raise RuntimeError("Lua config support requires `lupa`; install with `poetry install --extras lua`") from exc

    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    result = lua.execute(path.read_text(encoding="utf-8"))
    if isinstance(result, tuple):
        raise ValueError("lua config must return a single table")
    data = _lua_to_python(result)
    if not isinstance(data, dict):
        raise ValueError("lua config must return a top-level table")
    return data


def _lua_to_python(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_lua_to_python(item) for item in value]
    if isinstance(value, dict):
        return {key: _lua_to_python(item) for key, item in value.items()}
    if not hasattr(value, "items"):
        return value

    items = [(key, item) for key, item in value.items()]
    if not items:
        return {}

    keys = [key for key, _ in items]
    if all(isinstance(key, str) for key in keys):
        return {key: _lua_to_python(item) for key, item in items}

    if all(isinstance(key, int) and not isinstance(key, bool) and key > 0 for key in keys):
        ordered_keys = sorted(keys)
        expected_keys = list(range(1, len(keys) + 1))
        if ordered_keys != expected_keys:
            raise ValueError("lua config arrays must use consecutive integer keys starting at 1")
        ordered_items = sorted(items, key=lambda item: item[0])
        return [_lua_to_python(item) for _, item in ordered_items]

    raise ValueError("lua config tables must use either string keys or consecutive integer keys")


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

    if origin in (list, List):
        item_annotation = get_args(annotation)[0] if get_args(annotation) else Any
        if value == {}:
            return []
        if isinstance(value, list):
            return [_normalize_for_annotation(item, item_annotation) for item in value]
        return value

    if origin in (dict, Dict):
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
    reasoning_enabled: bool | None = None
    plugins: List[Dict[str, Any]] = Field(default_factory=list)


class LLMMConfig(BaseModel):
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
    structured_output_mode: Literal["provider_with_fallback", "prompt_only", "provider_strict"] = (
        "provider_with_fallback"
    )
    reasoning_effort: str | None = None
    main_responses_state_mode: Literal["full_messages", "previous_response_id"] = "full_messages"
    agent_responses_state_mode: Literal["full_messages", "previous_response_id"] = "previous_response_id"
    responses_state_mode: Literal["full_messages", "previous_response_id"] = "full_messages"
    prompt_cache_enabled: bool = True
    prompt_cache_retention: Literal["in-memory", "24h"] | None = None
    openrouter: OpenRouterLLMConfig = OpenRouterLLMConfig()


class ProviderConfig(BaseModel):
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
    tools_allow: List[str] = Field(default_factory=list)
    tools_deny: List[str] = Field(default_factory=list)
    mcp_servers: List[str] = Field(default_factory=list)
    openrouter_provider_overrides: Dict[str, Any] = Field(default_factory=dict)
    openrouter_reasoning_enabled: bool | None = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _validate_tool_policy(self) -> "AgentDefinitionConfig":
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
    tools_allow: List[str] = Field(default_factory=list)
    tools_deny: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_tool_policy(self) -> "MainAgentConfig":
        if self.tools_allow and self.tools_deny:
            raise ValueError("only one of tools_allow or tools_deny can be set")
        return self


class OrchestrationConfig(BaseModel):
    directory: str = "./agents"
    default_timeout_seconds: PositiveInt = 90
    tool_ownership_mode: Literal["shared", "exclusive", "exclusive_mcp"] = "shared"
    delegated_tool_call_policy: Literal["auto", "always", "never"] = "auto"
    main_tool_use_guardrail: Literal["disabled", "llm_classifier"] = "disabled"
    main_agent: MainAgentConfig = MainAgentConfig()


class MemoryConfig(BaseModel):
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


class BashToolConfig(BaseModel):
    enabled: bool = False
    default_timeout_seconds: PositiveInt = 15
    max_timeout_seconds: PositiveInt = 120
    max_output_bytes: ByteSizeValue = 128000
    pass_parent_env: bool = True
    env_allowlist: List[str] = Field(default_factory=lambda: ["PATH", "HOME", "USER", "LANG", "LC_ALL", "SHELL"])


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
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: Dict[str, str] = Field(default_factory=dict)
    enabled_tools: List[str] = Field(default_factory=list)
    disabled_tools: List[str] = Field(default_factory=list)


class MCPToolConfig(BaseModel):
    enabled: bool = False
    name_prefix: str = "mcp"
    timeout_seconds: PositiveInt = 10
    servers: List[MCPServerConfig] = Field(default_factory=list)


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
    paths: List[str] = Field(default_factory=list)


class ToolsConfig(BaseModel):
    kv_memory: KeyValueMemoryConfig = KeyValueMemoryConfig()
    http_client: HTTPClientToolConfig = HTTPClientToolConfig()
    time: TimeToolConfig = TimeToolConfig()
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
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)
    llm: LLMMConfig = LLMMConfig()
    orchestration: OrchestrationConfig = OrchestrationConfig()
    memory: MemoryConfig = MemoryConfig()
    tools: ToolsConfig = ToolsConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    logging: LoggingConfig = LoggingConfig()

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        return cls.model_validate(_normalize_for_annotation(data, cls))

    @classmethod
    def from_file(cls, path: Path | None = None) -> "Settings":
        if path is None:
            raise ValueError("config file path is required")
        return cls.from_dict(_load_file_data(path))
