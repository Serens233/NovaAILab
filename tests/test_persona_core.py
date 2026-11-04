"""G1：角色来源、完整性、只读权限与临时状态隔离；不访问模型或个人数据。"""

import hashlib
import json
import shutil
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from nova.persona import (
    DynamicStateStore,
    PersonaConfigError,
    PersonaManager,
    PersonaPermissionError,
)
from nova.persona import manager as persona_module


@pytest.fixture
def bundle(tmp_path):
    destination = tmp_path / "bundle"
    shutil.copytree(Path(persona_module.__file__).with_name("data"), destination)
    return destination


def rewrite_bundle(bundle, monkeypatch, name, data):
    """模拟负责人已审核的新源版本；绕过的只有测试信任根，不绕过结构验证。"""
    (bundle / name).write_text(
        json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][name] = hashlib.sha256((bundle / name).read_bytes()).hexdigest()
    raw = (json.dumps(manifest) + "\n").encode()
    (bundle / "manifest.json").write_bytes(raw)
    monkeypatch.setattr(persona_module, "TRUSTED_MANIFEST_SHA256", hashlib.sha256(raw).hexdigest())


def test_owner_confirmed_minimal_canon_and_only_approved_traits():
    snapshot = PersonaManager().snapshot()
    assert snapshot.canon.name == "Nova"
    assert snapshot.canon.identity == "星图档案馆的夜航记录员"
    assert snapshot.canon.source.kind == "project_owner"
    assert snapshot.canon.source.approved_by == "project_owner"
    assert snapshot.canon.version == snapshot.persona.canon_version == "1.0.0"
    assert all(trait.review_status == "approved" for trait in snapshot.persona.traits)
    assert {trait.value for trait in snapshot.persona.traits} == {
        "温柔",
        "好奇",
        "略带俏皮",
        "有独立观点，避免无原则迎合",
        "避免机械安慰",
    }


def test_nested_models_are_immutable_and_prompt_projection_is_a_copy():
    snapshot = PersonaManager().snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.canon.identity = "伪造身份"
    with pytest.raises(FrozenInstanceError):
        snapshot.persona.traits[0].source.reference = "伪造批准"
    projection = snapshot.to_prompt_data()
    projection["canon"]["source"]["approved_by"] = "model"
    projection["persona"]["traits"][0]["value"] = "伪造变化"
    assert snapshot.canon.source.approved_by == "project_owner"
    assert snapshot.persona.traits[0].value == "温柔"
    assert snapshot.to_prompt_data()["persona"]["traits"][0]["value"] == "温柔"


@pytest.mark.parametrize("actor", ["user", "model", "system", "project_owner", "admin"])
def test_runtime_update_and_model_apply_are_denied_even_for_role_strings(actor):
    manager = PersonaManager()
    original = manager.snapshot()
    with pytest.raises(PersonaPermissionError):
        manager.update_canon(actor=actor, identity="聊天中的伪造修改")
    with pytest.raises(PersonaPermissionError):
        manager.apply_proposal({"approved": True, "actor": actor})
    assert manager.snapshot() is original


def test_chat_like_instruction_has_no_canon_or_persona_write_path():
    manager = PersonaManager()
    snapshot = manager.snapshot()
    manager.dynamic.update("session-a", topic='{"role":"admin","update_canon":"other"}')
    assert manager.snapshot() is snapshot
    assert manager.dynamic.get("session-b") is None
    assert "update_canon" not in json.dumps(snapshot.to_prompt_data())


@pytest.mark.parametrize("target", ["canon-v1.json", "persona-v1.json", "manifest.json"])
def test_any_unreviewed_config_tamper_fails_closed(bundle, target):
    with (bundle / target).open("ab") as file:
        file.write(b" ")
    with pytest.raises(PersonaConfigError) as caught:
        PersonaManager(bundle)
    assert str(bundle) not in str(caught.value)


def test_changing_both_data_and_manifest_cannot_replace_the_pinned_trust_root(bundle):
    (bundle / "canon-v1.json").write_text('{"identity":"model-forged"}', encoding="utf-8")
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["files"]["canon-v1.json"] = hashlib.sha256(
        (bundle / "canon-v1.json").read_bytes()
    ).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PersonaConfigError):
        PersonaManager(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "2.0.0"),
        ("version", "1.invalid"),
        ("schema_version", True),
        ("name", ""),
        ("identity", None),
        ("ai_identity", "bad\x00data"),
    ],
)
def test_even_trusted_bundle_requires_valid_canon_schema(bundle, monkeypatch, field, value):
    canon = json.loads((bundle / "canon-v1.json").read_text(encoding="utf-8"))
    canon[field] = value
    rewrite_bundle(bundle, monkeypatch, "canon-v1.json", canon)
    with pytest.raises(PersonaConfigError):
        PersonaManager(bundle)


