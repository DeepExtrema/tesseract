"""Platform-aware paths into the repo's own virtualenv.

Windows venvs put console scripts in `Scripts/` with an `.exe` suffix; POSIX
(macOS/Linux) venvs use `bin/` with no suffix.
"""

from __future__ import annotations

import os
from pathlib import Path


def venv_bin_dir(repo_root: Path) -> Path:
    return Path(repo_root) / ".venv" / ("Scripts" if os.name == "nt" else "bin")


def exe_name(stem: str) -> str:
    return stem + (".exe" if os.name == "nt" else "")


def venv_python(repo_root: Path) -> Path:
    return venv_bin_dir(repo_root) / exe_name("python")


def venv_script(repo_root: Path, stem: str) -> Path:
    return venv_bin_dir(repo_root) / exe_name(stem)
