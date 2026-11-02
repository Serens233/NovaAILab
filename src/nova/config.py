"""类型化环境配置；只有显式加载时才读取本地 .env。"""

import ipaddress
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

QWEN38_MODEL = "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL"


class ConfigurationError(Exception):
    """可安全显示的配置错误，不携带配置值或原始校验异常。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="forbid",
        populate_by_name=True,
        case_sensitive=False,
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = Field(
        default="development", alias="NOVA_ENV"
    )
    host: str = Field(default="127.0.0.1", alias="NOVA_HOST")
    port: int = Field(default=8000, ge=1, le=65535, alias="NOVA_PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", alias="NOVA_LOG_LEVEL"
    )
    data_dir: Path = Field(default=Path("data"), alias="NOVA_DATA_DIR")
    database_path: Path = Field(default=Path("data/nova.sqlite3"), alias="NOVA_DATABASE_PATH")
    db_timeout: float = Field(
        default=5.0, ge=0.1, le=30.0, allow_inf_nan=False, alias="NOVA_DB_TIMEOUT"
    )

    # 保留 Phase 0 配置契约；本阶段没有 Provider，不使用这些值联网。
    llm_provider: Literal["deepseek"] = Field(default="deepseek", alias="NOVA_LLM_PROVIDER")
    model: str = Field(default="", alias="DEEPSEEK_MODEL")
    api_key: SecretStr = Field(
        default=SecretStr(""), alias="DEEPSEEK_API_KEY", repr=False, exclude=True
    )

    gpu_enabled: bool = Field(default=False, alias="NOVA_GPU_ENABLED")
    gpu_token: SecretStr = Field(
        default=SecretStr(""), alias="NOVA_GPU_TOKEN", repr=False, exclude=True
    )
    gpu_ollama_url: str = Field(default="http://127.0.0.1:11434", alias="NOVA_GPU_OLLAMA_URL")
    gpu_model: str = Field(
        default="qwen3.5:4b", min_length=1, max_length=128, alias="NOVA_GPU_MODEL"
    )
    gpu_drain_timeout: float = Field(
        default=30, ge=0.1, le=120, allow_inf_nan=False, alias="NOVA_GPU_DRAIN_TIMEOUT"
    )
    gpu_request_timeout: float = Field(
        default=120, ge=1, le=300, allow_inf_nan=False, alias="NOVA_GPU_REQUEST_TIMEOUT"
    )
    gpu_game_detection_enabled: bool = Field(default=False, alias="NOVA_GPU_GAME_DETECTION_ENABLED")
    gpu_game_processes: list[str] = Field(default_factory=list, alias="NOVA_GPU_GAME_PROCESSES")
    gpu_game_poll_seconds: int = Field(default=5, ge=5, le=10, alias="NOVA_GPU_GAME_POLL_SECONDS")
    gpu_auto_game_enabled: bool = Field(default=False, alias="NOVA_GPU_AUTO_GAME_ENABLED")
    gpu_memory_pressure_enabled: bool = Field(
        default=False, alias="NOVA_GPU_MEMORY_PRESSURE_ENABLED"
    )
    chat_enabled: bool = Field(default=False, alias="NOVA_CHAT_ENABLED")
    chat_default_model: Literal["qwen3.5:9b", "qwen3.5:4b"] = Field(
        default="qwen3.5:9b", alias="NOVA_CHAT_DEFAULT_MODEL"
    )
    chat_think: bool = Field(default=False, alias="NOVA_CHAT_THINK")
    chat_fallback_model: Literal["", "qwen3.5:9b", "qwen3.5:4b"] = Field(
        default="", alias="NOVA_CHAT_FALLBACK_MODEL"
    )
    chat_idle_seconds: float = Field(
        default=180, ge=1, le=3600, allow_inf_nan=False, alias="NOVA_CHAT_IDLE_SECONDS"
    )
    cloud_enabled: bool = Field(default=False, alias="NOVA_CLOUD_ENABLED")
    persona_config_dir: Path | None = Field(default=None, alias="NOVA_PERSONA_CONFIG_DIR")

    @field_validator("persona_config_dir", mode="before")
    @classmethod
    def optional_persona_directory(cls, value):
        if value is None or isinstance(value, str) and not value.strip():
            return None
        if not isinstance(value, (str, Path)):
            raise ValueError("人格配置目录必须为空或路径")
        return Path(value).expanduser().resolve()

    # 实验模型不替换稳定默认值，默认只登记而不允许加载。
    qwen38_enabled: bool = Field(default=False, alias="NOVA_QWEN38_ENABLED")
    qwen38_model: str = Field(default=QWEN38_MODEL, max_length=128, alias="NOVA_QWEN38_MODEL")
    qwen38_context: int = Field(default=4096, ge=512, le=8192, alias="NOVA_QWEN38_CONTEXT")
    qwen38_num_predict: int = Field(default=256, ge=1, le=1024, alias="NOVA_QWEN38_NUM_PREDICT")
    qwen38_keep_alive: int = Field(default=180, ge=0, le=180, alias="NOVA_QWEN38_KEEP_ALIVE")
    qwen38_num_gpu: int | None = Field(default=None, ge=0, le=256, alias="NOVA_QWEN38_NUM_GPU")
    qwen38_temperature: float = Field(
        default=0.7, ge=0, le=2, allow_inf_nan=False, alias="NOVA_QWEN38_TEMPERATURE"
    )
    qwen38_min_total_ram_mib: int = Field(
        default=28672, ge=24576, le=1048576, alias="NOVA_QWEN38_MIN_TOTAL_RAM_MIB"
    )
    qwen38_min_available_ram_mib: int = Field(
        default=18432, ge=12288, le=1048576, alias="NOVA_QWEN38_MIN_AVAILABLE_RAM_MIB"
    )
    qwen38_min_free_vram_mib: int = Field(
        default=4096, ge=2048, le=65536, alias="NOVA_QWEN38_MIN_FREE_VRAM_MIB"
    )

    @field_validator("qwen38_model")
    @classmethod
    def validate_experimental_model(cls, value):
        if not re.fullmatch(
            r"hf\.co/[A-Za-z0-9][A-Za-z0-9._-]{0,95}/"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}:[A-Za-z0-9][A-Za-z0-9._-]{0,63}",
            value,
        ):
            raise ValueError("实验模型必须为公开hf.co/namespace/repo:tag标识")
        return value

    @field_validator("qwen38_num_gpu", mode="before")
    @classmethod
    def blank_gpu_layers_means_auto(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("gpu_game_processes")
    @classmethod
    def validate_game_processes(cls, value):
        from nova.gpu.processes import normalize_game_names

        return normalize_game_names(value)

    @field_validator("gpu_ollama_url")
    @classmethod
    def validate_gpu_url(cls, value):
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(value)
            if (
                value != value.strip()
                or any(c.isspace() for c in value)
                or any(c in value for c in "\\?#")
                or parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.port is None
                or not 1 <= parsed.port <= 65535
            ):
                raise ValueError
        except ValueError:
            raise ValueError("仅允许带端口的本机 127.0.0.1 HTTP 服务根地址") from None
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_gpu_security(self):
        if self.chat_enabled and not self.gpu_enabled:
            raise ValueError("聊天必须经过已启用的GPU管理器")
        if (
            self.gpu_auto_game_enabled or self.gpu_memory_pressure_enabled
        ) and not self.gpu_enabled:
            raise ValueError("自动检测和显存保护要求启用GPU管理")
        if self.gpu_auto_game_enabled and self.gpu_game_detection_enabled:
            raise ValueError("请选择自动检测或旧白名单模式之一")
        if self.gpu_game_detection_enabled and (
            not self.gpu_enabled or not self.gpu_game_processes
        ):
            raise ValueError("游戏检测要求开启GPU管理并提供非空EXE白名单")
        if self.gpu_enabled:
            if self.host not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("启用 GPU 管理时只能监听本机")
            if len(self.gpu_token.get_secret_value()) < 32:
                raise ValueError("启用 GPU 管理需要至少32字符的随机令牌")
        return self

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = value.strip()
        if value == "localhost":
            return value
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("监听地址必须为 IP 地址或 localhost") from None
        return value

    @field_validator(
        "port",
        "db_timeout",
        "gpu_drain_timeout",
        "gpu_request_timeout",
        "gpu_game_poll_seconds",
        "chat_idle_seconds",
        "qwen38_context",
        "qwen38_num_predict",
        "qwen38_keep_alive",
        "qwen38_num_gpu",
        "qwen38_temperature",
        "qwen38_min_total_ram_mib",
        "qwen38_min_available_ram_mib",
        "qwen38_min_free_vram_mib",
        mode="before",
    )
    @classmethod
    def reject_boolean_numbers(cls, value):
        if isinstance(value, bool):
            raise ValueError("数值配置不能使用布尔值")
        return value

    @field_validator("data_dir", "database_path", mode="before")
    @classmethod
    def reject_empty_paths(cls, value):
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("路径不能为空")
            if "\x00" in value:
                raise ValueError("路径不能包含空字符")
        return value

    @field_validator("data_dir", "database_path")
    @classmethod
    def resolve_paths(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    def secret_values(self) -> tuple[str, ...]:
        return tuple(
            value
            for secret in (self.api_key, self.gpu_token)
            if (value := secret.get_secret_value())
        )


_FIELD_HINTS = {
    "NOVA_ENV": "必须为 development、test 或 production",
    "NOVA_HOST": "必须为 IP 地址或 localhost",
    "NOVA_PORT": "必须为 1 至 65535 的整数",
    "NOVA_LOG_LEVEL": "必须为 DEBUG、INFO、WARNING、ERROR 或 CRITICAL",
    "NOVA_DATA_DIR": "必须为非空有效路径",
    "NOVA_DATABASE_PATH": "必须为非空有效路径",
    "NOVA_DB_TIMEOUT": "必须为 0.1 至 30 秒的有限数值",
    "NOVA_LLM_PROVIDER": "预留字段必须为 deepseek",
    "DEEPSEEK_MODEL": "预留字段必须为字符串",
    "DEEPSEEK_API_KEY": "预留字段必须为字符串",
    "NOVA_GPU_ENABLED": "必须为布尔值；启用时要求回环监听和至少32字符的 NOVA_GPU_TOKEN",
    "NOVA_GPU_TOKEN": "必须为至少32字符的随机令牌，禁止提交或记录",
    "NOVA_GPU_OLLAMA_URL": "必须为本机127.0.0.1的HTTP根地址并带端口",
    "NOVA_GPU_MODEL": "必须为本地已安装模型名",
    "NOVA_GPU_DRAIN_TIMEOUT": "必须为0.1至120秒的有限数值",
    "NOVA_GPU_REQUEST_TIMEOUT": "必须为1至300秒的有限数值",
    "NOVA_GPU_GAME_DETECTION_ENABLED": "必须为布尔值；开启前启用GPU管理并配置EXE白名单",
    "NOVA_GPU_GAME_PROCESSES": "必须为JSON数组，仅含EXE文件名，不得使用路径或通配符",
    "NOVA_GPU_GAME_POLL_SECONDS": "必须为5至10秒的整数",
    "NOVA_GPU_AUTO_GAME_ENABLED": "开启时要求GPU管理已启用，并关闭旧白名单检测开关",
    "NOVA_GPU_MEMORY_PRESSURE_ENABLED": "必须为布尔值，开启时要求GPU管理已启用",
    "NOVA_CHAT_ENABLED": "必须为布尔值，开启时要求GPU管理已启用",
    "NOVA_CHAT_DEFAULT_MODEL": "必须为qwen3.5:9b或qwen3.5:4b",
    "NOVA_CHAT_THINK": "必须为布尔值；思考内容不会作为聊天正文展示或保存",
    "NOVA_CHAT_FALLBACK_MODEL": "必须为空、qwen3.5:9b或qwen3.5:4b；只允许显式配置的本地回退",
    "NOVA_CHAT_IDLE_SECONDS": "必须为1至3600秒的有限数值，默认180秒",
    "NOVA_CLOUD_ENABLED": "必须为布尔值；付费云端默认关闭",
    "NOVA_PERSONA_CONFIG_DIR": "必须为空或只读、已审核人格配置目录；默认使用包内版本",
    "NOVA_QWEN38_ENABLED": "必须为布尔值，实验模型默认关闭",
    "NOVA_QWEN38_MODEL": "必须为公开hf.co/namespace/repo:tag标识，不允许本机路径或URL",
    "NOVA_QWEN38_CONTEXT": "必须为512至8192的整数，初始4096",
    "NOVA_QWEN38_NUM_PREDICT": "必须为1至1024的整数",
    "NOVA_QWEN38_KEEP_ALIVE": "必须为0至180秒的整数",
    "NOVA_QWEN38_NUM_GPU": "必须为空值或0至256的整数，默认由Ollama自动分配",
    "NOVA_QWEN38_TEMPERATURE": "必须为0至2的有限数值",
    "NOVA_QWEN38_MIN_TOTAL_RAM_MIB": "必须为24576至1048576的整数，不能降低到16GB",
    "NOVA_QWEN38_MIN_AVAILABLE_RAM_MIB": "必须为12288至1048576的整数",
    "NOVA_QWEN38_MIN_FREE_VRAM_MIB": "必须为2048至65536的整数",
}


def load_settings(env_file: Path | str | None = ".env") -> Settings:
    """环境变量优先于指定 .env；错误只报告字段和固定提示。"""
    try:
        if env_file is not None:
            path = Path(env_file)
            if path.exists() and not path.is_file():
                raise ConfigurationError("本地配置路径必须为文件，请检查 .env 配置。")
        return Settings(_env_file=env_file)
    except ValidationError as error:
        aliases = {name: field.alias for name, field in Settings.model_fields.items()}
        hints = set()
        for item in error.errors(include_input=False, include_context=False, include_url=False):
            if not item["loc"]:
                hints.add(
                    "NOVA_GPU_ENABLED：启用时要求回环监听及至少32字符NOVA_GPU_TOKEN；旧白名单检测要求非空NOVA_GPU_GAME_PROCESSES；自动检测与旧白名单模式不能同时开启；显存保护要求GPU管理开启"
                )
                continue
            location = item["loc"][0] if item["loc"] else None
            alias = aliases.get(location, location)
            if alias in _FIELD_HINTS:
                hints.add(f"{alias}：{_FIELD_HINTS[alias]}")
            else:
                hints.add("存在未声明的 .env 配置项，请核对变量名称")
        raise ConfigurationError("配置无效；" + "；".join(sorted(hints)) + "。") from None
    except SettingsError, OSError, UnicodeError:
        raise ConfigurationError(
            "配置读取失败，请检查 .env 文件、权限、UTF-8 编码和字段类型。"
        ) from None