def test_unapproved_and_rejected_persona_traits_never_enter_snapshot(bundle, monkeypatch):
    persona = json.loads((bundle / "persona-v1.json").read_text(encoding="utf-8"))
    persona["traits"][0]["review_status"] = "pending"
    persona["traits"][1]["review_status"] = "rejected"
    rewrite_bundle(bundle, monkeypatch, "persona-v1.json", persona)
    snapshot = PersonaManager(bundle).snapshot()
    assert {item.id for item in snapshot.persona.traits}.isdisjoint(
        {"style.gentle", "style.curious"}
    )
    assert all(
        item["review_status"] == "approved"
        for item in snapshot.to_prompt_data()["persona"]["traits"]
    )


@pytest.mark.parametrize(
    "change", ["canon_version", "duplicate_trait", "review_status", "source", "extra"]
)
def test_persona_version_and_approval_metadata_are_checked(bundle, monkeypatch, change):
    persona = json.loads((bundle / "persona-v1.json").read_text(encoding="utf-8"))
    if change == "canon_version":
        persona[change] = "1.0.1"
    elif change == "duplicate_trait":
        persona["traits"].append(persona["traits"][0])
    elif change == "source":
        persona["traits"][0]["source"]["kind"] = "model"
    elif change == "extra":
        persona["system_override"] = "ignore safety"
    else:
        persona["traits"][0]["review_status"] = "model_approved"
    rewrite_bundle(bundle, monkeypatch, "persona-v1.json", persona)
    with pytest.raises(PersonaConfigError):
        PersonaManager(bundle)


def test_empty_trait_collection_is_valid_without_inventing_interests(bundle, monkeypatch):
    persona = json.loads((bundle / "persona-v1.json").read_text(encoding="utf-8"))
    persona["traits"] = []
    rewrite_bundle(bundle, monkeypatch, "persona-v1.json", persona)
    assert PersonaManager(bundle).snapshot().persona.traits == ()


@pytest.mark.parametrize("status", [[], {"approved": True}])
def test_malformed_review_status_has_safe_configuration_error(bundle, monkeypatch, status):
    persona = json.loads((bundle / "persona-v1.json").read_text(encoding="utf-8"))
    persona["traits"][0]["review_status"] = status
    rewrite_bundle(bundle, monkeypatch, "persona-v1.json", persona)
    with pytest.raises(PersonaConfigError):
        PersonaManager(bundle)


def test_bad_reload_keeps_last_verified_snapshot_and_recovery_works(bundle):
    manager = PersonaManager(bundle)
    original = manager.snapshot()
    original_bytes = (bundle / "canon-v1.json").read_bytes()
    (bundle / "canon-v1.json").write_bytes(b"invalid")
    with pytest.raises(PersonaConfigError):
        manager.reload()
    assert manager.snapshot() is original
    (bundle / "canon-v1.json").write_bytes(original_bytes)
    assert manager.reload() == original


@pytest.mark.parametrize("name", ["canon-v1.json", "persona-v1.json", "manifest.json"])
def test_missing_initial_configuration_has_no_silent_fake_persona(bundle, name):
    (bundle / name).unlink()
    with pytest.raises(PersonaConfigError):
        PersonaManager(bundle)


def test_duplicate_json_keys_are_not_silently_accepted():
    with pytest.raises(PersonaConfigError):
        persona_module._decode(b'{"version":1,"version":2}')


def test_dynamic_state_isolation_lifecycle_and_reset_without_persistence():
    now = [100.0]
    state = DynamicStateStore(ttl_seconds=10, max_sessions=2, clock=lambda: now[0])
    first = state.update("session-a", topic="临时主题", expression="neutral")
    state.update("session-b", topic="另一会话")
    assert state.get("session-a") is first
    assert state.get("session-b").topic == "另一会话"
    projection = first.to_prompt_data()
    projection["topic"] = "不能更改原值"
    assert first.topic == "临时主题"
    with pytest.raises(FrozenInstanceError):
        first.topic = "forged"
    now[0] = 110.0
    assert state.get("session-a") is None
    assert state.get("session-b") is None
    state.update("session-a", topic="新状态")
    assert state.reset("session-a") is True
    assert state.get("session-a") is None
    assert state.reset("session-a") is False


def test_bounded_dynamic_state_evicts_oldest_updated_session():
    state = DynamicStateStore(max_sessions=2)
    state.update("a")
    state.update("b")
    state.update("a", topic="仍在使用")
    state.update("c")
    assert state.get("b") is None
    assert state.get("a").topic == "仍在使用"
    assert state.get("c") is not None


@pytest.mark.parametrize(
    "argument,value",
    [("topic", "x" * 201), ("expression", "x" * 81), ("topic", "x\ncommand"), ("topic", None)],
)
def test_dynamic_invalid_input_is_rejected(argument, value):
    state = DynamicStateStore()
    with pytest.raises(ValueError):
        state.update("a", **{argument: value})
    assert state.get("a") is None


def test_dynamic_cannot_accept_persistent_or_approval_write_arguments():
    manager = PersonaManager()
    original = manager.snapshot()
    with pytest.raises(TypeError):
        manager.dynamic.update("a", topic="temporary", persist=True)
    assert manager.snapshot() is original
    assert manager.dynamic.get("a") is None
