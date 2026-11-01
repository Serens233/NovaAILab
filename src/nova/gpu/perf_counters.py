"""只读 PDH/DXGI 采样；不运行追踪服务，不将进程预算冒充整卡预算。"""

import ctypes
import math
import re
import sys
import threading
import time
import uuid
from ctypes import wintypes

_MORE_DATA = 0x800007D2
_FMT_DOUBLE = 0x200
_FMT_LARGE = 0x400
_MAX_BUFFER = 32 * 1024 * 1024
_ENGINE = re.compile(
    r"^pid_(\d+)_luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_phys_(\d+)_eng_(\d+)_engtype_(.+)$",
    re.IGNORECASE,
)
_PROCESS = re.compile(
    r"^pid_(\d+)_luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_phys_(\d+)(?:#\d+)?$",
    re.IGNORECASE,
)
_ADAPTER = re.compile(r"^luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_phys_(\d+)(?:#\d+)?$", re.IGNORECASE)
_COUNTERS = {
    "engine": (r"\GPU Engine(*)\Utilization Percentage", _FMT_DOUBLE),
    "process_memory": (r"\GPU Process Memory(*)\Dedicated Usage", _FMT_LARGE),
    "adapter_memory": (r"\GPU Adapter Memory(*)\Dedicated Usage", _FMT_LARGE),
}


class _NativeError(Exception):
    pass


class _Value(ctypes.Union):
    _fields_ = [("double", ctypes.c_double), ("large", ctypes.c_longlong)]


class _FormattedValue(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("value", _Value)]


class _Item(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("formatted", _FormattedValue)]


class _Guid(ctypes.Structure):
    _fields_ = [
        ("a", wintypes.DWORD),
        ("b", wintypes.WORD),
        ("c", wintypes.WORD),
        ("d", wintypes.BYTE * 8),
    ]


class _Luid(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]


class _AdapterDescription(ctypes.Structure):
    _fields_ = [
        ("description", wintypes.WCHAR * 128),
        ("vendor", wintypes.UINT),
        ("device", wintypes.UINT),
        ("subsystem", wintypes.UINT),
        ("revision", wintypes.UINT),
        ("dedicated_video", ctypes.c_size_t),
        ("dedicated_system", ctypes.c_size_t),
        ("shared_system", ctypes.c_size_t),
        ("luid", _Luid),
        ("flags", wintypes.UINT),
    ]


class _MemoryInfo(ctypes.Structure):
    _fields_ = [
        ("budget", ctypes.c_ulonglong),
        ("usage", ctypes.c_ulonglong),
        ("available_for_reservation", ctypes.c_ulonglong),
        ("current_reservation", ctypes.c_ulonglong),
    ]


def _guid(value):
    return _Guid.from_buffer_copy(uuid.UUID(value).bytes_le)


def _com_method(pointer, index, result, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])


def _canonical_luid(high, low):
    high, low = int(high, 16), int(low, 16)
    if not 0 <= high <= 0xFFFFFFFF or not 0 <= low <= 0xFFFFFFFF:
        raise ValueError("luid")
    return f"0x{high:08x}_0x{low:08x}"


