"""独立 Win32 只读采集：进程身份、前台几何与渲染模块；不读窗口标题/命令行。"""

import ctypes as c
import sys
import time
from ctypes import wintypes as w

RENDER_DLLS = frozenset(
    {
        "d3d9.dll",
        "d3d10.dll",
        "d3d10_1.dll",
        "d3d11.dll",
        "d3d12.dll",
        "dxgi.dll",
        "opengl32.dll",
        "vulkan-1.dll",
    }
)


def window_features(rect, monitor, style, *, visible=True, minimized=False):
    """只按同一 DPI 坐标空间比较窗口与所在显示器，不能据此单独判游戏。"""
    fills = bool(rect and monitor) and all(
        abs(a - b) <= 2 for a, b in zip(rect, monitor, strict=True)
    )
    fills = bool(fills and visible and not minimized)
    return {
        "fullscreen": fills,
        "borderless": fills and not bool(style & 0x00C00000),
        "visible": bool(visible),
        "minimized": bool(minimized),
    }


class _ProcessEntry(c.Structure):
    _fields_ = [
        ("dwSize", w.DWORD),
        ("cntUsage", w.DWORD),
        ("pid", w.DWORD),
        ("heap", c.c_size_t),
        ("module", w.DWORD),
        ("threads", w.DWORD),
        ("ppid", w.DWORD),
        ("priority", w.LONG),
        ("flags", w.DWORD),
        ("name", w.WCHAR * 260),
    ]


class _MonitorInfo(c.Structure):
    _fields_ = [("cbSize", w.DWORD), ("monitor", w.RECT), ("work", w.RECT), ("flags", w.DWORD)]


def _rect_tuple(rect):
    return (rect.left, rect.top, rect.right, rect.bottom)


