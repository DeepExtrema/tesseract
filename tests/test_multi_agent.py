"""Several agents writing into one vault at the same time.

Agents contribute through separate server instances that share the vault, so
the write path has to be safe without a coordinator: create-only writes are
atomic, temp files never collide, and each agent's identity is recorded."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tesseract_mcp import notes, sheets, tasks
from tesseract_mcp.vault import NoteExists, VaultError

THINGS_SCHEMA = (
    "---\nsheet: things\nfilename: \"{name}\"\nkey: [name]\n"
    "columns:\n  name: {type: string, required: true}\n"
    "  status: {type: string}\n---\n"
)


@pytest.fixture
def things(vault, vault_dir):
    (vault_dir / "Things").mkdir()
    (vault_dir / "Things" / "_schema.md").write_text(THINGS_SCHEMA, encoding="utf-8")
    return vault


def _race(n, fn):
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


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


def test_concept_collision_extends_the_note_even_when_lookup_misses(vault, monkeypatch):
    # e.g. NFC vs NFD names on macOS: the create collides but the casefold
    # lookup cannot see the file. Extend it; never recurse or overwrite.
    vault.write("Claude/Concepts/Idea.md", "# Idea\n\noriginal\n")
    monkeypatch.setattr(notes, "_find_concept", lambda *a: None)
    rel = notes.upsert_concept(vault, "Idea", "addition")
    text = vault.read(rel)
    assert rel == "Claude/Concepts/Idea.md"
    assert "original" in text and "addition" in text


def test_concurrent_appends_keep_every_line(vault):
    # capture/add_task/concept updates append. Windows' emulated "a" mode
    # (seek to end, then write) let racing agents overwrite each other's
    # lines; every backend's open_append must make appends atomic.
    def agent(i):
        for j in range(25):
            vault.append("Claude/Inbox/shared.md", f"- agent {i} line {j}\n")

    _race(16, agent)
    lines = vault.read("Claude/Inbox/shared.md").splitlines()
    assert sorted(lines) == sorted(
        f"- agent {i} line {j}" for i in range(16) for j in range(25))


def test_concurrent_first_tasks_keep_every_task(vault):
    _race(8, lambda i: tasks.add_task(vault, f"task {i}"))
    text = vault.read(tasks.TASKS_FILE)
    assert text.startswith(tasks.TASKS_SEED)
    assert all(f"- [ ] task {i}\n" in text for i in range(8))


def test_sheet_rows_take_the_instance_identity(things, monkeypatch):
    monkeypatch.setenv("TESSERACT_AGENT_NAME", "donna")
    implicit = sheets.upsert(things, "things", {"name": "A"})
    explicit = sheets.upsert(things, "things", {"name": "B"}, agent="cowork")
    assert "agent: donna" in things.read(implicit["path"])
    assert "agent: cowork" in things.read(explicit["path"])


def test_concurrent_sheet_creates_of_one_row_become_updates(things):
    results = _race(8, lambda i: sheets.upsert(
        things, "things", {"name": "Same", "status": f"s{i}"})["result"])
    assert results.count("created") == 1
    assert results.count("updated") == 7
    rows = [p.name for p in (things.root / "Things").glob("*.md")]
    assert sorted(rows) == ["Same.md", "_schema.md"]


def test_sheet_rows_sharing_a_filename_still_get_suffixed(things, monkeypatch):
    # a different row whose filename is taken is not an update target
    (things.root / "Things" / "Same.md").write_text(
        "---\nname: Other\n---\n", encoding="utf-8")
    monkeypatch.setattr(sheets, "render_filename", lambda schema, fields: "Same")
    out = sheets.upsert(things, "things", {"name": "Mine"})
    assert out == {**out, "result": "created", "path": "Things/Same 2.md"}
    assert "name: Other" in things.read("Things/Same.md")
