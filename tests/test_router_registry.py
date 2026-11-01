"""发现只能扩大可见目录，不能扩大本地模型、资源或云端权限。"""

import asyncio

import pytest
from pydantic import ValidationError

from nova.chat.registry import QWEN38_MODEL, ModelRegistry, experimental_policy
from nova.config import ConfigurationError, Settings, load_settings
from nova.errors import ApplicationError
from nova.gpu import resources
from nova.gpu.backend import BackendError

CANONICAL = "qwen3.8:27b"
OTHER_QUANT = "hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M"
LOCAL_QUANT = "qwen3.8:27b-q3_k_xl"


class ReadOnlyCatalog:
    def __init__(self, names=()):
        self.names = list(names)
        self.failed = False
        self.queries = 0

    async def list_installed_models(self):
        self.queries += 1
        if self.failed:
            raise BackendError("SYNTHETIC_PRIVATE_FAILURE")
        return self.names


@pytest.fixture
def enough_memory(monkeypatch):
    monkeypatch.setattr(
        resources,
        "get_system_memory",
        lambda: {"available": True, "total_mib": 32768, "available_mib": 22000},
    )


def router(names=(), **settings):
    backend = ReadOnlyCatalog(names)
    return ModelRegistry(Settings(**settings), backend), backend


def test_preferred_actual_name_does_not_replace_stable_default(enough_memory):
    async def scenario():
        registry, backend = router(
            [QWEN38_MODEL, OTHER_QUANT, CANONICAL, "qwen3.5:9b"], qwen38_enabled=True
        )
        listed = await registry.list()
        assert listed["preferred_model"] == CANONICAL
        assert listed["default_model"] == "qwen3.5:9b"
        assert (await registry.resolve())["id"] == "qwen3.5:9b"
        assert backend.queries == 2
        assert not hasattr(backend, "generate")

    asyncio.run(scenario())


@pytest.mark.parametrize("actual", [QWEN38_MODEL, OTHER_QUANT, LOCAL_QUANT, "QWEN3.8:27B"])
def test_canonical_resolves_only_to_installed_real_identifier(enough_memory, actual):
    async def scenario():
        registry, _ = router([actual], qwen38_enabled=True)
        entry = await registry.resolve(CANONICAL)
        assert entry["id"] == actual
        assert entry["requested_model"] == CANONICAL
        assert entry["policy"]["model"] == actual
        assert entry["policy"]["min_total_ram_mib"] == 28672
        assert entry["policy"]["min_available_ram_mib"] == 18432
        assert entry["policy"]["enabled"] is True

    asyncio.run(scenario())


def test_configured_hf_identifier_resolves_to_installed_canonical(enough_memory):
    async def scenario():
        registry, _ = router([CANONICAL], qwen38_enabled=True)
        assert (await registry.resolve(QWEN38_MODEL))["id"] == CANONICAL

    asyncio.run(scenario())


def test_local_quant_tag_is_listed_as_experimental_not_discovered(enough_memory):
    async def scenario():
        registry, _ = router([LOCAL_QUANT, "qwen3.5:9b"], qwen38_enabled=True)
        listed = await registry.list()
        entry = next(model for model in listed["models"] if model["id"] == LOCAL_QUANT)
        assert entry["installed"] is True
        assert entry["available"] is True
        assert entry["experimental"] is True
        assert listed["preferred_model"] == LOCAL_QUANT
        assert LOCAL_QUANT not in {model["id"] for model in listed["discovered_models"]}
        direct = await registry.resolve(LOCAL_QUANT)
        assert direct["id"] == LOCAL_QUANT
        assert direct["requested_model"] == LOCAL_QUANT
        assert direct["policy"]["model"] == LOCAL_QUANT

    asyncio.run(scenario())


