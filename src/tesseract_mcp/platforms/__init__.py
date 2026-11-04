"""Everything that differs between operating systems, in one place.

Each backend module implements the same contract (the names in CONTRACT):

- posix.py   — Linux and macOS
- windows.py — Windows

The backend for the running OS is chosen once, below, as `current`. The rest
of the package calls `platforms.current.<name>` (or the venv helpers here) and
never branches on os.name itself, so OS behaviour can be read, changed and
tested per backend. Supporting another OS means adding a backend module that
defines every CONTRACT name and selecting it here; tests/test_platforms.py
fails if any backend is missing one.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import posix, windows

# The interface every backend must provide:
#   NAME                       backend id, for messages and tests
#   VENV_BIN_DIR, EXE_SUFFIX   virtualenv console-script layout
#   O_BINARY                   extra os.open flag so bytes are not translated
#   quote_command(argv)        argv as text the OS's usual shell accepts
#   try_lock(f), unlock(f)     non-blocking exclusive lock on an open file
#   hardlinks_unsupported(e)   does this os.link error mean "no hard links
#                              on this filesystem" (vs. a lock or real error)?
CONTRACT = (
    "NAME",
    "VENV_BIN_DIR",
    "EXE_SUFFIX",
    "O_BINARY",
    "quote_command",
    "try_lock",
    "unlock",
    "hardlinks_unsupported",
)

BACKENDS = (posix, windows)

current = windows if os.name == "nt" else posix


def venv_bin_dir(repo_root: Path) -> Path:
    return Path(repo_root) / ".venv" / current.VENV_BIN_DIR


def exe_name(stem: str) -> str:
    return stem + current.EXE_SUFFIX


def venv_python(repo_root: Path) -> Path:
    return venv_bin_dir(repo_root) / exe_name("python")


def venv_script(repo_root: Path, stem: str) -> Path:
    return venv_bin_dir(repo_root) / exe_name(stem)
