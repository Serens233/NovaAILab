"""27B实验模型标识的共享白名单；注册表和GPU管理器使用同一判定。"""

import re

QWEN38_CANONICAL = "qwen3.8:27b"
_LOCAL = re.compile(r"qwen3\.8:27b-[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z", re.IGNORECASE)
_QWEN38_GGUF = re.compile(
    r"hf\.co/unsloth/Qwen3\.8-27B-GGUF:[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.IGNORECASE
)


def is_qwen38_alias(name, configured_model=None):
    """只承认规范名、本机量化族、公开HF标识或显式配置的同一标识。"""
    if not isinstance(name, str) or not name or name.casefold().endswith((":cloud", "-cloud")):
        return False
    folded = name.casefold()
    if folded == QWEN38_CANONICAL:
        return True
    if _LOCAL.fullmatch(name) or _QWEN38_GGUF.fullmatch(name):
        return True
    return isinstance(configured_model, str) and folded == configured_model.casefold()
