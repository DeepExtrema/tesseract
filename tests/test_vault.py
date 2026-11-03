import os
import sys

import pytest

from tesseract_mcp.vault import Vault, VaultError


def test_missing_root_raises(tmp_path):
    with pytest.raises(VaultError, match="does not exist"):
        Vault(tmp_path / "nope")


def test_read_note(vault):
    assert "Remember to check" in vault.read("Daily.md")


def test_read_missing_note_raises(vault):
    with pytest.raises(VaultError, match="not found"):
        vault.read("Ghost.md")


def test_path_escape_rejected(vault):
    with pytest.raises(VaultError, match="escapes"):
        vault.read("../outside.md")


def test_write_inside_claude_allowed(vault):
    vault.write("Claude/Inbox/note.md", "hello")
    assert vault.read("Claude/Inbox/note.md") == "hello"


def test_write_outside_claude_refused_by_default(vault):
    with pytest.raises(VaultError, match="outside Claude/"):
        vault.write("Projects/New.md", "hello")


def test_write_outside_claude_with_confirmation(vault):
    vault.write("Projects/New.md", "hello", confirm_outside_claude=True)
    assert vault.read("Projects/New.md") == "hello"


def test_write_refuses_overwrite_by_default(vault):
    vault.write("Claude/Inbox/note.md", "v1")
    with pytest.raises(VaultError, match="already exists"):
        vault.write("Claude/Inbox/note.md", "v2")


def test_write_overwrite_flag(vault):
    vault.write("Claude/Inbox/note.md", "v1")
    vault.write("Claude/Inbox/note.md", "v2", overwrite=True)
    assert vault.read("Claude/Inbox/note.md") == "v2"


def test_write_creates_parent_dirs(vault):
    vault.write("Claude/Sessions/2026/deep.md", "x")
    assert vault.read("Claude/Sessions/2026/deep.md") == "x"


def test_append_creates_and_appends(vault):
    vault.append("Claude/Inbox/2026-07-05.md", "- one\n")
    vault.append("Claude/Inbox/2026-07-05.md", "- two\n")
    assert vault.read("Claude/Inbox/2026-07-05.md") == "- one\n- two\n"


def test_append_outside_claude_refused_by_default(vault):
    with pytest.raises(VaultError, match="outside Claude/"):
        vault.append("Daily.md", "- sneaky\n")


def test_in_claude(vault):
    assert vault.in_claude("Claude/Index.md")
    assert vault.in_claude("Claude/Sessions/x.md")
    assert not vault.in_claude("Daily.md")
    assert not vault.in_claude("ClaudeFake/x.md")


@pytest.mark.skipif(
    os.path.normcase("A") == "A",
    reason="case-insensitive quarantine semantics only apply on case-insensitive filesystems",
)
def test_in_claude_case_insensitive_on_windows(vault):
    assert vault.in_claude("claude/Sessions/x.md")
    assert vault.in_claude("CLAUDE/Index.md")


def test_write_to_directory_raises_vault_error(vault):
    with pytest.raises(VaultError, match="directory"):
        vault.write("Claude/Sessions", "x", overwrite=True)


def test_write_to_vault_root_raises_vault_error(vault):
    with pytest.raises(VaultError, match="directory"):
        vault.write("", "x", overwrite=True, confirm_outside_claude=True)


def test_append_to_directory_raises_vault_error(vault):
    with pytest.raises(VaultError, match="directory"):
        vault.append("Claude/Inbox", "- x\n")


def test_read_directory_raises_vault_error(vault):
    with pytest.raises(VaultError, match="directory"):
        vault.read("Claude/Sessions")


def test_write_is_atomic_no_tmp_left_behind(vault):
    vault.write("Claude/Inbox/atomic.md", "v1")
    vault.write("Claude/Inbox/atomic.md", "v2", overwrite=True)
    assert vault.read("Claude/Inbox/atomic.md") == "v2"
    leftovers = list(vault.root.rglob("*.tmp-write"))
    assert leftovers == []


def test_write_preserves_lf_line_endings_on_windows(vault):
    """Regression: Path.write_text's default newline translation rewrites
    every '\\n' as os.linesep, so on Windows the first patch of a
    pre-existing LF file silently turned every line into CRLF. Bytes-level
    check — a str-level read would hide this via universal-newline
    translation on the way back in."""
    vault.write("Claude/Inbox/lf.md", "line one\nline two\nline three\n")
    raw = (vault.root / "Claude" / "Inbox" / "lf.md").read_bytes()
    assert b"\r\n" not in raw
    assert raw == b"line one\nline two\nline three\n"


def test_append_preserves_lf_line_endings_on_windows(vault):
    vault.append("Claude/Inbox/lf-append.md", "line one\n")
    vault.append("Claude/Inbox/lf-append.md", "line two\n")
    raw = (vault.root / "Claude" / "Inbox" / "lf-append.md").read_bytes()
    assert b"\r\n" not in raw
    assert raw == b"line one\nline two\n"


def test_write_retries_transient_windows_lock(vault, monkeypatch):
    # Obsidian/LiveSync briefly lock notes; publishing then raises
    # PermissionError (WinError 5). Two transient failures must not crash.
    # A create-only write publishes via os.link; overwrite uses os.replace.
    import os as os_mod
    from tesseract_mcp import vault as vault_mod
    real_link = os_mod.link
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real_link(src, dst)

    monkeypatch.setattr(vault_mod.os, "link", flaky)
    monkeypatch.setattr(vault_mod.time, "sleep", lambda s: None)
    vault.write("Claude/Inbox/lock-test.md", "content")
    assert calls["n"] == 3
    assert vault.read("Claude/Inbox/lock-test.md") == "content"


