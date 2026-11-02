"""Graph deletion & orphaned-entity cleanup.

Make graph state converge to the facts that still exist: retract mentions of
deleted notes, propose retirement of unsupported entities, repair dangling
relations and merge-stub chains, prune consolidation caches. The librarian
sweep only reports pending repairs unless auto-repair is opted in
(`librarian --auto-cleanup` / TESSERACT_AUTO_CLEANUP=1): the original
Librarian plan made health findings report-only. `--apply-repairs` here
applies them by hand. Retiring an entity note is always propose-only,
applied via `--apply-retirements`. See
docs/superpowers/specs/2026-07-13-graph-deletion-cleanup-design.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import yaml

from . import cache
from . import indexer
from .graphstore import (
    GRAPH_ROOT,
    MENTIONS_HEADER,
    RELATION_LINE,
    RELATIONS_HEADER,
    GraphStore,
    entity_summary,
    resolve_redirect,
    section_lines,
)
from .search import parse_frontmatter
from .vault import Vault, VaultError

MAX_RETRACTIONS_PER_SWEEP = 100
MAX_RELATION_FIXES_PER_SWEEP = 200
MAX_PENDING_RETIREMENTS = 200
# A vault that is only partly present (unsynced device, iCloud-evicted
# files, a privacy prompt the scheduler never answered) reads as a mass
# deletion. Above this share of tracked notes, retraction refuses to run.
MASS_DELETION_MIN_NOTES = 20
MASS_DELETION_MAX_FRACTION = 0.10


def deleted_notes(vault: Vault) -> list[str]:
    """Notes the manifest tracks (hashes or failure ledger) that no longer
    exist on disk. Organizer moves never appear here: mover.move_note
    transfers manifest entries on move."""
    manifest = indexer.load_manifest(vault.root)
    tracked = set(manifest["hashes"]) | set(manifest["failures"])
    return sorted(tracked - set(indexer.scan_notes(vault)))


def _mentioning_entities(vault: Vault, note_rel: str) -> list[str]:
    """Entity paths (no .md) whose notes hold a mention of note_rel. Prefers
    the cache; falls back to a markdown scan when the DB is missing."""
    db = indexer.db_path(vault.root)
    if db.exists():
        return cache.note_entity_paths(db, note_rel)
    target = note_rel[:-3] if note_rel.endswith(".md") else note_rel
    marker = f"[[{target}|"
    graph_dir = vault.resolve(GRAPH_ROOT)
    if not graph_dir.is_dir():
        return []
    return sorted(
        "/".join(p.relative_to(vault.root).parts)[:-3]
        for p in graph_dir.rglob("*.md")
        if marker in p.read_text(encoding="utf-8", errors="ignore")
    )


def suspect_mass_deletion(vault: Vault, deleted: list[str] | None = None) -> bool:
    """True when so many tracked notes are missing that the vault is more
    likely partly absent than really pruned."""
    if deleted is None:
        deleted = deleted_notes(vault)
    manifest = indexer.load_manifest(vault.root)
    tracked = len(set(manifest["hashes"]) | set(manifest["failures"]))
    return (len(deleted) >= MASS_DELETION_MIN_NOTES
            and len(deleted) > MASS_DELETION_MAX_FRACTION * tracked)


def retract_deleted(
    vault: Vault, limit: int = MAX_RETRACTIONS_PER_SWEEP, force: bool = False
) -> dict:
    """Retract mentions of deleted-but-tracked notes and prune their manifest
    entries, bounded per sweep. Refuses (changes nothing) on a suspected
    mass deletion unless force=True."""
    deleted = deleted_notes(vault)
    if not force and suspect_mass_deletion(vault, deleted):
        return {"retracted_notes": 0, "removed_mentions": 0,
                "remaining": len(deleted), "suspect_mass_deletion": True}
    todo = deleted[:limit]
    store = GraphStore(vault)
    removed = 0
    manifest = indexer.load_manifest(vault.root)
    for rel in todo:
        for entity_path in _mentioning_entities(vault, rel):
            try:
                if store.remove_mention(entity_path + ".md", rel):
                    removed += 1
            except VaultError:
                continue  # entity note deleted/renamed by hand
        manifest["hashes"].pop(rel, None)
        manifest["failures"].pop(rel, None)
    if todo:
        indexer.save_manifest(manifest, vault.root)
    return {"retracted_notes": len(todo), "removed_mentions": removed,
            "remaining": len(deleted) - len(todo)}


def retire_note(vault: Vault, rel: str, now: datetime, reason: str) -> None:
    """Replace an entity note with a retired tombstone. Aliases stay in the
    frontmatter and the summary stays in the body for audit/revival."""
    text = vault.read(rel)
    meta = parse_frontmatter(text)
    meta["retired"] = now.strftime("%Y-%m-%d %H:%M")
    summary = entity_summary(text)
    stem = rel.rsplit("/", 1)[-1][:-3]
    body = (f"# {stem}\n\n"
            + (f"{summary}\n\n" if summary else "")
            + f"Retired: {reason}.\n")
    fm = "---\n" + yaml.safe_dump(meta, sort_keys=False,
                                  default_flow_style=None) + "---\n\n"
    vault.write(rel, fm + body, overwrite=True)


def _target_status(vault: Vault, path: str) -> tuple[str, str | None]:
    """('live', None) | ('stub', canonical-or-None) | ('gone', None)."""
    try:
        p = vault.resolve(path + ".md")
    except VaultError:
        return "gone", None
    if not p.is_file():
        return "gone", None
    meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
    if meta.get("retired"):
        return "gone", None
    if meta.get("merged_into"):
        return "stub", resolve_redirect(vault, path)
    return "live", None


def repair_relations(
    vault: Vault, limit: int = MAX_RELATION_FIXES_PER_SWEEP, dry: bool = False
) -> dict:
    """Rewrite relation lines whose target is a merge stub to the final
    canonical; drop lines whose target is retired or missing. Bounded.
    dry=True counts the repairs without writing."""
    graph_dir = vault.resolve(GRAPH_ROOT)
    fixed = removed = 0
    if not graph_dir.is_dir():
        return {"fixed": 0, "removed": 0}
    # targets repeat heavily across the graph's relation lines; repair never
    # touches a target's own frontmatter, so one status per target per pass
    status_cache: dict[str, tuple[str, str | None]] = {}
    for p in sorted(graph_dir.rglob("*.md")):
        if fixed + removed >= limit:
            break
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        if meta.get("merged_into") or meta.get("retired"):
            continue
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        changed = False
        for line in lines:
            m = RELATION_LINE.match(line.strip())
            if not m or fixed + removed >= limit:
                out.append(line)
                continue
            target = m.group(2).strip()
            if target not in status_cache:
                status_cache[target] = _target_status(vault, target)
            status, canonical = status_cache[target]
            if status == "live":
                out.append(line)
                continue
            changed = True
            if status == "stub" and canonical:
                stem = canonical.rsplit("/", 1)[-1]
                new = f"- {m.group(1)} [[{canonical}|{stem}]]\n"
                if new in out or new in lines:
                    removed += 1  # canonical relation already present
                else:
                    out.append(new)
                    fixed += 1
            else:
                removed += 1
        if changed and not dry:
            rel = "/".join(p.relative_to(vault.root).parts)
            vault.write(rel, "".join(out), overwrite=True)
    return {"fixed": fixed, "removed": removed}


def flatten_stubs(vault: Vault, now: datetime, dry: bool = False) -> dict:
    """Point stub chains at the final canonical; retire dead-end stubs
    (target missing, retired, or a cycle). dry=True counts only."""
    graph_dir = vault.resolve(GRAPH_ROOT)
    flattened = retired = 0
    if not graph_dir.is_dir():
        return {"flattened": 0, "retired_stubs": 0}
    for p in sorted(graph_dir.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        if not meta.get("merged_into") or meta.get("retired"):
            continue
        target = str(meta["merged_into"])
        status, _ = _target_status(vault, target)
        if status == "live":
            continue
        rel = "/".join(p.relative_to(vault.root).parts)
        final = resolve_redirect(vault, target)
        if dry:
            if final:
                flattened += 1
            else:
                retired += 1
            continue
        if final:
            meta["merged_into"] = final
            stem = final.rsplit("/", 1)[-1]
            fm = "---\n" + yaml.safe_dump(meta, sort_keys=False,
                                          default_flow_style=None) + "---\n\n"
            vault.write(rel, fm + f"# {p.stem}\n\nMerged into [[{stem}]].\n",
                        overwrite=True)
            flattened += 1
        else:
            retire_note(vault, rel, now, reason="merge redirect target gone")
            retired += 1
    return {"flattened": flattened, "retired_stubs": retired}


def find_orphans(vault: Vault) -> list[dict]:
    """Live entities with no mentions, no outbound and no inbound relations.
    One markdown pass — the notes are the source of truth, not the DB.
    Relation-only entities (endpoints created by graphstore.apply without a
    mention) are supported by their inbound edge and are NOT orphans."""
    graph_dir = vault.resolve(GRAPH_ROOT)
    if not graph_dir.is_dir():
        return []
    candidates: list[dict] = []
    inbound: set[str] = set()
    for p in sorted(graph_dir.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        rel_lines = section_lines(text, RELATIONS_HEADER)
        for line in rel_lines:
            m = RELATION_LINE.match(line.strip())
            if m:
                inbound.add(m.group(2).strip())
        if meta.get("merged_into") or meta.get("retired"):
            continue
        candidates.append(
            {"path": "/".join(p.relative_to(vault.root).parts)[:-3],
             "name": p.stem,
             "type": str(meta.get("entity") or "topic"),
             "supported": bool(
                 section_lines(text, MENTIONS_HEADER) or rel_lines)})
    return [
        {"path": c["path"], "name": c["name"], "type": c["type"],
         "reason": "orphaned: no mentions or relations"}
        for c in candidates
        if not c["supported"] and c["path"] not in inbound
    ]


def update_retirement_proposals(
    block: dict, orphans: list[dict], limit: int = MAX_PENDING_RETIREMENTS
) -> list[dict]:
    """Self-healing pending list: drop entries no longer orphaned, add new
    orphans, cap the total. Mutates block["pending_retirements"]."""
    current = {o["path"] for o in orphans}
    pending = [p for p in (block.get("pending_retirements") or [])
               if p["path"] in current]
    have = {p["path"] for p in pending}
    for o in orphans:
        if len(pending) >= limit:
            break
        if o["path"] not in have:
            pending.append(o)
    block["pending_retirements"] = pending
    return pending


def apply_retirements(
    vault: Vault, paths: list[str] | None = None, now: datetime | None = None
) -> dict:
    """Retire CURRENTLY-orphaned entities (orphanhood recomputed — stale
    proposals are never trusted), optionally filtered to paths."""
    now = now or datetime.now()
    orphans = find_orphans(vault)
    if paths is not None:
        wanted = set(paths)
        orphans = [o for o in orphans if o["path"] in wanted]
    for o in orphans:
        retire_note(vault, o["path"] + ".md", now,
                    reason="orphaned — no mentions or relations")
    if orphans:
        cache.rebuild(vault, indexer.db_path(vault.root))
    return {"retired": sorted(o["path"] for o in orphans)}


def prune_checked_hash(con: dict, live_paths: set[str]) -> int:
    """Drop consolidation checked_hash entries for vanished entities.
    Mutates con in place; the caller owns persisting the state."""
    checked = con.get("checked_hash") or {}
    stale = [k for k in checked if k not in live_paths]
    for k in stale:
        del checked[k]
    if stale:
        con["checked_hash"] = checked
    return len(stale)


def apply_repairs(vault: Vault, now: datetime | None = None,
                  force: bool = False) -> dict:
    """The mechanical repairs, applied by hand: retract mentions of deleted
    notes, fix dangling relations, flatten stub chains."""
    now = now or datetime.now()
    ret = retract_deleted(vault, force=force)
    rel = repair_relations(vault)
    stubs = flatten_stubs(vault, now)
    if (ret["removed_mentions"] or ret["retracted_notes"] or rel["fixed"]
            or rel["removed"] or stubs["flattened"] or stubs["retired_stubs"]):
        cache.rebuild(vault, indexer.db_path(vault.root))
    return {"retract": ret, "relations": rel, "stubs": stubs}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Graph deletion & orphaned-entity cleanup.")
    parser.add_argument("vault", help="Path to the Obsidian vault root")
    parser.add_argument("--apply-retirements", action="store_true",
                        help="retire currently-orphaned entities "
                             "(default: report only)")
    parser.add_argument("--paths", nargs="*", default=None,
                        help="restrict retirement to these entity paths")
    parser.add_argument("--apply-repairs", action="store_true",
                        help="retract mentions of deleted notes, fix dangling "
                             "relations and stub chains (default: report only)")
    parser.add_argument("--force", action="store_true",
                        help="with --apply-repairs: retract even when the "
                             "missing-note count looks like a mass deletion")
    args = parser.parse_args()
    vault = Vault(args.vault)
    if args.apply_repairs:
        print(json.dumps(apply_repairs(vault, force=args.force), indent=2))
        return
    if args.apply_retirements:
        print(json.dumps(apply_retirements(vault, paths=args.paths), indent=2))
        return
    print(json.dumps({"deleted_notes": deleted_notes(vault),
                      "orphans": find_orphans(vault)}, indent=2))


if __name__ == "__main__":
    main()
