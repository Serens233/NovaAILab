"""实验模型配置、只读目录与内存门禁；无网络、无真实模型加载。"""

import asyncio

import pytest
from pydantic import ValidationError

from nova.chat.registry import MODELS, QWEN38_MODEL, ModelRegistry, experimental_policy
from nova.config import ConfigurationError, Settings, load_settings
from nova.errors import ApplicationError
from nova.gpu import resources
from nova.gpu.backend import BackendError


class CatalogBackend:
    def __init__(self, names=(), unavailable=False):
        self.names = list(names)
        self.unavailable = unavailable

    async def list_installed_models(self):
        if self.unavailable:
            raise BackendError("synthetic upstream private detail")
        return self.names


def registered(settings=None, names=("qwen3.5:9b", "qwen3.5:4b", QWEN38_MODEL), **kwargs):
    return ModelRegistry(settings or Settings(), CatalogBackend(names, **kwargs))


def model_list(registry):
    result = asyncio.run(registry.list())
    return {item["id"]: item for item in result["models"]}


def memory(monkeypatch, *, total=32768, free=22000, available=True):
    monkeypatch.setattr(
        resources,
        "get_system_memory",
        lambda: {"available": available, "total_mib": total, "available_mib": free},
    )


def test_default_stable_models_and_experimental_policy():
    settings = Settings()
    policy = experimental_policy(settings)
    assert settings.chat_default_model == "qwen3.5:9b"
    assert settings.gpu_model == "qwen3.5:4b"
    assert set(MODELS) == {"qwen3.5:9b", "qwen3.5:4b", "deepseek"}
    assert policy == {
        "model": QWEN38_MODEL,
        "enabled": False,
        "required_mib": 4096,
        "min_total_ram_mib": 28672,
        "min_available_ram_mib": 18432,
        "runtime": {"num_ctx": 4096, "num_predict": 256, "keep_alive": 180, "temperature": 0.7},
    }
    assert not settings.cloud_enabled


def test_disabled_experiment_is_listed_but_cannot_be_requested(monkeypatch):
    def no_telemetry_needed(*args):
        pytest.fail("关闭的模型不应要求读取内存，更不能加载模型")

    monkeypatch.setattr(resources, "get_system_memory", no_telemetry_needed)
    registry = registered()
    items = model_list(registry)
    assert items[QWEN38_MODEL]["installed"] is True
    assert items[QWEN38_MODEL]["available"] is False
    assert items[QWEN38_MODEL]["experimental"] is True
    assert "关闭" in items[QWEN38_MODEL]["reason"]
    for name in ("qwen3.5:9b", "qwen3.5:4b"):
        assert items[name]["available"] is True
        assert registry.get(name)["id"] == name
    with pytest.raises(ApplicationError) as error:
        registry.get(QWEN38_MODEL)
    assert (error.value.code, error.value.status_code) == ("MODEL_EXPERIMENT_DISABLED", 503)


def test_enabled_installed_model_becomes_available_only_with_enough_ram(monkeypatch):
    memory(monkeypatch)
    registry = registered(Settings(qwen38_enabled=True))
    assert model_list(registry)[QWEN38_MODEL]["available"] is True
    assert registry.get(QWEN38_MODEL)["required_mib"] == 4096


@pytest.mark.parametrize(
    ("total", "free"), [(15472, 12000), (32768, 5000), (28671, 22000), (32768, 18431)]
)
def test_low_ram_never_advertised_as_available(monkeypatch, total, free):
    memory(monkeypatch, total=total, free=free)
    items = model_list(registered(Settings(qwen38_enabled=True)))
    assert items[QWEN38_MODEL]["available"] is False
    assert "内存不足" in items[QWEN38_MODEL]["reason"]
    assert items["qwen3.5:9b"]["available"] is True
    with pytest.raises(ApplicationError) as error:
        resources.require_system_memory(28672, 18432)
    assert (error.value.code, error.value.status_code) == ("MODEL_RAM_INSUFFICIENT", 409)


def test_exact_ram_boundary_is_accepted(monkeypatch):
    memory(monkeypatch, total=28672, free=18432)
    assert resources.require_system_memory(28672, 18432)["available_mib"] == 18432


@pytest.mark.parametrize(
    "sample",
    [
        {"available": False, "total_mib": 32768, "available_mib": 22000},
        {"available": True, "total_mib": 32768, "available_mib": -1},
        {"available": True, "total_mib": 32768, "available_mib": 32769},
        {"available": True, "total_mib": True, "available_mib": 0},
        {"available": True, "total_mib": 32768, "available_mib": "22000"},
        {"available": True},
    ],
)
def test_unknown_or_invalid_telemetry_fails_closed(monkeypatch, sample):
    monkeypatch.setattr(resources, "get_system_memory", lambda: sample)
    with pytest.raises(ApplicationError) as error:
        resources.require_system_memory(28672, 18432)
    assert (error.value.code, error.value.status_code) == ("TELEMETRY_UNAVAILABLE", 503)
    entry = model_list(registered(Settings(qwen38_enabled=True)))[QWEN38_MODEL]
    assert not entry["available"]
    assert "无法读取" in entry["reason"]