def test_overwrite_retries_transient_windows_lock(vault, monkeypatch):
    import os as os_mod
    from tesseract_mcp import vault as vault_mod
    vault.write("Claude/Inbox/lock-over.md", "v1")
    real_replace = os_mod.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(vault_mod.os, "replace", flaky)
    monkeypatch.setattr(vault_mod.time, "sleep", lambda s: None)
    vault.write("Claude/Inbox/lock-over.md", "v2", overwrite=True)
    assert calls["n"] == 3
    assert vault.read("Claude/Inbox/lock-over.md") == "v2"


def test_write_gives_up_after_persistent_lock(vault, monkeypatch):
    from tesseract_mcp import vault as vault_mod
    monkeypatch.setattr(vault_mod.os, "link",
                        lambda s, d: (_ for _ in ()).throw(
                            PermissionError(5, "Access is denied")))
    monkeypatch.setattr(vault_mod.time, "sleep", lambda s: None)
    import pytest as _pytest
    with _pytest.raises(PermissionError):
        vault.write("Claude/Inbox/lock-test2.md", "content")


def test_create_only_without_hard_links_still_has_one_winner(vault, monkeypatch):
    # link(2) answers EPERM on FAT/exFAT and some FUSE/SMB mounts; that is
    # "no hard links", not a lock, so it must fall back at once rather than
    # retry and fail — and the fallback must not let a racer clobber the note.
    import errno
    from concurrent.futures import ThreadPoolExecutor
    from tesseract_mcp import vault as vault_mod
    from tesseract_mcp.vault import NoteExists

    def no_links(src, dst):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(vault_mod.os, "link", no_links)
    monkeypatch.setattr(vault_mod.time, "sleep",
                        lambda s: pytest.fail("EPERM must not be retried"))

    def attempt(i):
        try:
            vault.write("Claude/Inbox/nolinks.md", f"writer {i}\nline two\n")
            return i
        except NoteExists:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        winners = [i for i in pool.map(attempt, range(16)) if i is not None]
    assert len(winners) == 1
    raw = (vault.root / "Claude" / "Inbox" / "nolinks.md").read_bytes()
    assert raw == f"writer {winners[0]}\nline two\n".encode()
    assert not list(vault.root.rglob("*.tmp-write"))


@pytest.mark.skipif(sys.platform == "win32",
                    reason="full path would exceed MAX_PATH; the limit is POSIX NAME_MAX")
def test_write_accepts_names_near_the_filesystem_limit(vault):
    rel = "Claude/Inbox/" + "a" * 237 + ".md"  # 240-byte component
    vault.write(rel, "v1")
    vault.write(rel, "v2", overwrite=True)
    assert vault.read(rel) == "v2"


def test_locked_temp_cleanup_does_not_fail_a_published_write(vault, monkeypatch):
    # Windows can hold the temp link open after os.link published the note;
    # the write succeeded and must report success.
    from pathlib import Path
    from tesseract_mcp import vault as vault_mod
    real_unlink = Path.unlink

    def locked(self, missing_ok=False):
        if self.name.endswith(".tmp-write"):
            raise PermissionError(13, "Access is denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)
    monkeypatch.setattr(vault_mod.time, "sleep", lambda s: None)
    vault.write("Claude/Inbox/cleanup.md", "content")
    assert vault.read("Claude/Inbox/cleanup.md") == "content"


def test_append_retries_transient_windows_lock(vault, vault_dir, monkeypatch):
    # append hits the same Windows lock window as write: the open("a")
    # itself raises PermissionError while Obsidian/LiveSync hold the file.
    from tesseract_mcp import vault as vault_mod
    target = vault_dir / "Claude" / "Inbox" / "lock-append.md"
    real_open = type(target).open
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        if self == target:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(5, "Access is denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(type(target), "open", flaky)
    monkeypatch.setattr(vault_mod.time, "sleep", lambda s: None)
    vault.append("Claude/Inbox/lock-append.md", "- captured\n")
    assert calls["n"] == 3
    assert vault.read("Claude/Inbox/lock-append.md") == "- captured\n"


def test_append_gives_up_after_persistent_lock(vault, vault_dir, monkeypatch):
    import pytest as _pytest
    from tesseract_mcp import vault as vault_mod
    target = vault_dir / "Claude" / "Inbox" / "lock-append2.md"
    real_open = type(target).open

    def always_locked(self, *args, **kwargs):
        if self == target:
            raise PermissionError(5, "Access is denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(type(target), "open", always_locked)
    monkeypatch.setattr(vault_mod.time, "sleep", lambda s: None)
    with _pytest.raises(PermissionError):
        vault.append("Claude/Inbox/lock-append2.md", "- captured\n")


def test_append_does_not_retry_after_write_started(vault, vault_dir, monkeypatch):
    # a failure mid-write must surface, not re-open and append the content
    # a second time on top of what already landed
    import pytest as _pytest
    from tesseract_mcp import vault as vault_mod
    target = vault_dir / "Claude" / "Inbox" / "partial-append.md"
    real_open = type(target).open

    class PartialWrite:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()

        def close(self):
            self._f.close()

        def write(self, s):
            self._f.write(s[: len(s) // 2])
            self._f.flush()
            raise PermissionError(5, "Access is denied")

    def opener(self, *args, **kwargs):
        f = real_open(self, *args, **kwargs)
        return PartialWrite(f) if self == target else f

    monkeypatch.setattr(type(target), "open", opener)
    monkeypatch.setattr(vault_mod.time, "sleep", lambda s: None)
    with _pytest.raises(PermissionError):
        vault.append("Claude/Inbox/partial-append.md", "- captured\n")
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "- cap"