def enumerate_dxgi_adapters():
    """官方 COM ABI，区分软件适配器；QueryVideoMemoryInfo 的主体始终是本进程。"""
    if sys.platform != "win32":
        raise _NativeError("DXGI_UNAVAILABLE")
    library = ctypes.WinDLL("dxgi.dll", winmode=0x00000800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
    create = library.CreateDXGIFactory1
    create.argtypes = [ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = wintypes.LONG
    factory = ctypes.c_void_p()
    identity = _guid("770aae78-f26f-4dba-a829-253c83d1b387")
    if create(ctypes.byref(identity), ctypes.byref(factory)) < 0:
        raise _NativeError("DXGI_UNAVAILABLE")
    result = []
    try:
        for index in range(64):
            adapter = ctypes.c_void_p()
            status = _com_method(
                factory, 12, wintypes.LONG, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)
            )(factory, index, ctypes.byref(adapter))
            if status & 0xFFFFFFFF == 0x887A0002:  # DXGI_ERROR_NOT_FOUND
                break
            if status < 0 or not adapter.value:
                raise _NativeError("DXGI_ENUMERATION_FAILED")
            try:
                desc = _AdapterDescription()
                status = _com_method(
                    adapter, 10, wintypes.LONG, ctypes.POINTER(_AdapterDescription)
                )(adapter, ctypes.byref(desc))
                if status < 0:
                    raise _NativeError("DXGI_DESCRIPTION_FAILED")
                item = {
                    "luid": f"0x{desc.luid.high & 0xFFFFFFFF:08x}_0x{desc.luid.low:08x}",
                    "vendor_id": int(desc.vendor),
                    "software": bool(desc.flags & 2),  # DXGI_ADAPTER_FLAG_SOFTWARE
                    "name": desc.description,
                    "total_dedicated_bytes": int(desc.dedicated_video),
                    "budget_bytes": None,
                    "probe_budget_bytes": None,
                    "probe_current_usage": None,
                    "budget_scope": "unknown",
                }
                adapter3 = ctypes.c_void_p()
                interface = _guid("645967a4-1392-4310-a798-8053ce3e93fd")
                status = _com_method(
                    adapter,
                    0,
                    wintypes.LONG,
                    ctypes.POINTER(_Guid),
                    ctypes.POINTER(ctypes.c_void_p),
                )(adapter, ctypes.byref(interface), ctypes.byref(adapter3))
                if status >= 0 and adapter3.value:
                    try:
                        memory = _MemoryInfo()
                        status = _com_method(
                            adapter3,
                            14,
                            wintypes.LONG,
                            wintypes.UINT,
                            wintypes.UINT,
                            ctypes.POINTER(_MemoryInfo),
                        )(adapter3, 0, 0, ctypes.byref(memory))
                        if status >= 0:
                            item.update(
                                probe_budget_bytes=int(memory.budget),
                                probe_current_usage=int(memory.usage),
                                budget_scope="current_process",
                            )
                    finally:
                        _com_method(adapter3, 2, wintypes.ULONG)(adapter3)
                result.append(item)
            finally:
                _com_method(adapter, 2, wintypes.ULONG)(adapter)
    finally:
        _com_method(factory, 2, wintypes.ULONG)(factory)
    if not any(not item["software"] for item in result):
        raise _NativeError("DXGI_NO_HARDWARE_ADAPTER")
    return result


class _PdhReader:
    """同一个 query 跨巡检保留，格式化速率取相邻 Collect 的差，不阻塞 sleep。"""

    def __init__(self):
        if sys.platform != "win32":
            raise _NativeError("PDH_UNAVAILABLE")
        self.library = ctypes.WinDLL("pdh.dll", winmode=0x00000800)
        handle = ctypes.c_void_p
        pointer = ctypes.POINTER(handle)
        signatures = {
            "PdhOpenQueryW": [wintypes.LPCWSTR, ctypes.c_size_t, pointer],
            "PdhAddEnglishCounterW": [handle, wintypes.LPCWSTR, ctypes.c_size_t, pointer],
            "PdhCollectQueryData": [handle],
            "PdhGetFormattedCounterArrayW": [
                handle,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD),
                ctypes.c_void_p,
            ],
            "PdhCloseQuery": [handle],
        }
        for name, arguments in signatures.items():
            function = getattr(self.library, name)
            function.argtypes = arguments
            function.restype = wintypes.LONG
        self.query = handle()
        self.handles = {}
        if self.library.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            raise _NativeError("PDH_QUERY_UNAVAILABLE")
        for key, (path, _) in _COUNTERS.items():
            counter = handle()
            if self.library.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter)) == 0:
                self.handles[key] = counter
        self.primed = False

    def _array(self, handle, format_flag):
        # 实例可在两次调用间出现/退出；MORE_DATA 时重新由零查询大小，最多三次。
        function = self.library.PdhGetFormattedCounterArrayW
        for _ in range(3):
            size, count = wintypes.DWORD(), wintypes.DWORD()
            status = function(handle, format_flag, ctypes.byref(size), ctypes.byref(count), None)
            if status == 0 and not count.value:
                return []
            if status & 0xFFFFFFFF != _MORE_DATA or not 0 < size.value <= _MAX_BUFFER:
                raise _NativeError("PDH_ARRAY_UNAVAILABLE")
            buffer = ctypes.create_string_buffer(size.value)
            capacity = size.value
            status = function(handle, format_flag, ctypes.byref(size), ctypes.byref(count), buffer)
            if status & 0xFFFFFFFF == _MORE_DATA:
                continue
            if status != 0 or count.value > capacity // ctypes.sizeof(_Item):
                raise _NativeError("PDH_ARRAY_UNAVAILABLE")
            items = ctypes.cast(buffer, ctypes.POINTER(_Item))
            rows = []
            for index in range(count.value):
                item = items[index]
                if item.formatted.status not in {0, 1} or not item.name:
                    continue
                value = (
                    item.formatted.value.double
                    if format_flag == _FMT_DOUBLE
                    else item.formatted.value.large
                )
                rows.append((item.name, value))
            if count.value and not rows:
                raise _NativeError("PDH_ARRAY_UNAVAILABLE")
            return rows
        raise _NativeError("PDH_ARRAY_UNSTABLE")

    def read(self):
        if self.library.PdhCollectQueryData(self.query) != 0:
            raise _NativeError("PDH_COLLECTION_FAILED")
        warming_up = not self.primed
        self.primed = True
        result = {}
        errors = []
        for key, (_, format_flag) in _COUNTERS.items():
            if key not in self.handles:
                result[key] = None
                errors.append("PDH_" + key.upper() + "_UNAVAILABLE")
            elif warming_up and key == "engine":
                result[key] = []
            else:
                try:
                    result[key] = self._array(self.handles[key], format_flag)
                except _NativeError:
                    result[key] = None
                    errors.append("PDH_" + key.upper() + "_UNAVAILABLE")
        return result, warming_up, errors

    def close(self):
        if self.query.value:
            self.library.PdhCloseQuery(self.query)
            self.query = ctypes.c_void_p()


