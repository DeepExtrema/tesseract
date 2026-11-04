"""POSIX backend: Linux and macOS.

macOS shares this module because nothing tesseract-mcp does behaves
differently there from Linux today. Give macOS its own backend only when a
real difference appears, not in advance.

OS-only modules (fcntl) are imported inside functions so every backend can be
imported, and its pure logic tested, on any OS.
"""

from __future__ import annotations

import errno
import os
import shlex

NAME = "posix"
VENV_BIN_DIR = "bin"
EXE_SUFFIX = ""
O_BINARY = 0  # POSIX never translates bytes


def quote_command(argv: list[str]) -> str:
    return shlex.join(argv)


def open_append(path) -> int:
    """A write-only fd whose every write lands at end of file.

    With O_APPEND the kernel moves to EOF and writes in one step, so
    concurrent appenders (other agents' servers) never overwrite each other."""
    return os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)


def try_lock(f) -> None:
    """Exclusive, non-blocking; raises OSError while another process holds it."""
    import fcntl

    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(f) -> None:
    # flock locks belong to the open file and are released when it closes.
    pass


def hardlinks_unsupported(err: OSError) -> bool:
    # link(2) reports "this filesystem has no hard links" (FAT/exFAT on
    # Linux, some FUSE/SMB mounts) as EPERM, which Python raises as
    # PermissionError. macOS uses ENOTSUP for the same case. Any other
    # PermissionError is a real access problem and must not be treated as one.
    if isinstance(err, PermissionError):
        return err.errno == errno.EPERM
    return True
