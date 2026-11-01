"""只读系统物理内存准入；不调整分页文件、工作集或系统配置。"""

import ctypes
import os

from nova.errors import ApplicationError

MIB = 1024 * 1024


def _read_system_memory():
    if os.name == "nt":
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.DWORD),
                ("load", wintypes.DWORD),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
        kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
        if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("memory telemetry unavailable")
        return status.total_physical, status.available_physical
    page_size = os.sysconf("SC_PAGE_SIZE")
    return page_size * os.sysconf("SC_PHYS_PAGES"), page_size * os.sysconf("SC_AVPHYS_PAGES")


def get_system_memory():
    """统一以MiB记录物理内存；失败为未知，不能把虚拟/分页容量算作RAM。"""
    unknown = {"available": False, "total_mib": 0, "available_mib": 0}
    try:
        total, available = _read_system_memory()
        if type(total) is not int or type(available) is not int or not 0 <= available <= total:
            return unknown
        if total < MIB:
            return unknown
        return {"available": True, "total_mib": total // MIB, "available_mib": available // MIB}
    except OSError, AttributeError, ValueError, TypeError, OverflowError:
        return unknown


def require_system_memory(min_total_mib, min_available_mib):
    sample = get_system_memory()
    if (
        sample.get("available") is not True
        or type(sample.get("total_mib")) is not int
        or type(sample.get("available_mib")) is not int
        or not 0 <= sample["available_mib"] <= sample["total_mib"]
        or sample["total_mib"] <= 0
    ):
        raise ApplicationError(
            "TELEMETRY_UNAVAILABLE", "无法读取系统内存，已阻止实验模型加载；请选择9B或4B", 503
        )
    if sample["total_mib"] < min_total_mib or sample["available_mib"] < min_available_mib:
        raise ApplicationError(
            "MODEL_RAM_INSUFFICIENT",
            "系统内存不足以安全加载27B；请升级内存并释放其他负载，或选择9B/4B",
            409,
        )
    return sample
