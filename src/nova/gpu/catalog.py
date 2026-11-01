"""只读游戏安装元数据索引；不枚举游戏目录中的程序，不启动或下载游戏。"""

import json
import os
import re
import sys
from pathlib import Path

_MAX_BYTES = 1024 * 1024
_MAX_FILES = 512
_MAX_ROOTS = 32
_MAX_REGISTRY_ENTRIES = 2048
_TOKENS = re.compile(r'\s+|//[^\r\n]*|"(?:\\.|[^"\\])*"|[{}]')
_TOOL_DIRS = frozenset(
    {"redist", "_commonredist", "directx", "support", "installer", "installers", "tools", "sdk"}
)


def _local_path(value):
    """拒绝网络/设备路径和父级跳转；只在已知元数据指向的位置操作。"""
    if not isinstance(value, (str, os.PathLike)):
        return None
    text = os.fspath(value)
    if (
        not isinstance(text, str)
        or not text
        or any(ord(char) < 32 for char in text)
        or any(char in text for char in '<>"|?*')
        or ":" in text[2:]
        or text.startswith(("\\\\", "//"))
    ):
        return None
    if ".." in text.replace("\\", "/").split("/"):
        return None
    path = Path(text)
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve()
        if str(resolved).startswith(("\\\\", "//")):
            return None
        return resolved
    except OSError, ValueError, RuntimeError:
        return None


def _key(path):
    return path.as_posix().rstrip("/").casefold()