def test_missing_model_and_unavailable_ollama_do_not_enable_experiment(monkeypatch):
    memory(monkeypatch)
    missing = model_list(registered(Settings(qwen38_enabled=True), names=[]))[QWEN38_MODEL]
    assert not missing["installed"] and not missing["available"]
    assert missing["reason"] == "模型尚未下载"
    missing = model_list(registered(Settings(qwen38_enabled=True), unavailable=True))[QWEN38_MODEL]
    assert not missing["available"] and missing["reason"] == "Ollama 未运行"
    assert "private" not in missing["reason"]


def test_unknown_model_still_rejected():
    with pytest.raises(ApplicationError) as error:
        registered().get("unregistered:model")
    assert error.value.code == "MODEL_UNKNOWN"


def test_configurable_hf_model_and_runtime_do_not_mutate_original_registry(monkeypatch):
    memory(monkeypatch)
    model = "hf.co/example/public-model:Q3_K_XL"
    settings = Settings(
        qwen38_enabled=True,
        qwen38_model=model,
        qwen38_context=2048,
        qwen38_num_predict=64,
        qwen38_keep_alive=0,
        qwen38_num_gpu=18,
        qwen38_temperature=0,
    )
    policy = experimental_policy(settings)
    assert policy["runtime"] == {
        "num_ctx": 2048,
        "num_predict": 64,
        "keep_alive": 0,
        "temperature": 0,
        "num_gpu": 18,
    }
    entries = model_list(registered(settings, names=[model]))
    assert entries[model]["available"] is True
    assert QWEN38_MODEL not in entries
    assert model not in MODELS


def test_env_can_select_auto_gpu_and_override_typed_values(monkeypatch, tmp_path):
    env_file = tmp_path / "v3.env"
    env_file.write_text(
        "NOVA_QWEN38_ENABLED=true\nNOVA_QWEN38_CONTEXT=2048\nNOVA_QWEN38_NUM_GPU=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NOVA_QWEN38_NUM_PREDICT", "32")
    settings = load_settings(env_file)
    assert settings.qwen38_enabled is True
    assert settings.qwen38_context == 2048
    assert settings.qwen38_num_predict == 32
    assert settings.qwen38_num_gpu is None


@pytest.mark.parametrize(
    "value",
    [
        "https://hf.co/unsloth/model:Q3",
        "C:/Users/private/model.gguf",
        "hf.co/../model:Q3",
        "hf.co/org/repo/extra:Q3",
        "hf.co/org/repo:Q3?key=SYNTHETIC_PRIVATE_VALUE",
        "hf.co/org/repo",
        "hf.co/org/repo:Q3 cloud",
    ],
)
def test_experiment_model_is_public_hf_identifier_only(value):
    with pytest.raises(ValidationError):
        Settings(qwen38_model=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qwen38_context", 511),
        ("qwen38_context", 8193),
        ("qwen38_num_predict", 0),
        ("qwen38_num_predict", 1025),
        ("qwen38_keep_alive", -1),
        ("qwen38_keep_alive", 181),
        ("qwen38_num_gpu", -1),
        ("qwen38_num_gpu", 257),
        ("qwen38_temperature", float("inf")),
        ("qwen38_temperature", float("nan")),
        ("qwen38_temperature", -0.1),
        ("qwen38_min_total_ram_mib", 16384),
        ("qwen38_min_available_ram_mib", 4096),
        ("qwen38_min_free_vram_mib", 1024),
        ("qwen38_min_total_ram_mib", 1048577),
        ("qwen38_min_available_ram_mib", 1048577),
        ("qwen38_min_free_vram_mib", 65537),
    ],
)
def test_resource_configuration_is_bounded(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "qwen38_context",
        "qwen38_num_predict",
        "qwen38_keep_alive",
        "qwen38_num_gpu",
        "qwen38_temperature",
        "qwen38_min_total_ram_mib",
        "qwen38_min_available_ram_mib",
        "qwen38_min_free_vram_mib",
    ],
)
def test_boolean_is_not_a_memory_or_runtime_number(field):
    with pytest.raises(ValidationError):
        Settings(**{field: True})


def test_invalid_config_error_does_not_expose_value(tmp_path):
    env_file = tmp_path / "v3.env"
    env_file.write_text("NOVA_QWEN38_MODEL=private-SYNTHETIC-CREDENTIAL\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        load_settings(env_file)
    assert "NOVA_QWEN38_MODEL" in str(error.value)
    assert "SYNTHETIC-CREDENTIAL" not in str(error.value)


@pytest.mark.parametrize(
    "values",
    [(32 * 1024**3, 20 * 1024**3), (28672 * 1024**2, 18432 * 1024**2)],
)
def test_native_telemetry_conversion_uses_physical_bytes(monkeypatch, values):
    monkeypatch.setattr(resources, "_read_system_memory", lambda: values)
    assert resources.get_system_memory() == {
        "available": True,
        "total_mib": values[0] // 1024**2,
        "available_mib": values[1] // 1024**2,
    }


@pytest.mark.parametrize(
    "values", [(0, 0), (-1, 0), (1024**3, -1), (1024**3, 2 * 1024**3), (True, 0)]
)
def test_native_telemetry_invalid_reading_is_unknown(monkeypatch, values):
    monkeypatch.setattr(resources, "_read_system_memory", lambda: values)
    assert resources.get_system_memory()["available"] is False


def test_native_api_failure_is_unknown(monkeypatch):
    def failed():
        raise OSError("SYNTHETIC_PRIVATE_API_ERROR")

    monkeypatch.setattr(resources, "_read_system_memory", failed)
    assert resources.get_system_memory() == {
        "available": False,
        "total_mib": 0,
        "available_mib": 0,
    }
