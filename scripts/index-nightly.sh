#!/bin/bash
# Nightly incremental graph index (macOS/Linux). Indexes only new/changed notes
# into Claude/Graph/. Does NOT run the organizer, so no notes are ever moved.
# Usage: scripts/index-nightly.sh <vault> [state-dir]
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="${1:?usage: index-nightly.sh <vault> [state-dir]}"
[ -n "${2:-}" ] && export TESSERACT_STATE_DIR="$2"
export TESSERACT_VAULT_PATH="$VAULT"
export TESSERACT_EXTRACTOR="${TESSERACT_EXTRACTOR:-claude}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
LOCK="${TMPDIR:-/tmp}/tesseract-index.lock"
echo "[$(date '+%F %T')] index-nightly start"
ls "$VAULT" >/dev/null 2>&1 || { echo "cannot read vault (macOS privacy permission for python/launchd?)"; exit 2; }
if ! mkdir "$LOCK" 2>/dev/null; then echo "another index run holds $LOCK; skipping"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT
if pgrep -f "tesseract_mcp.indexer" >/dev/null; then echo "indexer already running; skipping"; exit 0; fi
for i in $(seq 1 20); do
  out="$("$REPO/.venv/bin/python" -m tesseract_mcp.indexer "$VAULT" 2>&1)" || { echo "$out" | tail -5; exit 1; }
  echo "$out" | tail -12
  echo "$out" | grep -q '"remaining": 0' && break
done
echo "[$(date '+%F %T')] index-nightly done"
