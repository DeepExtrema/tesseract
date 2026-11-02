"""Structured note operations for the Claude/ subtree."""

from __future__ import annotations

import os
import re
from datetime import datetime

import yaml

from .vault import NoteExists, Vault

AGENT_NAME = "claude"
AGENT_ENV = "TESSERACT_AGENT_NAME"
_ILLEGAL = re.compile(r'[\\/:*?"<>|\[\]#^]')
_WHITESPACE = re.compile(r"\s+")


def safe_filename(title: str) -> str:
    cleaned = _ILLEGAL.sub("", title)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned or "untitled"


def agent_name() -> str:
    """Identity stamped on notes this server instance writes.

    Each agent runs its own server instance, so identity is per instance:
    set TESSERACT_AGENT_NAME and every note it creates is attributed to that
    agent instead of the default "claude"."""
    name = os.environ.get(AGENT_ENV, "").strip()
    return safe_filename(name) if name else AGENT_NAME


def make_frontmatter(
    *,
    project: str = "",
    tags: list[str] | None = None,
    agent: str | None = None,
    created: datetime | None = None,
) -> str:
    created = created or datetime.now()
    meta = {
        "created": created.strftime("%Y-%m-%d %H:%M"),
        "agent": agent or agent_name(),
        "project": project,
        "tags": tags or [],
    }
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n"


def log_session(
    vault: Vault,
    title: str,
    content: str,
    project: str,
    tags: list[str],
    now: datetime | None = None,
) -> str:
    now = now or datetime.now()
    base = f"{now:%Y-%m-%d} {safe_filename(title)}"
    stem, n = base, 2
    while True:
        rel = f"Claude/Sessions/{stem}.md"
        try:
            # Create-only and atomic: if another agent logs the same title at
            # the same moment, one of us gets the next free suffix instead of
            # the earlier note being overwritten.
            vault.write(
                rel,
                make_frontmatter(project=project, tags=tags, created=now)
                + content
                + "\n",
            )
            break
        except NoteExists:
            stem = f"{base} {n}"
            n += 1
    vault.append("Claude/Index.md", f"- [[{stem}]] — {project}: {title}\n")
    return rel


def capture(vault: Vault, content: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    rel = f"Claude/Inbox/{now:%Y-%m-%d}.md"
    vault.append(rel, f"- {now:%H:%M} {content}\n")
    return rel


def _find_concept(vault: Vault, filename: str) -> str | None:
    folder = vault.resolve("Claude/Concepts")
    if folder.is_dir():
        target = filename.casefold()
        for p in folder.iterdir():
            if p.name.casefold() == target:
                return f"Claude/Concepts/{p.name}"
    return None


def upsert_concept(
    vault: Vault, name: str, content: str, now: datetime | None = None
) -> str:
    now = now or datetime.now()
    filename = f"{safe_filename(name)}.md"
    existing = _find_concept(vault, filename)
    if existing is None:
        rel = f"Claude/Concepts/{filename}"
        try:
            vault.write(
                rel,
                make_frontmatter(tags=["concept"], created=now)
                + f"# {name}\n\n{content}\n",
            )
            return rel
        except NoteExists:
            # Another agent created the concept between our lookup and our
            # write: extend theirs rather than fail or overwrite it.
            return upsert_concept(vault, name, content, now)
    by = agent_name()
    heading = f"## Update {now:%Y-%m-%d}" + (f" ({by})" if by != AGENT_NAME else "")
    vault.append(existing, f"\n{heading}\n\n{content}\n")
    return existing
