"""Caretaker writer lock: one writer per vault, released on exit."""

import sys

import pytest

from tesseract_mcp import librarian, locking
from tesseract_mcp.vault import VaultError


def test_second_writer_is_refused_while_first_holds(tmp_path):
    with locking.caretaker_lock(tmp_path):
        with pytest.raises(locking.LockBusy):
            with locking.caretaker_lock(tmp_path):
                pass


def test_lock_is_released_after_the_block(tmp_path):
    with locking.caretaker_lock(tmp_path):
        pass
    with locking.caretaker_lock(tmp_path):
        pass


def test_lock_busy_is_a_vault_error(tmp_path):
    assert issubclass(locking.LockBusy, VaultError)


def test_librarian_cli_refuses_while_another_caretaker_writes(
        vault, vault_dir, monkeypatch):
    from tesseract_mcp import indexer

    librarian.save_state(vault, librarian.load_state(vault))  # not a first run
    called = []
    monkeypatch.setattr(librarian, "run_sweep",
                        lambda *a, **k: called.append(1))
    monkeypatch.setattr(sys, "argv", ["librarian", str(vault_dir)])
    with locking.caretaker_lock(indexer.state_dir(vault.root)):
        with pytest.raises(locking.LockBusy):
            librarian.main()
    assert called == []


def test_librarian_dry_run_takes_no_lock(vault, vault_dir, monkeypatch):
    from tesseract_mcp import indexer

    monkeypatch.setattr(sys, "argv", ["librarian", str(vault_dir), "--dry-run"])
    monkeypatch.setattr(librarian, "run_sweep", lambda *a, **k: {
        "steps": {}, "health": {}, "errors": {}, "applied": False})
    monkeypatch.setattr(librarian, "format_report", lambda r, now: "")
    with locking.caretaker_lock(indexer.state_dir(vault.root)):
        librarian.main()  # a read-only dry-run never waits on a writer