def _engine_class(name):
    name = re.sub(r"#\d+$", "", name).casefold().replace(" ", "").replace("_", "")
    if name == "3d":
        return "3D"
    if "compute" in name:
        return "Compute"
    if "decode" in name:
        return "VideoDecode"
    if "encode" in name:
        return "VideoEncode"
    if name == "copy":
        return "Copy"
    return "Other"


def aggregate_counters(raw, metadata, *, warming_up=False, errors=()):
    """仅合并已知完整 LUID。重复实例取 max，独立节点显存再求和。"""
    errors = set(errors)
    software_luids = {item["luid"] for item in metadata if item.get("software")}
    adapters = {
        item["luid"]: {**item, "dedicated_used_bytes": None}
        for item in metadata
        if not item.get("software")
    }
    nodes = {}
    adapter_nodes = {}

    def entry(pid, high, low, physical):
        luid = _canonical_luid(high, low)
        if luid in software_luids:
            return None
        if luid not in adapters:
            errors.add("ADAPTER_MAPPING_UNAVAILABLE")
            return None
        pid, physical = int(pid), int(physical)
        if pid == 0:
            return None  # 驱动/内核工作没有可调度的用户进程，不能归入任一应用。
        if not 0 < pid <= 0xFFFFFFFF or not 0 <= physical < 64:
            raise ValueError("instance")
        return nodes.setdefault((pid, luid, physical), {"engines": {}, "dedicated_bytes": None})

    for kind, pattern in (("engine", _ENGINE), ("process_memory", _PROCESS)):
        for name, value in raw.get(kind) or []:
            try:
                match = pattern.fullmatch(name)
                if not match or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                    raise ValueError("instance")
                pid, high, low, physical, *extra = match.groups()
                row = entry(pid, high, low, physical)
                if row is None:
                    continue
                if kind == "engine":
                    label = _engine_class(extra[1])
                    row["engines"][label] = max(row["engines"].get(label, 0), min(100.0, value))
                else:
                    row["dedicated_bytes"] = max(row["dedicated_bytes"] or 0, int(value))
            except TypeError, ValueError, OverflowError:
                errors.add("PDH_INSTANCE_INVALID")

    for name, value in raw.get("adapter_memory") or []:
        try:
            match = _ADAPTER.fullmatch(name)
            if not match or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError("instance")
            high, low, physical = match.groups()
            if not 0 <= int(physical) < 64:
                raise ValueError("physical node")
            luid = _canonical_luid(high, low)
            if luid in software_luids:
                continue
            if luid not in adapters:
                errors.add("ADAPTER_MAPPING_UNAVAILABLE")
                continue
            key = (luid, int(physical))
            adapter_nodes[key] = max(adapter_nodes.get(key, 0), int(value))
        except TypeError, ValueError, OverflowError:
            errors.add("PDH_INSTANCE_INVALID")

    for (luid, _), value in adapter_nodes.items():
        adapters[luid]["dedicated_used_bytes"] = (
            adapters[luid]["dedicated_used_bytes"] or 0
        ) + value
    processes = {}
    for (pid, luid, _), row in nodes.items():
        process = processes.setdefault(
            pid, {"engines": {}, "dedicated_bytes": None, "adapters": [], "per_adapter": {}}
        )
        per_adapter = process["per_adapter"].setdefault(
            luid, {"engines": {}, "dedicated_bytes": None}
        )
        for label, value in row["engines"].items():
            per_adapter["engines"][label] = max(per_adapter["engines"].get(label, 0), value)
            process["engines"][label] = max(process["engines"].get(label, 0), value)
        if row["dedicated_bytes"] is not None:
            per_adapter["dedicated_bytes"] = (per_adapter["dedicated_bytes"] or 0) + row[
                "dedicated_bytes"
            ]
            process["dedicated_bytes"] = (process["dedicated_bytes"] or 0) + row["dedicated_bytes"]
        if luid not in process["adapters"]:
            process["adapters"].append(luid)
    complete = (
        bool(adapters) and raw.get("engine") is not None and raw.get("process_memory") is not None
    )
    return {
        "available": complete and not errors,
        "warming_up": warming_up,
        "processes": processes,
        "adapters": list(adapters.values()),
        "errors": sorted(errors),
    }


