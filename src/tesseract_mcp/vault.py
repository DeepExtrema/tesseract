"""Filesystem access to the Obsidian vault with safety rules.

Two rules are enforced in code, not by convention:
- No path may escape the vault root.
- Writes outside the Claude/ subtree require confirm_outside_claude=True,
  which callers may only pass when the user explicitly asked for the write.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path


class VaultError(Exception):
    """Raised when a vault operation is invalid."""


class NoteExists(VaultError):
    """A create-only write lost the race to (or collided with) an existing note."""


def _retry_locked(fn):
    """Run fn, retrying transient PermissionError with backoff.

    Obsidian/LiveSync/antivirus briefly lock notes on Windows, making file
    operations raise PermissionError (WinError 5). Caretaker sweeps run
    while Obsidian is open, so both write and append must ride out short
    locks before giving up."""
    for attempt in range(5):
        try:
            return fn()
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


class Vault:
    CLAUDE_DIR = "Claude"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise VaultError(f"Vault root does not exist: {self.root}")

    def resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise VaultError(f"Path escapes the vault: {relative}")
        return candidate

    def in_claude(self, relative: str) -> bool:
        path = self.resolve(relative)
        claude_root = self.root / self.CLAUDE_DIR
        norm = os.path.normcase(str(path))
        claude_norm = os.path.normcase(str(claude_root))
        return norm == claude_norm or norm.startswith(claude_norm + os.sep)

    def read(self, relative: str) -> str:
        path = self.resolve(relative)
        if path.is_dir():
            raise VaultError(f"'{relative}' is a directory, not a note.")
        if not path.is_file():
            raise VaultError(f"Note not found: {relative}")
        return path.read_text(encoding="utf-8")

    def _check_write_allowed(self, relative: str, confirm_outside_claude: bool) -> None:
        if not self.in_claude(relative) and not confirm_outside_claude:
            raise VaultError(
                f"'{relative}' is outside Claude/. Pass confirm_outside_claude=True "
                "only when the user explicitly asked for this write."
            )

    def write(
        self,
        relative: str,
        content: str,
        *,
        overwrite: bool = False,
        confirm_outside_claude: bool = False,
    ) -> Path:
        path = self.resolve(relative)
        self._check_write_allowed(relative, confirm_outside_claude)
        if path.is_dir():
            raise VaultError(f"'{relative}' is a directory, not a note.")
        if path.exists() and not overwrite:
            raise NoteExists(
                f"'{relative}' already exists. Pass overwrite=True to replace it."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name: several agents may write into the same folder at
        # once, and a shared temp name would let one agent's bytes land in
        # another agent's note.
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp-write")
        # newline="\n": Path.write_text's default newline translation
        # rewrites every LF in `content` as the platform line ending, so on
        # Windows the first patch of a pre-existing LF file silently
        # rewrites every line as CRLF. Force LF regardless of platform.
        tmp.write_text(content, encoding="utf-8", newline="\n")
        try:
            if overwrite:
                _retry_locked(lambda: os.replace(tmp, path))
            else:
                # Create-only must be atomic. The exists() check above is only
                # a fast path: two agents can both pass it, and os.replace
                # would then silently clobber whichever landed first. A hard
                # link fails if the target exists, so exactly one writer wins.
                try:
                    _retry_locked(lambda: os.link(tmp, path))
                except FileExistsError:
                    raise NoteExists(
                        f"'{relative}' already exists. Pass overwrite=True to replace it."
                    ) from None
                except PermissionError:
                    raise  # a persistent lock is an error, not "no hard links"
                except OSError:
                    # Filesystem without hard links: best effort, never lose data.
                    if path.exists():
                        raise NoteExists(
                            f"'{relative}' already exists. Pass overwrite=True to replace it."
                        ) from None
                    _retry_locked(lambda: os.replace(tmp, path))
        finally:
            tmp.unlink(missing_ok=True)
        return path

    def append(
        self,
        relative: str,
        content: str,
        *,
        confirm_outside_claude: bool = False,
    ) -> Path:
        path = self.resolve(relative)
        self._check_write_allowed(relative, confirm_outside_claude)
        if path.is_dir():
            raise VaultError(f"'{relative}' is a directory, not a note.")
        path.parent.mkdir(parents=True, exist_ok=True)
        # retry only the open: Windows locks surface there, and re-running a
        # write that failed midway would append the content twice
        f = _retry_locked(lambda: path.open("a", encoding="utf-8", newline="\n"))
        with f:
            f.write(content)
        return path
