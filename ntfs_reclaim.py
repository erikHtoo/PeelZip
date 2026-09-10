"""Windows NTFS sparse-range reclamation primitive.

This preserves a file's logical length while returning a verified byte range's
physical clusters to NTFS. It is intentionally separate from ZIP extraction
until its crash/recovery tests pass.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys

FSCTL_SET_SPARSE = 0x000900C4
FSCTL_SET_ZERO_DATA = 0x000980C8
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
FILE_SHARE_DELETE = 4
OPEN_EXISTING = 3
FILE_FLAG_WRITE_THROUGH = 0x80000000


class FILE_ZERO_DATA_INFORMATION(ctypes.Structure):
    _fields_ = [('FileOffset', ctypes.c_longlong),
                ('BeyondFinalZero', ctypes.c_longlong)]


def reclaim_range(path, offset, length):
    """Punch a logical byte range out of an NTFS/ReFS sparse file.

    Reading the range later returns zero bytes; logical file length and all
    offsets after the range are unchanged. Caller must have already committed
    and verified the replacement output and journal checkpoint.
    """
    if sys.platform != 'win32':
        raise OSError('Aggressive reclamation currently requires Windows')
    path = Path(path).absolute()
    if offset < 0 or length <= 0:
        raise ValueError('offset must be non-negative and length must be positive')
    size = path.stat().st_size
    if offset + length > size:
        raise ValueError('range exceeds file length')
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    ioctl = kernel32.DeviceIoControl
    ioctl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                      wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    ioctl.restype = wintypes.BOOL
    handle = create(str(path), GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    None, OPEN_EXISTING, FILE_FLAG_WRITE_THROUGH, None)
    if handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        returned = wintypes.DWORD()
        if not ioctl(handle, FSCTL_SET_SPARSE, None, 0, None, 0,
                     ctypes.byref(returned), None):
            error = ctypes.get_last_error()
            # Already sparse is harmless on Windows.
            if error not in (0, 0xB7):
                raise ctypes.WinError(error)
        info = FILE_ZERO_DATA_INFORMATION(offset, offset + length)
        if not ioctl(handle, FSCTL_SET_ZERO_DATA, ctypes.byref(info), ctypes.sizeof(info),
                     None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def allocated_bytes(path):
    """Return physical allocation on Windows; logical size elsewhere."""
    path = Path(path)
    if sys.platform != 'win32':
        return path.stat().st_size
    import ctypes.wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = kernel32.GetCompressedFileSizeW
    fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    fn.restype = wintypes.DWORD
    high = wintypes.DWORD()
    low = fn(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
        raise ctypes.WinError(ctypes.get_last_error())
    return (high.value << 32) | low
