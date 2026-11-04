"""Windows backend.

OS-only modules (msvcrt) are imported inside functions so this backend can be
imported, and its pure logic tested, on Linux and macOS too.
"""

from __future__ import annotations

import os
import subprocess

NAME = "windows"
VENV_BIN_DIR = "Scripts"
EXE_SUFFIX = ".exe"
# Without O_BINARY the C runtime rewrites "\n" as "\r\n" on write; notes are
# stored with LF on every OS. Absent off Windows, hence the fallback.
O_BINARY = getattr(os, "O_BINARY", 0)


def quote_command(argv: list[str]) -> str:
    # cmd/PowerShell quoting; shlex would emit POSIX single quotes they reject.
    return subprocess.list2cmdline(argv)


_FILE_APPEND_DATA = 0x0004
_FILE_SHARE_READ_WRITE_DELETE = 0x0001 | 0x0002 | 0x0004
_OPEN_ALWAYS = 4
_FILE_ATTRIBUTE_NORMAL = 0x80


def open_append(path) -> int:
    """A write-only fd whose every write lands at end of file.

    Python's open("a") on Windows only emulates append: the C runtime seeks
    to the end and then writes, so two agents appending at once can land on
    the same offset and one line is lost. A handle opened with
    FILE_APPEND_DATA but not FILE_WRITE_DATA makes Windows itself write at
    end of file (the WriteFile docs call it equivalent to writing at offset
    0xFFFFFFFF'FFFFFFFF), so concurrent appends cannot overwrite each other.
    Sharing violations (Obsidian/LiveSync holding the note) raise
    PermissionError, which callers retry like any other Windows lock."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path), _FILE_APPEND_DATA, _FILE_SHARE_READ_WRITE_DELETE, None,
        _OPEN_ALWAYS, _FILE_ATTRIBUTE_NORMAL, None,
    )
    if handle is None or handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    # flags=0: binary mode, so the C runtime never turns "\n" into "\r\n"
    return msvcrt.open_osfhandle(handle, 0)


def try_lock(f) -> None:
    """Exclusive, non-blocking; raises OSError while another process holds it."""
    import msvcrt

    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)


def unlock(f) -> None:
    # Unlike flock, msvcrt byte locks must be released explicitly.
    import msvcrt

    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def hardlinks_unsupported(err: OSError) -> bool:
    # On Windows a PermissionError from os.link is a sharing violation or
    # lock held by Obsidian/LiveSync/antivirus: retry it, never treat it as
    # "no hard links". FAT/exFAT and some network shares fail with other
    # codes (ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED).
    return not isinstance(err, PermissionError)
