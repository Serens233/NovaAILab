"""只读 Windows 进程名称；不查看命令行、窗口标题或游戏安装目录。"""

import asyncio
import csv
import ctypes
import io
import os
import subprocess
import sys
from pathlib import PureWindowsPath

_INVALID_NAME_CHARACTERS = frozenset('<>:"/\\|?*')
_READ_ERROR = "无法可靠读取 Windows 进程列表，不能据此判定游戏已退出。"


class ProcessReadError(Exception):
    """固定公开错误，不包含进程列表或原始系统输出。"""


def normalize_game_names(value) -> list[str]:
    """白名单只接受 EXE 文件名；忽略大小写并保留首次配置的顺序。"""
    if not isinstance(value, list):
        raise ValueError("游戏白名单必须为 EXE 文件名列表。")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("游戏白名单仅接受 EXE 文件名，不接受路径或通配符。")
        name = item.strip().casefold()
        if (
            not 4 < len(name) <= 255
            or not name.endswith(".exe")
            or any(character in _INVALID_NAME_CHARACTERS for character in name)
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise ValueError("游戏白名单仅接受 EXE 文件名，不接受路径或通配符。")
        if name not in result:
            result.append(name)
    return result


def _oem_encoding():
    # tasklist 的重定向输出使用 Windows OEM 代码页，不能直接假定为 UTF-8。
    return f"cp{ctypes.windll.kernel32.GetOEMCP()}"


def _decode_output(raw):
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("output")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    if b"\x00" in raw:
        return raw.decode("utf-16-le")
    return raw.decode(_oem_encoding())


def _read_process_names():
    try:
        if sys.platform != "win32":
            raise ProcessReadError(_READ_ERROR)
        system_root = PureWindowsPath(os.environ["SystemRoot"])
        if not system_root.is_absolute():
            raise ValueError("system_root")
        process = subprocess.run(
            [str(system_root / "System32" / "tasklist.exe"), "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.returncode != 0:
            raise ProcessReadError(_READ_ERROR)
        names = set()
        rows = csv.reader(io.StringIO(_decode_output(process.stdout)), strict=True)
        for row in rows:
            if not row:
                continue
            if len(row) != 5:
                raise ValueError("csv_shape")
            image, pid = row[0].strip(), row[1].strip()
            if not pid.isascii() or not pid.isdecimal():
                raise ValueError("pid")
            if image.casefold().endswith(".exe"):
                if int(pid) <= 0:
                    raise ValueError("pid")
                names.update(normalize_game_names([image]))
            # System、Registry 等系统特殊名称不是 EXE；不作为游戏名称。
        if not names:
            raise ProcessReadError(_READ_ERROR)
        return names
    except OSError, subprocess.SubprocessError, ValueError, csv.Error, LookupError, AttributeError:
        raise ProcessReadError(_READ_ERROR) from None


async def list_process_names() -> set[str]:
    """异步调用有界 tasklist；读取失败不会冒充“没有游戏”。"""
    return await asyncio.to_thread(_read_process_names)
