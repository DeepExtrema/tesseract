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

from . import platforms


class VaultError(Exception):
    """Raised when a vault operation is invalid."""


class NoteExists(VaultError):
    """A create-only write lost the race to (or collided with) an existing note."""


class _NoHardLinks(Exception):
    """The filesystem cannot hard-link; publish with an exclusive create."""


def _exists_error(relative: str) -> NoteExists:
    return NoteExists(
        f"'{relative}' already exists. Pass overwrite=True to replace it."
    )


def _link(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError as e:
        # Which errors mean "this filesystem has no hard links" differs per
        # OS (see platforms/); anything else, e.g. a Windows lock, is raised
        # for _retry_locked to handle.
        if platforms.current.hardlinks_unsupported(e):
            raise _NoHardLinks from e
        raise


def _discard(tmp: Path) -> None:
    # The write's outcome is already decided; a locked temp file must not
    # turn a published note (or a NoteExists) into a different error.
    try:
        _retry_locked(lambda: tmp.unlink(missing_ok=True))
    except OSError:
        pass


def _write_all(fd: int, data: bytes) -> None:
    # os.write may write less than asked (signals, pipes); loop until done.
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _create_exclusive(path: Path, relative: str, data: bytes) -> None:
    # O_EXCL keeps exactly one winner where hard links are unavailable; the
    # cost is that a reader may briefly see the note half-written.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | platforms.current.O_BINARY
    try:
        fd = _retry_locked(lambda: os.open(path, flags, 0o666))
    except FileExistsError:
        raise _exists_error(relative) from None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


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
            raise _exists_error(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Short unique temp name in the target folder: concurrent writers
        # must not share one, and suffixing the note's own name would push
        # long-but-valid names past the 255-byte component limit. The leading
        # dot keeps Obsidian from indexing it.
        tmp = path.with_name(f".{secrets.token_hex(8)}.tmp-write")
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
                    _retry_locked(lambda: _link(tmp, path))
                except FileExistsError:
                    raise _exists_error(relative) from None
                except _NoHardLinks:
                    _create_exclusive(path, relative, content.encode("utf-8"))
        finally:
            _discard(tmp)
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
        fd = _retry_locked(lambda: platforms.current.open_append(path))
        try:
            # Encode once and write raw bytes: LF on every OS, and one write
            # per append so concurrent agents' lines never interleave.
            _write_all(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        return path
