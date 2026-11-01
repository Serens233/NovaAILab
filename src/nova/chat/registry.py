"""只读发现本地模型；27B别名共享资源策略，不自动下载或调用云端。"""

import re

from nova.config import QWEN38_MODEL as QWEN38_MODEL
from nova.errors import ApplicationError
from nova.gpu.backend import BackendError
from nova.gpu.resources import require_system_memory

MODELS = {
    "qwen3.5:9b": {"label": "Qwen3.5 · 9B", "provider": "ollama", "required_mib": 9500},
    "qwen3.5:4b": {"label": "Qwen3.5 · 4B", "provider": "ollama", "required_mib": 5500},
    "deepseek": {"label": "DeepSeek · 云端", "provider": "deepseek", "required_mib": 0},
}
_LOCAL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?\Z")
_QWEN38_GGUF = re.compile(
    r"hf\.co/unsloth/Qwen3\.8-27B-GGUF:[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.IGNORECASE
)
# 本机导入的量化标签形如 qwen3.8:27b-q3_k_xl；后缀仍走同一RAM/启用策略。
_QWEN38_LOCAL = re.compile(r"qwen3\.8:27b-[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z", re.IGNORECASE)


def _qwen38_alias(name):
    if not isinstance(name, str) or name.casefold().endswith((":cloud", "-cloud")):
        return False
    return (
        name.casefold() == "qwen3.8:27b"
        or bool(_QWEN38_LOCAL.fullmatch(name))
        or bool(_QWEN38_GGUF.fullmatch(name))
    )


def experimental_policy(settings):
    runtime = {
        "num_ctx": settings.qwen38_context,
        "num_predict": settings.qwen38_num_predict,
        "keep_alive": settings.qwen38_keep_alive,
        "temperature": settings.qwen38_temperature,
    }
    if settings.qwen38_num_gpu is not None:
        runtime["num_gpu"] = settings.qwen38_num_gpu
    return {
        "model": settings.qwen38_model,
        "enabled": settings.qwen38_enabled,
        "required_mib": settings.qwen38_min_free_vram_mib,
        "min_total_ram_mib": settings.qwen38_min_total_ram_mib,
        "min_available_ram_mib": settings.qwen38_min_available_ram_mib,
        "runtime": runtime,
    }


