"""Several agents writing into one vault at the same time.

Agents contribute through separate server instances that share the vault, so
the write path has to be safe without a coordinator: create-only writes are
atomic, temp files never collide, and each agent's identity is recorded."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from tesseract_mcp import notes
from tesseract_mcp.vault import NoteExists, VaultError


def test_create_only_write_has_exactly_one_winner(vault):
    def attempt(i):
        try:
            vault.write("Claude/Inbox/race.md", f"writer {i}")
            return "won"
        except NoteExists:
            return "lost"

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(16)))
    assert results.count("won") == 1
    assert vault.read("Claude/Inbox/race.md").startswith("writer ")
    assert not list(vault.root.rglob("*.tmp-write"))


def test_note_exists_is_still_a_vault_error(vault):
    vault.write("Claude/Inbox/dup.md", "a")
    with pytest.raises(VaultError, match="already exists"):
        vault.write("Claude/Inbox/dup.md", "b")


def test_concurrent_log_session_keeps_every_note(vault):
    def log(i):
        return notes.log_session(vault, "Same title", f"body {i}", "p", [])

    with ThreadPoolExecutor(max_workers=8) as pool:
        rels = list(pool.map(log, range(8)))
    assert len(set(rels)) == 8
    bodies = {vault.read(r).split("---\n\n", 1)[1].strip() for r in rels}
    assert bodies == {f"body {i}" for i in range(8)}


def test_agent_identity_from_env(vault, monkeypatch):
    monkeypatch.setenv("TESSERACT_AGENT_NAME", "donna")
    rel = notes.log_session(vault, "Who wrote this", "x", "p", [])
    assert "agent: donna" in vault.read(rel)


def test_default_agent_identity_is_unchanged(vault, monkeypatch):
    monkeypatch.delenv("TESSERACT_AGENT_NAME", raising=False)
    rel = notes.log_session(vault, "Default identity", "x", "p", [])
    assert "agent: claude" in vault.read(rel)


def test_concept_update_is_attributed_to_the_extending_agent(vault, monkeypatch):
    notes.upsert_concept(vault, "Shared idea", "first")
    monkeypatch.setenv("TESSERACT_AGENT_NAME", "researcher")
    rel = notes.upsert_concept(vault, "Shared idea", "second")
    text = vault.read(rel)
    assert "(researcher)" in text and "second" in text and "first" in text


def test_concurrent_concept_creation_merges_instead_of_losing(vault):
    def up(i):
        return notes.upsert_concept(vault, "Race concept", f"from {i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        rels = set(pool.map(up, range(8)))
    assert len(rels) == 1
    text = vault.read(rels.pop())
    assert all(f"from {i}" in text for i in range(8))