@pytest.mark.parametrize("actual", [LOCAL_QUANT, CANONICAL, QWEN38_MODEL])
def test_experiment_gate_rejects_before_any_catalog_query(monkeypatch, actual):
    """开关和RAM不足都不能先查tags或接触常驻模型；错误码保持原有契约。"""
    monkeypatch.setattr(
        resources,
        "get_system_memory",
        lambda: {"available": True, "total_mib": 15477, "available_mib": 9000},
    )

    async def scenario():
        registry, backend = router([actual], qwen38_enabled=True)
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve(actual)
        assert caught.value.code == "MODEL_RAM_INSUFFICIENT"
        assert backend.queries == 0

        registry, backend = router([actual])
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve(actual)
        assert caught.value.code == "MODEL_EXPERIMENT_DISABLED"
        assert backend.queries == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name",
    [
        "qwen3.8:27b-",
        "qwen3.8:27b-a:b",
        "qwen3.8:27b-a/b",
        "qwen3.8:27b--q3",
        "qwen3.8:27b- good",
        "qwen3.8:27b-cloud",
        "qwen3.8:28b-q3_k_xl",
        "qwen3.8:27b_q3_k_xl",
    ],
)
def test_only_strict_27b_local_suffix_family_is_an_alias(enough_memory, name):
    async def scenario():
        registry, _ = router([name, CANONICAL], qwen38_enabled=True)
        await registry.refresh()
        assert name not in registry.local_policies()
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve(name)
        assert caught.value.code == "MODEL_UNKNOWN"

    asyncio.run(scenario())


def test_policy_copies_for_all_aliases_preserve_memory_and_enable_gate(enough_memory):
    async def scenario():
        registry, _ = router([CANONICAL, OTHER_QUANT], qwen38_enabled=True)
        await registry.refresh()
        policies = registry.local_policies()
        expected = experimental_policy(registry.settings)
        assert set(policies) == {CANONICAL, OTHER_QUANT, QWEN38_MODEL}
        for name, policy in policies.items():
            assert policy == {**expected, "model": name}
        policies[CANONICAL]["runtime"]["num_ctx"] = 8192
        policies[OTHER_QUANT]["enabled"] = False
        assert registry.local_policies()[CANONICAL]["runtime"]["num_ctx"] == 4096
        assert registry.local_policies()[OTHER_QUANT]["enabled"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize("requested", [CANONICAL, QWEN38_MODEL, OTHER_QUANT, LOCAL_QUANT])
def test_any_alias_still_rejects_disabled_experiment(requested, enough_memory):
    async def scenario():
        registry, _ = router([CANONICAL, OTHER_QUANT, QWEN38_MODEL])
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve(requested)
        assert caught.value.code == "MODEL_EXPERIMENT_DISABLED"
        assert (await registry.list())["preferred_model"] is None
        assert not any(policy["enabled"] for policy in registry.local_policies().values())

    asyncio.run(scenario())


@pytest.mark.parametrize("actual", [CANONICAL, OTHER_QUANT, LOCAL_QUANT])
def test_discovered_27b_is_never_usable_with_16gb_ram(monkeypatch, actual):
    monkeypatch.setattr(
        resources,
        "get_system_memory",
        lambda: {"available": True, "total_mib": 15477, "available_mib": 9000},
    )

    async def scenario():
        registry, _ = router([actual, "qwen3.5:4b"], qwen38_enabled=True)
        listed = await registry.list()
        assert listed["preferred_model"] == "qwen3.5:4b"
        entry = next(item for item in listed["models"] if item["id"] == actual)
        assert entry["installed"] is True
        assert entry["available"] is False
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve(actual)
        assert caught.value.code == "MODEL_RAM_INSUFFICIENT"

    asyncio.run(scenario())


def test_custom_hf_policy_has_priority_after_canonical(enough_memory):
    async def scenario():
        registry, _ = router(
            [QWEN38_MODEL, OTHER_QUANT], qwen38_enabled=True, qwen38_model=OTHER_QUANT
        )
        assert (await registry.list())["preferred_model"] == OTHER_QUANT

    asyncio.run(scenario())


def test_unregistered_local_models_are_visible_but_not_selectable():
    async def scenario():
        registry, _ = router(["other:120b", "qwen3.5:4b"])
        listed = await registry.list()
        assert listed["discovered_models"] == [
            {
                "id": "other:120b",
                "selectable": False,
                "reason": "尚未配置资源策略，不允许自动加载",
            }
        ]
        assert "other:120b" not in registry.local_policies()
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve("other:120b")
        assert caught.value.code == "MODEL_UNKNOWN"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name",
    [
        "qwen3.8:27b-cloud",
        "qwen3.8:cloud",
        "hf.co/unsloth/Qwen3.8-27B-GGUF:cloud",
        "hf.co/unsloth/Qwen3.8-27B-GGUF:Q3_K-cloud",
        "hf.co/other/Qwen3.8-27B-GGUF:UD-Q3_K_XL",
        "hf.co/unsloth/Qwen3.8-270B-GGUF:UD-Q3_K_XL",
    ],
)
def test_cloud_suffix_and_similarly_named_repository_never_become_aliases(name, enough_memory):
    async def scenario():
        registry, _ = router([name, CANONICAL], qwen38_enabled=True)
        await registry.refresh()
        assert name not in registry.local_policies()
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve(name)
        assert caught.value.code == "MODEL_UNKNOWN"

    asyncio.run(scenario())


