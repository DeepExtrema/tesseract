"""One caretaker writer per vault at a time.

The Librarian sweep, the indexer CLI (and index-nightly.sh), the organizer
and consolidation CLIs, and the MCP tools that index, move or merge all
rewrite the same manifest, graph notes and cache. An OS file lock in the
vault's state dir serializes them; the OS drops it when the holder exits,
so a killed sweep can never leave a stale lock behind. The lock primitive
itself (flock vs. msvcrt) lives in the platforms backend.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from . import platforms
from .vault import VaultError

LOCK_FILE = "caretaker.lock"


class LockBusy(VaultError):
    """Another caretaker is writing this vault right now."""


@contextmanager
def caretaker_lock(state_root: str | Path):
    path = Path(state_root) / LOCK_FILE
    f = open(path, "a+b")
    try:
        try:
            platforms.current.try_lock(f)
        except OSError as e:
            raise LockBusy(
                "another caretaker (Librarian sweep, indexer, organizer or "
                "consolidation) is writing this vault; try again when it "
                f"finishes ({path})"
            ) from e
        try:
            yield
        finally:
            platforms.current.unlock(f)
    finally:
        f.close()  # also releases a POSIX flock