class WindowsGpuCounters:
    """同步接口，由调度层 to_thread 调用。初始化和关闭均不在模块导入时发生。"""

    def __init__(self, *, reader_factory=_PdhReader, adapter_provider=enumerate_dxgi_adapters):
        self._reader_factory = reader_factory
        self._adapter_provider = adapter_provider
        self._reader = None
        self._metadata = None
        self._closed = False
        self._lock = threading.Lock()

    def sample(self):
        started = time.perf_counter()
        with self._lock:
            result = {
                "available": False,
                "warming_up": False,
                "processes": {},
                "adapters": [],
                "errors": [],
            }
            if self._closed:
                result["errors"] = ["COLLECTOR_CLOSED"]
                return result
            try:
                self._metadata = self._adapter_provider()
                if self._reader is None:
                    self._reader = self._reader_factory()
                raw, warming_up, errors = self._reader.read()
                result = aggregate_counters(
                    raw, self._metadata, warming_up=warming_up, errors=errors
                )
            except OSError, _NativeError, AttributeError, ValueError, TypeError, KeyError:
                result["errors"] = ["WINDOWS_GPU_COUNTERS_UNAVAILABLE"]
                if self._reader:
                    self._reader.close()
                    self._reader = None
            result["sample_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            return result

    def close(self):
        with self._lock:
            self._closed = True
            if self._reader:
                self._reader.close()
                self._reader = None
