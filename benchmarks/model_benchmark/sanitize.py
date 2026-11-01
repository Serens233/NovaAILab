"""Redact local paths and hardware identifiers before results are written.

Committed results are meant to be shareable.  The runner therefore never stores
an absolute path, the Python executable path or the GPU UUID: configuration
paths become repository-relative (or basename-only), and the GPU UUID and
``python_executable`` are removed entirely.
"""

from __future__ import annotations

import copy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PATH_KEYS = ("cases_path", "output_dir", "nova_state_path")
_LEASE_KEYS = ("granted_by", "expires_at", "models", "max_seconds", "remaining_s")


def display_path(value: object) -> object:
    if value is None or not isinstance(value, (str, Path)):
        return value
    text = str(value)
    if not text:
        return text
    path = Path(text)
    try:
        relative = path.resolve().relative_to(REPO_ROOT)
    except OSError, ValueError:
        return f"<external>/{path.name}" if path.name else "<external>"
    return relative.as_posix()


def sanitize_config(config: object) -> object:
    if not isinstance(config, dict):
        return config
    data = copy.deepcopy(config)
    for key in _PATH_KEYS:
        if key in data:
            data[key] = display_path(data[key])
    if data.get("lease_path"):
        data["lease_path"] = f"<lease>/{Path(str(data['lease_path'])).name}"
    return data


def sanitize_environment(environment: object) -> object:
    if not isinstance(environment, dict):
        return environment
    data = copy.deepcopy(environment)
    os_info = data.get("os")
    if isinstance(os_info, dict):
        os_info.pop("python_executable", None)
    gpu = data.get("gpu")
    if isinstance(gpu, dict):
        gpu.pop("uuid", None)
    return data


def sanitize_lease(lease: object) -> object:
    if not isinstance(lease, dict):
        return lease
    return {key: lease[key] for key in _LEASE_KEYS if key in lease}


def sanitize_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    data = copy.deepcopy(payload)
    data["environment"] = sanitize_environment(data.get("environment"))
    data["config"] = sanitize_config(data.get("config"))
    preflight = data.get("preflight")
    if isinstance(preflight, dict):
        preflight["lease"] = sanitize_lease(preflight.get("lease"))
        checks = preflight.get("checks")
        if isinstance(checks, list):
            for check in checks:
                if isinstance(check, dict) and check.get("name") == "gpu_lease":
                    check["detail"] = sanitize_lease(check.get("detail"))
    return data
