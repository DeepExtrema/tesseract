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
