"""Native process identity observation without a PowerShell/WMI startup dependency."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


def process_creation_time(pid: int) -> str:
    """Read kernel birth time with only PROCESS_QUERY_LIMITED_INFORMATION rights.

    GetProcessTimes accepts this right, including for the protected-process fixtures:
    https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes
    Preserve the previous observer's Unix-millisecond precision used in marker bodies.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        creation, exit_time, kernel_time, user_time = (
            wintypes.FILETIME() for _ in range(4)
        )
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        milliseconds = (ticks - 116444736000000000) // 10000
        return f"{milliseconds // 1000}.{milliseconds % 1000:03d}"
    finally:
        kernel32.CloseHandle(handle)