class WindowsProcessProbe:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("WINDOWS_ONLY")
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.user = c.WinDLL("user32", use_last_error=True)
        self.psapi = c.WinDLL("psapi", use_last_error=True)
        self.dwm = c.WinDLL("dwmapi", use_last_error=True)
        signatures = [
            (self.kernel, "CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE),
            (self.kernel, "Process32FirstW", [w.HANDLE, c.POINTER(_ProcessEntry)], w.BOOL),
            (self.kernel, "Process32NextW", [w.HANDLE, c.POINTER(_ProcessEntry)], w.BOOL),
            (self.kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (self.kernel, "CloseHandle", [w.HANDLE], w.BOOL),
            (
                self.kernel,
                "QueryFullProcessImageNameW",
                [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)],
                w.BOOL,
            ),
            (
                self.kernel,
                "GetProcessTimes",
                [
                    w.HANDLE,
                    c.POINTER(w.FILETIME),
                    c.POINTER(w.FILETIME),
                    c.POINTER(w.FILETIME),
                    c.POINTER(w.FILETIME),
                ],
                w.BOOL,
            ),
            (self.user, "GetForegroundWindow", [], w.HWND),
            (self.user, "GetWindowThreadProcessId", [w.HWND, c.POINTER(w.DWORD)], w.DWORD),
            (self.user, "GetWindowRect", [w.HWND, c.POINTER(w.RECT)], w.BOOL),
            (self.user, "IsWindowVisible", [w.HWND], w.BOOL),
            (self.user, "IsIconic", [w.HWND], w.BOOL),
            (self.user, "MonitorFromWindow", [w.HWND, w.DWORD], w.HANDLE),
            (self.user, "GetMonitorInfoW", [w.HANDLE, c.POINTER(_MonitorInfo)], w.BOOL),
            (self.user, "GetWindowLongPtrW", [w.HWND, c.c_int], c.c_ssize_t),
            (self.user, "SetThreadDpiAwarenessContext", [c.c_void_p], c.c_void_p),
            (self.dwm, "DwmGetWindowAttribute", [w.HWND, w.DWORD, c.c_void_p, w.DWORD], c.c_long),
            (
                self.psapi,
                "EnumProcessModulesEx",
                [w.HANDLE, c.POINTER(c.c_void_p), w.DWORD, c.POINTER(w.DWORD), w.DWORD],
                w.BOOL,
            ),
            (self.psapi, "GetModuleBaseNameW", [w.HANDLE, c.c_void_p, w.LPWSTR, w.DWORD], w.DWORD),
        ]
        for dll, name, args, result in signatures:
            function = getattr(dll, name)
            function.argtypes, function.restype = args, result

    def _identity(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None, None
        try:
            path = c.create_unicode_buffer(32768)
            length = w.DWORD(len(path))
            full_path = (
                path.value
                if self.kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(length))
                else None
            )
            creation, exit_time, kernel, user = (w.FILETIME() for _ in range(4))
            created = None
            if self.kernel.GetProcessTimes(
                handle, c.byref(creation), c.byref(exit_time), c.byref(kernel), c.byref(user)
            ):
                created = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return full_path, created
        finally:
            self.kernel.CloseHandle(handle)

    def _render_api(self, pid):
        handle = self.kernel.OpenProcess(0x410, False, pid)
        if not handle:
            return None
        try:
            modules = (c.c_void_p * 2048)()
            needed = w.DWORD()
            if not self.psapi.EnumProcessModulesEx(
                handle, modules, c.sizeof(modules), c.byref(needed), 3
            ):
                return None
            if needed.value > c.sizeof(modules):
                return None
            for module in modules[: needed.value // c.sizeof(c.c_void_p)]:
                name = c.create_unicode_buffer(260)
                if (
                    self.psapi.GetModuleBaseNameW(handle, module, name, len(name))
                    and name.value.casefold() in RENDER_DLLS
                ):
                    return True
            return False
        finally:
            self.kernel.CloseHandle(handle)

    def _foreground(self):
        hwnd = self.user.GetForegroundWindow()
        empty = {
            "pid": None,
            "fullscreen": False,
            "borderless": False,
            "visible": False,
            "minimized": False,
            "available": False,
        }
        if not hwnd:
            return empty
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, c.byref(pid))
        # 只改变当前采集线程，恢复后不影响应用/用户的全局DPI配置。
        previous = self.user.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        try:
            rect = w.RECT()
            info = _MonitorInfo()
            info.cbSize = c.sizeof(info)
            monitor = self.user.MonitorFromWindow(hwnd, 2)
            if not self.user.GetWindowRect(hwnd, c.byref(rect)) or not self.user.GetMonitorInfoW(
                monitor, c.byref(info)
            ):
                return {**empty, "pid": pid.value}
            # 扩展边框不包含透明阴影，比GetWindowRect更适合无边框窗口。
            extended = w.RECT()
            if self.dwm.DwmGetWindowAttribute(hwnd, 9, c.byref(extended), c.sizeof(extended)) == 0:
                rect = extended
            style = self.user.GetWindowLongPtrW(hwnd, -16)
            return {
                "pid": pid.value,
                "available": True,
                **window_features(
                    _rect_tuple(rect),
                    _rect_tuple(info.monitor),
                    style,
                    visible=self.user.IsWindowVisible(hwnd),
                    minimized=self.user.IsIconic(hwnd),
                ),
            }
        finally:
            if previous:
                self.user.SetThreadDpiAwarenessContext(previous)

    def sample(self, catalog=None):
        started = time.perf_counter()
        foreground = self._foreground()
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot in {None, c.c_void_p(-1).value}:
            return {
                "available": False,
                "processes": [],
                "foreground": foreground,
                "errors": ["PROCESS_ENUMERATION_FAILED"],
            }
        processes = []
        errors = []
        try:
            entry = _ProcessEntry()
            entry.dwSize = c.sizeof(entry)
            present = self.kernel.Process32FirstW(snapshot, c.byref(entry))
            while present:
                if entry.pid:
                    path, created = self._identity(entry.pid)
                    item = {
                        "pid": entry.pid,
                        "ppid": entry.ppid,
                        "created": created,
                        "name": entry.name.casefold(),
                        "path": path,
                        "render_api": None,
                    }
                    candidate = bool(path and catalog and catalog.match(path))
                    if entry.pid == foreground.get("pid") or candidate:
                        item["render_api"] = self._render_api(entry.pid)
                    processes.append(item)
                present = self.kernel.Process32NextW(snapshot, c.byref(entry))
            if c.get_last_error() not in {0, 18}:
                errors.append("PROCESS_ENUMERATION_PARTIAL")
        finally:
            self.kernel.CloseHandle(snapshot)
        return {
            "available": bool(processes) and not errors,
            "processes": processes,
            "foreground": foreground,
            "errors": errors,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