class ModelRegistry:
    def __init__(self, settings, backend):
        self.settings = settings
        self.backend = backend
        self.policy = experimental_policy(settings)
        self.installed = frozenset()
        self.reachable = False
        self.entries = self._entries(())

    def _entries(self, installed):
        """只开放已明确支持的27B标识；任意安装的大模型不自动获得加载资格。"""
        experimental = {
            self.policy["model"],
            *(name for name in installed if _qwen38_alias(name)),
        }
        entries = {model: dict(value) for model, value in MODELS.items()}
        for model in sorted(experimental):
            entries[model] = {
                "label": "Qwen3.8 · 27B · 实验",
                "provider": "ollama",
                "required_mib": self.policy["required_mib"],
                "experimental": True,
            }
        return entries

    async def refresh(self):
        """只执行tags查询；故障时清空发现快照，不沿用上次可用的别名。"""
        try:
            names = await self.backend.list_installed_models()
            if not isinstance(names, list) or len(names) > 4096:
                raise BackendError("本地模型列表无效。")
            installed = set()
            for name in names:
                if (
                    not isinstance(name, str)
                    or not 1 <= len(name) <= 128
                    or not _LOCAL_NAME.fullmatch(name)
                ):
                    raise BackendError("本地模型列表无效。")
                # 官方适配器已过滤remote元数据；此处再拒绝明确的云模型后缀。
                if not name.casefold().endswith((":cloud", "-cloud")):
                    installed.add(name)
            reachable = True
        except BackendError:
            installed, reachable = set(), False
        # 此处至返回前无await，确保每次发布的是同一快照而非部分更新。
        self.installed = frozenset(installed)
        self.reachable = reachable
        self.entries = self._entries(installed)
        return {"installed": sorted(installed), "reachable": reachable}

    def local_policies(self):
        """实际别名全部复制同一RAM/启用/运行参数策略，供唯一GPU管理器装配。"""
        return {
            model: {**self.policy, "model": model, "runtime": dict(self.policy["runtime"])}
            for model, entry in self.entries.items()
            if entry.get("experimental")
        }

    def _installed_experiments(self):
        return sorted(
            (
                model
                for model, entry in self.entries.items()
                if entry.get("experimental") and model in self.installed
            ),
            key=lambda model: (
                model.casefold() != "qwen3.8:27b",
                model != self.policy["model"],
                model.casefold(),
                model,
            ),
        )

    def _is_experimental(self, name):
        return isinstance(name, str) and (
            name.casefold() == self.policy["model"].casefold() or _qwen38_alias(name)
        )

    def _require_experiment_enabled(self):
        if not self.policy["enabled"]:
            raise ApplicationError(
                "MODEL_EXPERIMENT_DISABLED", "27B实验模型尚未启用，请使用9B或4B", 503
            )

    async def resolve(self, model=None):
        """解析请求的实际本地标识；None保留稳定默认，不静默升级至27B。"""
        requested = self.settings.chat_default_model if model is None else model
        # 开关与RAM门禁必须先于/api/tags查询：不足时不得接触Ollama或驱逐常驻模型。
        if self._is_experimental(requested):
            self._require_experiment_enabled()
            require_system_memory(
                self.policy["min_total_ram_mib"], self.policy["min_available_ram_mib"]
            )
        await self.refresh()
        model = requested
        if self._is_experimental(model):
            installed = self._installed_experiments()
            if model not in self.installed and installed:
                model = installed[0]
            elif model not in self.entries and _qwen38_alias(model):
                # 缺失的规范别名使用声明入口，统一禁用/未安装错误。
                model = self.policy["model"]
        entry = self.get(model)
        if entry["provider"] == "ollama":
            if not self.reachable:
                raise ApplicationError("OLLAMA_UNAVAILABLE", "Ollama 未运行，请检查本机服务", 503)
            if model not in self.installed:
                raise ApplicationError("MODEL_NOT_INSTALLED", "模型尚未下载，不会自动下载", 503)
            if entry.get("experimental"):
                # 映射后复核：第一次门禁在 /api/tags 之前，这里确认解析出的实际别名
                # 仍满足同一内存策略，避免持久化别名绕过或依赖缓存结果。
                require_system_memory(
                    self.policy["min_total_ram_mib"], self.policy["min_available_ram_mib"]
                )
                entry["policy"] = self.local_policies()[model]
        entry["requested_model"] = requested
        return entry

    def get(self, model):
        if not isinstance(model, str) or model not in self.entries:
            raise ApplicationError("MODEL_UNKNOWN", "请选择 Nova 已注册的模型", 422)
        if self.entries[model].get("experimental"):
            self._require_experiment_enabled()
        return {"id": model, **self.entries[model]}

    def cloud_ready(self):
        return self.settings.cloud_enabled and bool(self.settings.api_key.get_secret_value())

    async def list(self):
        await self.refresh()
        installed, reachable = self.installed, self.reachable
        result = []
        for model, values in self.entries.items():
            entry = {"id": model, **values}
            if entry["provider"] == "ollama":
                entry.update(installed=model in installed, available=model in installed)
                entry["reason"] = (
                    "" if model in installed else "模型尚未下载" if reachable else "Ollama 未运行"
                )
                if entry.get("experimental"):
                    entry["enabled"] = self.policy["enabled"]
                    if not self.policy["enabled"]:
                        entry.update(
                            available=False, reason="实验模型默认关闭，需核实来源并显式启用"
                        )
                    elif entry["available"]:
                        try:
                            require_system_memory(
                                self.policy["min_total_ram_mib"],
                                self.policy["min_available_ram_mib"],
                            )
                        except ApplicationError as error:
                            entry.update(available=False, reason=error.public_message)
            else:
                entry.update(
                    available=self.cloud_ready(),
                    requires_cloud_consent=True,
                    reason="" if self.cloud_ready() else "云端未启用或未配置密钥",
                )
            result.append(entry)
        available = {entry["id"] for entry in result if entry["available"]}
        preferred = next(
            (model for model in self._installed_experiments() if model in available),
            next(
                (
                    model
                    for model in (self.settings.chat_default_model, "qwen3.5:9b", "qwen3.5:4b")
                    if model in available
                ),
                None,
            ),
        )
        return {
            "models": result,
            "default_model": self.settings.chat_default_model,
            "preferred_model": preferred,
            "discovered_models": [
                {"id": model, "selectable": False, "reason": "尚未配置资源策略，不允许自动加载"}
                for model in sorted(installed - self.entries.keys())
            ],
        }

    def require_cloud(self, consent):
        if not self.cloud_ready():
            raise ApplicationError("CLOUD_DISABLED", "DeepSeek 云端尚未启用或配置", 503)
        if consent is not True:
            raise ApplicationError("CLOUD_CONSENT_REQUIRED", "调用云端可能计费，请先明确同意", 403)
