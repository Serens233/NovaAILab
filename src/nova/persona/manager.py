"""受版本控制的只读人格加载器；审批生效通过代码审查及重启完成。"""

import hashlib
import json
import re
import threading
from datetime import date, datetime
from pathlib import Path

from nova.persona.models import Canon, EvolvablePersona, PersonaSnapshot, PersonaTrait, SourceRecord
from nova.persona.state import DynamicStateStore

TRUSTED_MANIFEST_SHA256 = "e347649e918c5600fc9476bbc0e7f8b39f5a8b0c6742ff544ff9165838ecff4c"
_BUNDLE_DIR = Path(__file__).with_name("data")
_FILES = {"canon-v1.json", "persona-v1.json"}


class PersonaConfigError(ValueError):
    def __init__(self):
        super().__init__("人格配置不可用：请检查已审核版本、结构和完整性")


class PersonaPermissionError(PermissionError):
    def __init__(self):
        super().__init__("人格配置为只读；变更须由项目负责人审核版本并部署")


def _keys(item, expected):
    if not isinstance(item, dict) or set(item) != set(expected):
        raise PersonaConfigError()


def _text(value, maximum=500):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(c) < 32 for c in value)
    ):
        raise PersonaConfigError()
    return value


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"1\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})", value
    ):
        raise PersonaConfigError()
    return value


def _schema(value):
    if type(value) is not int or value != 1:
        raise PersonaConfigError()
    return value


def _date(value):
    _text(value, 40)
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError
        else:
            date.fromisoformat(value)
    except ValueError:
        raise PersonaConfigError() from None
    return value


def _source(value):
    _keys(value, ("kind", "reference", "approved_by", "approved_at"))
    if value["kind"] != "project_owner" or value["approved_by"] != "project_owner":
        raise PersonaConfigError()
    return SourceRecord(
        value["kind"],
        _text(value["reference"], 300),
        value["approved_by"],
        _date(value["approved_at"]),
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PersonaConfigError()
        result[key] = value
    return result


def _read(path, *, maximum=65536):
    try:
        if not path.is_file():
            raise PersonaConfigError()
        with path.open("rb") as file:
            raw = file.read(maximum + 1)
        if len(raw) > maximum:
            raise PersonaConfigError()
        return raw
    except OSError:
        raise PersonaConfigError() from None


def _decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs)
    except UnicodeError, ValueError, RecursionError:
        raise PersonaConfigError() from None


def _canon(value):
    _keys(value, ("schema_version", "version", "name", "identity", "ai_identity", "source"))
    return Canon(
        _schema(value["schema_version"]),
        _version(value["version"]),
        _text(value["name"], 40),
        _text(value["identity"], 120),
        _text(value["ai_identity"], 200),
        _source(value["source"]),
    )


def _persona(value, canon):
    _keys(value, ("schema_version", "version", "canon_version", "traits"))
    if value["canon_version"] != canon.version or not isinstance(value["traits"], list):
        raise PersonaConfigError()
    if len(value["traits"]) > 32:
        raise PersonaConfigError()
    approved, identifiers = [], set()
    for item in value["traits"]:
        _keys(item, ("id", "value", "version", "source", "updated_at", "review_status", "reason"))
        identifier = item["id"]
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", identifier)
            or identifier in identifiers
            or not isinstance(item["review_status"], str)
            or item["review_status"] not in {"approved", "pending", "rejected"}
        ):
            raise PersonaConfigError()
        identifiers.add(identifier)
        trait = PersonaTrait(
            identifier,
            _text(item["value"], 200),
            _version(item["version"]),
            _source(item["source"]),
            _date(item["updated_at"]),
            item["review_status"],
            _text(item["reason"], 300),
        )
        if trait.review_status == "approved":
            approved.append(trait)
    return EvolvablePersona(
        _schema(value["schema_version"]),
        _version(value["version"]),
        value["canon_version"],
        tuple(approved),
    )


def load_snapshot(bundle_dir):
    """manifest本身固定在代码中；单改JSON或JSON+manifest都不会获得运行授权。"""
    raw_manifest = _read(bundle_dir / "manifest.json", maximum=4096)
    digest = hashlib.sha256(raw_manifest).hexdigest()
    if digest != TRUSTED_MANIFEST_SHA256:
        raise PersonaConfigError()
    manifest = _decode(raw_manifest)
    _keys(manifest, ("schema_version", "bundle_version", "files"))
    _schema(manifest["schema_version"])
    _version(manifest["bundle_version"])
    if not isinstance(manifest["files"], dict) or set(manifest["files"]) != _FILES:
        raise PersonaConfigError()
    documents = {}
    for filename, expected in manifest["files"].items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise PersonaConfigError()
        raw = _read(bundle_dir / filename)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise PersonaConfigError()
        documents[filename] = _decode(raw)
    canon = _canon(documents["canon-v1.json"])
    persona = _persona(documents["persona-v1.json"], canon)
    return PersonaSnapshot(canon, persona, digest)


class PersonaManager:
    """没有运行时写入API；源控审核、版本变更及部署构成唯一持久生效路径。"""

    def __init__(self, bundle_dir=None, *, dynamic_ttl_seconds=900, max_dynamic_sessions=256):
        self._bundle_dir = Path(bundle_dir) if bundle_dir is not None else _BUNDLE_DIR
        self._lock = threading.RLock()
        self.dynamic = DynamicStateStore(
            ttl_seconds=dynamic_ttl_seconds, max_sessions=max_dynamic_sessions
        )
        self._snapshot = load_snapshot(self._bundle_dir)

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def reload(self):
        # 完整构建成功后才原子发布；错误不能把最后一个合法快照替换成半成品。
        candidate = load_snapshot(self._bundle_dir)
        with self._lock:
            self._snapshot = candidate
        return candidate

    def update_canon(self, *args, **kwargs):
        raise PersonaPermissionError()

    def apply_proposal(self, *args, **kwargs):
        raise PersonaPermissionError()
