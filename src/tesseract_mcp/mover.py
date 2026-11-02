"""Move a vault note while keeping every inbound link resolvable.

Only path-qualified wikilinks are rewritten: `[[old/path` immediately
followed by `]]`, `|`, or `#` (the lookahead prevents `[[Note 2` from
matching a move of `Note`). Bare `[[Stem]]` links keep working because the
stem does not change and the organizer's duplicate-stem guard ensures the
stem stays unique vault-wide.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import indexer
from .search import iter_note_files
from .vault import Vault, VaultError, _retry_locked


def _no_md(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def _link_pattern(rel: str) -> re.Pattern:
    return re.compile(r"\[\[" + re.escape(_no_md(rel)) + r"(?=[\]|#])")


def duplicate_stem_exists(vault: Vault, rel: str) -> bool:
    stem = _no_md(rel).rsplit("/", 1)[-1].casefold()
    count = 0
    for p, _rel in iter_note_files(vault):
        if p.stem.casefold() == stem:
            count += 1
            if count > 1:
                return True
    return False


def _decode(data: bytes) -> str:
    # surrogateescape round-trips any byte that is not valid UTF-8, so a
    # rewrite never silently drops content from a human note
    return data.decode("utf-8", "surrogateescape")


def _encode(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def _write_bytes(path: Path, data: bytes) -> None:
    """Atomic byte-exact write: no newline translation (CRLF/LF stay as the
    file had them) and no half-written note if the process dies."""
    tmp = path.with_name(path.name + ".tmp-move")
    tmp.write_bytes(data)
    _retry_locked(lambda: os.replace(tmp, path))


def _move_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _retry_locked(lambda: os.replace(src, dst))


def _plan_rewrites(
    vault: Vault, src_rel: str, dst_rel: str
) -> list[tuple[Path, str, bytes, bytes, int]]:
    """(path, rel, original bytes, rewritten bytes, count) for every note
    with a path-qualified link to src_rel. Reads only; writes nothing."""
    pattern = _link_pattern(src_rel)
    replacement = "[[" + _no_md(dst_rel)
    plans = []
    for p, rel in iter_note_files(vault):
        original = p.read_bytes()
        new_text, count = pattern.subn(replacement, _decode(original))
        if count:
            plans.append((p, rel, original, _encode(new_text), count))
    return plans


def move_note(vault: Vault, old_rel: str, new_rel: str) -> dict:
    """Move a note and rewrite its inbound path-qualified links, all or
    nothing: links are planned in memory, the file moves first, then the
    rewrites land; any failure restores every touched file and moves the
    note back before re-raising, so no link is left pointing at a path the
    note never reached."""
    src = vault.resolve(old_rel)
    dst = vault.resolve(new_rel)
    if not src.is_file():
        raise VaultError(f"Cannot move: not a file: {old_rel}")
    if dst.exists():
        raise VaultError(f"Cannot move: destination exists: {new_rel}")
    plans = _plan_rewrites(vault, old_rel, new_rel)
    _move_file(src, dst)
    written: list[tuple[Path, bytes]] = []
    try:
        for p, _rel, original, rewritten, _count in plans:
            target = dst if p == src else p  # a self-link travels with the note
            _write_bytes(target, rewritten)
            written.append((target, original))
    except BaseException:
        for target, original in reversed(written):
            try:
                _write_bytes(target, original)
            except OSError:
                pass  # best effort: keep restoring the rest
        try:
            _move_file(dst, src)
        except OSError:
            pass
        raise
    indexer.rename_manifest_entry(vault.root, old_rel, new_rel)
    return {"from": old_rel, "to": new_rel,
            "rewrites": [{"path": rel, "count": count}
                         for _p, rel, _o, _r, count in plans]}


def reverse_rewrites(
    vault: Vault, old_rel: str, new_rel: str, rewrite_paths: list[str]
) -> None:
    """Undo helper: rewrite new→old in exactly the files a move touched."""
    pattern = _link_pattern(new_rel)
    replacement = "[[" + _no_md(old_rel)
    for rel in rewrite_paths:
        p = vault.resolve(rel)
        if not p.is_file():
            continue
        original = p.read_bytes()
        new_text, count = pattern.subn(replacement, _decode(original))
        if count:
            _write_bytes(p, _encode(new_text))
