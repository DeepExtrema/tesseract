"""One caretaker writer per vault at a time.

The Librarian sweep, the indexer CLI (and index-nightly.sh), the organizer
and consolidation CLIs, and the MCP tools that index, move or merge all
rewrite the same manifest, graph notes and cache. An OS file lock in the
vault's state dir serializes them; the OS drops it when the holder exits,
so a killed sweep can never leave a stale lock behind.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from .vault import VaultError

if os.name == "nt":
    import msvcrt
else:
    import fcntl

LOCK_FILE = "caretaker.lock"


class LockBusy(VaultError):
    """Another caretaker is writing this vault right now."""


@contextmanager
def caretaker_lock(state_root: str | Path):
    path = Path(state_root) / LOCK_FILE
    f = open(path, "a+b")
    try:
        try:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise LockBusy(
                "another caretaker (Librarian sweep, indexer, organizer or "
                "consolidation) is writing this vault; try again when it "
                f"finishes ({path})"
            ) from e
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        f.close()  # also releases flock
