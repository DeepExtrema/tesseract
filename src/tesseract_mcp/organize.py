"""CLI sweep: python -m tesseract_mcp.organize <vault> [--dry-run]

Default APPLIES moves (this is the scheduled autonomous path). The FIRST
live run against a real vault must be --dry-run, reviewed by a human — see
the design spec and README.
"""

from __future__ import annotations

import argparse
import json

from contextlib import nullcontext

from .indexer import state_dir
from .locking import caretaker_lock
from .organizer import run_sweep
from .vault import Vault


def main() -> None:
    parser = argparse.ArgumentParser(description="Organize the vault by neighbor vote.")
    parser.add_argument("vault", help="Path to the vault root")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report without moving anything")
    args = parser.parse_args()
    vault = Vault(args.vault)
    lock = nullcontext() if args.dry_run else caretaker_lock(state_dir(vault.root))
    with lock:
        report = run_sweep(vault, apply=not args.dry_run)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