def test_discovery_refresh_forgets_removed_models_and_failure_snapshots(enough_memory):
    async def scenario():
        registry, backend = router([CANONICAL], qwen38_enabled=True)
        assert (await registry.resolve(CANONICAL))["id"] == CANONICAL
        backend.names = ["qwen3.5:4b"]
        await registry.refresh()
        assert CANONICAL not in registry.entries
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve(CANONICAL)
        assert caught.value.code == "MODEL_NOT_INSTALLED"
        backend.failed = True
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve("qwen3.5:4b")
        assert caught.value.code == "OLLAMA_UNAVAILABLE"
        assert "PRIVATE" not in caught.value.public_message
        assert not registry.installed

    asyncio.run(scenario())


@pytest.mark.parametrize("names", [["invalid name"], [None], "qwen3.8:27b", ["a" * 129]])
def test_malformed_catalog_fails_closed(names):
    async def scenario():
        registry, backend = router()
        backend.names = names
        listed = await registry.list()
        assert not registry.reachable
        assert listed["preferred_model"] is None
        assert not listed["discovered_models"]

    asyncio.run(scenario())


def test_oversized_catalog_fails_closed_without_partial_snapshot(enough_memory):
    async def scenario():
        registry, backend = router([LOCAL_QUANT], qwen38_enabled=True)
        assert (await registry.refresh())["reachable"] is True
        backend.names = [f"qwen3.5:{index}" for index in range(4097)]
        listed = await registry.list()
        assert not registry.reachable
        assert not registry.installed
        assert listed["preferred_model"] is None
        assert listed["discovered_models"] == []

    asyncio.run(scenario())


def test_local_service_failure_does_not_disable_explicit_cloud_registration():
    async def scenario():
        registry, backend = router()
        backend.failed = True
        assert (await registry.resolve("deepseek"))["provider"] == "deepseek"
        assert not registry.cloud_ready()
        with pytest.raises(ApplicationError) as caught:
            registry.require_cloud(True)
        assert caught.value.code == "CLOUD_DISABLED"

    asyncio.run(scenario())


def test_fallback_configuration_does_not_implicitly_route_or_download():
    async def scenario():
        registry, _ = router(["qwen3.5:4b"], chat_fallback_model="qwen3.5:4b")
        with pytest.raises(ApplicationError) as caught:
            await registry.resolve("qwen3.5:9b")
        assert caught.value.code == "MODEL_NOT_INSTALLED"
        assert registry.settings.chat_fallback_model == "qwen3.5:4b"

    asyncio.run(scenario())


def test_new_typed_config_defaults_and_environment(monkeypatch, tmp_path):
    assert Settings().chat_think is False
    assert Settings().chat_fallback_model == ""
    path = tmp_path / "router.env"
    path.write_text("NOVA_CHAT_THINK=true\nNOVA_CHAT_FALLBACK_MODEL=qwen3.5:4b\n", encoding="utf-8")
    assert load_settings(path).chat_think is True
    assert load_settings(path).chat_fallback_model == "qwen3.5:4b"
    monkeypatch.setenv("NOVA_CHAT_THINK", "false")
    assert load_settings(path).chat_think is False


@pytest.mark.parametrize("name", ["deepseek", CANONICAL, "other:model", None, 4])
def test_fallback_is_only_an_explicit_stable_local_model(name):
    with pytest.raises(ValidationError):
        Settings(chat_fallback_model=name)


@pytest.mark.parametrize("value", ["private-invalid", "3", ""])
def test_invalid_think_config_has_safe_field_hint(tmp_path, value):
    path = tmp_path / "router.env"
    path.write_text(f"NOVA_CHAT_THINK={value}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        load_settings(path)
    assert "NOVA_CHAT_THINK" in str(caught.value)
    assert "private-invalid" not in str(caught.value)