def _safe_relative(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        return None
    if any(ord(char) < 32 for char in value) or any(char in value for char in ':*?"<>|'):
        return None
    text = value.replace("\\", "/")
    if text.startswith("/") or any(part in {"", ".", ".."} for part in text.split("/")):
        return None
    return Path(*text.split("/"))


def _read_text(path):
    with path.open("rb") as source:
        content = source.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise ValueError("manifest_too_large")
    return content.decode("utf-8-sig")


def _parse_vdf(text):
    """独立实现所需的带引号KV子集；限制嵌套，损坏输入不能静默部分通过。"""
    tokens = []
    position = 0
    while position < len(text):
        match = _TOKENS.match(text, position)
        if not match:
            raise ValueError("invalid_vdf")
        token = match.group()
        position = match.end()
        if token.isspace() or token.startswith("//"):
            continue
        tokens.append(token)
        if len(tokens) > 100000:
            raise ValueError("vdf_token_limit")

    def string(token):
        if not token.startswith('"'):
            raise ValueError("invalid_vdf_string")
        return re.sub(r'\\([\\"])', r"\1", token[1:-1])

    def pairs(index, depth):
        if depth > 16:
            raise ValueError("vdf_depth_limit")
        result = {}
        while index < len(tokens):
            if tokens[index] == "}":
                if depth == 0:
                    raise ValueError("unexpected_vdf_close")
                return result, index + 1
            name = string(tokens[index])
            index += 1
            if index == len(tokens):
                raise ValueError("missing_vdf_value")
            if tokens[index] == "{":
                value, index = pairs(index + 1, depth + 1)
            else:
                value = string(tokens[index])
                index += 1
            result[name.casefold()] = value
        if depth:
            raise ValueError("unclosed_vdf_object")
        return result, index

    return pairs(0, 0)[0]


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        return "Unknown"
    if any(ord(char) < 32 for char in value):
        return "Unknown"
    return value.strip()


def _category(title, install_name=""):
    text = (title + " " + install_name).casefold()
    # 这些是工具/服务类别的否定信号，不是需要维护的游戏EXE名单。
    markers = ("wallpaper", "redistributable", "steamworks shared", "dedicated server", " sdk")
    if any(marker in text for marker in markers):
        return "tool"
    if re.search(r"\b(?:tools?|servers?)\b", title.casefold()):
        return "tool"
    return "game"


class GameCatalog:
    """刷新只访问注册表/manifest，match仅返回证据，不单独决定是否正在游戏。

    registry_roots 的注入值为已发现的 MatchedExeFullPath 样本列表；None 才访问注册表。
    steam_paths 为 Steam 安装根或 SteamLibrary 根；epic_manifest_dirs 为 .item 目录。
    测试传空列表即可禁用各自的真实发现。summary 不包含本机路径或账号。
    """

    def __init__(self, *, registry_roots=None, steam_paths=None, epic_manifest_dirs=None):
        self.registry_roots = registry_roots
        self.steam_paths = steam_paths
        self.epic_manifest_dirs = epic_manifest_dirs
        self._exact = {}
        self._roots = []
        self._errors = set()
        self._counts = {}
        self._summary = {}

    def _error(self, source, code):
        self._errors.add((source, code))

    def _registry_values(self, source):
        if sys.platform != "win32":
            self._error(source, "WINDOWS_REGISTRY_UNAVAILABLE")
            return []
        import winreg

        result = []
        if source == "gameconfig":
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, r"System\GameConfigStore\Children"
                ) as root:
                    count = min(winreg.QueryInfoKey(root)[0], _MAX_REGISTRY_ENTRIES)
                    if winreg.QueryInfoKey(root)[0] > count:
                        self._error(source, "ENTRY_LIMIT")
                    for index in range(count):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as child:
                                result.append(winreg.QueryValueEx(child, "MatchedExeFullPath")[0])
                        except FileNotFoundError:
                            continue
                        except OSError:
                            self._error(source, "REGISTRY_READ_FAILED")
            except FileNotFoundError:
                pass
            except OSError:
                self._error(source, "REGISTRY_READ_FAILED")
            return result
        keys = (
            [
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            ]
            if source == "steam"
            else [
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Epic Games\EpicGamesLauncher",
                    "AppDataPath",
                ),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher",
                    "AppDataPath",
                ),
            ]
        )
        for hive, name, field in keys:
            try:
                with winreg.OpenKey(hive, name) as handle:
                    value = winreg.QueryValueEx(handle, field)[0]
                    if isinstance(value, str):
                        result.append(value)
                    else:
                        self._error(source, "REGISTRY_VALUE_INVALID")
            except FileNotFoundError:
                continue
            except OSError:
                self._error(source, "REGISTRY_READ_FAILED")
        return result

    def _add(self, path, source, identity, title, *, exact, category):
        info = {
            "source": source,
            "id": str(identity),
            "title": _title(title),
            "exact": exact,
            "category": category,
        }
        if exact:
            key = _key(path)
            previous = self._exact.get(key)
            if previous is None or category == "tool":
                self._exact[key] = info
        else:
            self._roots.append((_key(path), info))
        self._counts[source] += 1

    def _scan_gameconfig(self):
        values = (
            self.registry_roots
            if self.registry_roots is not None
            else self._registry_values("gameconfig")
        )
        for index, value in enumerate(values):
            if index >= _MAX_REGISTRY_ENTRIES:
                self._error("gameconfig", "ENTRY_LIMIT")
                break
            path = _local_path(value)
            if path is not None and path.suffix.casefold() == ".exe" and path.is_file():
                self._add(
                    path,
                    "gameconfig",
                    "windows",
                    path.stem,
                    exact=True,
                    category=_category(path.stem),
                )

    def _scan_steam(self):
        values = (
            self.steam_paths if self.steam_paths is not None else self._registry_values("steam")
        )
        libraries = {}
        for value in values:
            path = _local_path(value)
            if path is None or not path.is_dir():
                self._error("steam", "INVALID_LIBRARY_PATH")
                continue
            if len(libraries) >= _MAX_ROOTS and _key(path) not in libraries:
                self._error("steam", "LIBRARY_LIMIT")
                break
            libraries[_key(path)] = path
        for path in tuple(libraries.values()):
            manifest = path / "steamapps" / "libraryfolders.vdf"
            if not manifest.is_file():
                continue
            try:
                parsed = _parse_vdf(_read_text(manifest))
                if "libraryfolders" not in parsed:
                    raise ValueError("libraries")
                data = parsed["libraryfolders"]
                if not isinstance(data, dict):
                    raise ValueError("libraries")
                for name, entry in data.items():
                    if not name.isdecimal():
                        continue
                    value = entry.get("path") if isinstance(entry, dict) else entry
                    other = _local_path(value)
                    if other is None or not other.is_dir():
                        self._error("steam", "INVALID_LIBRARY_PATH")
                        continue
                    if len(libraries) >= _MAX_ROOTS and _key(other) not in libraries:
                        self._error("steam", "LIBRARY_LIMIT")
                        break
                    libraries[_key(other)] = other
            except OSError, ValueError, RecursionError:
                self._error("steam", "LIBRARY_MANIFEST_INVALID")
        seen = 0
        for path in list(libraries.values())[:_MAX_ROOTS]:
            try:
                for manifest in (path / "steamapps").glob("appmanifest_*.acf"):
                    seen += 1
                    if seen > _MAX_FILES:
                        self._error("steam", "MANIFEST_LIMIT")
                        return
                    try:
                        data = _parse_vdf(_read_text(manifest)).get("appstate", {})
                        identity = data.get("appid")
                        child = _safe_relative(data.get("installdir"))
                        if (
                            not isinstance(identity, str)
                            or not identity.isascii()
                            or not identity.isdecimal()
                            or child is None
                            or manifest.stem.casefold() != "appmanifest_" + identity
                        ):
                            raise ValueError("manifest")
                        common = (path / "steamapps" / "common").resolve()
                        root = (common / child).resolve()
                        if not root.is_relative_to(common) or not root.is_dir():
                            raise ValueError("installation")
                        title = _title(data.get("name"))
                        self._add(
                            root,
                            "steam",
                            identity,
                            title,
                            exact=False,
                            category=_category(title, child.name),
                        )
                    except OSError, ValueError, AttributeError, RecursionError:
                        self._error("steam", "GAME_MANIFEST_INVALID")
            except OSError:
                self._error("steam", "MANIFEST_DIRECTORY_UNREADABLE")

    def _scan_epic(self):
        values = self.epic_manifest_dirs
        if values is None:
            values = [str(Path(value) / "Manifests") for value in self._registry_values("epic")]
            if os.environ.get("ProgramData"):
                values.append(
                    str(
                        Path(os.environ["ProgramData"])
                        / "Epic"
                        / "EpicGamesLauncher"
                        / "Data"
                        / "Manifests"
                    )
                )
        roots = {}
        for value in values:
            path = _local_path(value)
            if path is not None and path.is_dir():
                if len(roots) >= _MAX_ROOTS and _key(path) not in roots:
                    self._error("epic", "DIRECTORY_LIMIT")
                    break
                roots[_key(path)] = path
        seen = 0
        for path in list(roots.values())[:_MAX_ROOTS]:
            try:
                for manifest in path.glob("*.item"):
                    seen += 1
                    if seen > _MAX_FILES:
                        self._error("epic", "MANIFEST_LIMIT")
                        return
                    try:
                        data = json.loads(_read_text(manifest))
                        root = _local_path(data.get("InstallLocation"))
                        child = _safe_relative(data.get("LaunchExecutable"))
                        if root is None or child is None:
                            raise ValueError("installation")
                        executable = (root / child).resolve()
                        if (
                            not executable.is_relative_to(root)
                            or not executable.is_file()
                            or executable.suffix.casefold() != ".exe"
                        ):
                            raise ValueError("executable")
                        title = _title(data.get("DisplayName"))
                        identity = _title(data.get("AppName"))
                        self._add(
                            executable,
                            "epic",
                            identity,
                            title,
                            exact=True,
                            category=_category(title),
                        )
                    except OSError, ValueError, AttributeError, RecursionError:
                        self._error("epic", "GAME_MANIFEST_INVALID")
            except OSError:
                self._error("epic", "MANIFEST_DIRECTORY_UNREADABLE")

    def refresh(self):
        self._exact = {}
        self._roots = []
        self._errors = set()
        self._counts = {"gameconfig": 0, "steam": 0, "epic": 0}
        self._scan_gameconfig()
        self._scan_steam()
        self._scan_epic()
        self._roots.sort(key=lambda item: len(item[0]), reverse=True)
        self._summary = {
            "counts": dict(self._counts),
            "exact_entries": len(self._exact),
            "installation_roots": len(self._roots),
            "tool_entries": sum(x["category"] == "tool" for x in self._exact.values())
            + sum(x["category"] == "tool" for _, x in self._roots),
            "errors": [{"source": source, "code": code} for source, code in sorted(self._errors)],
        }
        return self.summary

    @property
    def summary(self):
        """只暴露匿名聚合，调用方修改副本不会改变索引内部状态。"""
        if not self._summary:
            return {
                "counts": {},
                "exact_entries": 0,
                "installation_roots": 0,
                "tool_entries": 0,
                "errors": [],
            }
        return {
            **self._summary,
            "counts": dict(self._summary["counts"]),
            "errors": [dict(item) for item in self._summary["errors"]],
        }

    def match(self, value):
        path = _local_path(value)
        if path is None or path.suffix.casefold() != ".exe" or not path.is_file():
            return None
        key = _key(path)
        exact = self._exact.get(key)
        roots = [(root, info) for root, info in self._roots if key.startswith(root + "/")]
        candidates = ([exact] if exact else []) + [info for _, info in roots]
        if not candidates:
            return None
        chosen = next((info for info in candidates if info["category"] == "tool"), candidates[0])
        result = dict(chosen)
        relative_parts = set()
        for root, _ in roots:
            relative_parts.update(key[len(root) + 1 :].split("/")[:-1])
        stem = path.stem.casefold()
        if (
            relative_parts & _TOOL_DIRS
            or "launcher" in stem
            or stem.startswith(("unins", "uninstall", "crash", "vcredist", "dxsetup"))
        ):
            result["category"] = "tool"
        return result
