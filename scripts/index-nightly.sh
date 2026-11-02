#!/bin/bash
# Nightly incremental graph index (macOS/Linux). Indexes only new/changed notes
# into Claude/Graph/. Does NOT run the organizer, so no notes are ever moved.
# The Librarian sweep already includes indexing: schedule this OR the
# Librarian, not both (neither takes a lock against the other).
# Usage: scripts/index-nightly.sh <vault> [state-dir]
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="${1:?usage: index-nightly.sh <vault> [state-dir]}"
[ -n "${2:-}" ] && export TESSERACT_STATE_DIR="$2"
export TESSERACT_VAULT_PATH="$VAULT"
export TESSERACT_EXTRACTOR="${TESSERACT_EXTRACTOR:-claude}"
# HF_HUB_OFFLINE is deliberately not forced: the indexer embeds notes with
# bge-micro-v2, and on a machine that never downloaded it an offline run fails
# every night. Set HF_HUB_OFFLINE=1 yourself once the model is cached.
LOCK="${TMPDIR:-/tmp}/tesseract-index.lock"
echo "[$(date '+%F %T')] index-nightly start"
ls "$VAULT" >/dev/null 2>&1 || { echo "cannot read vault (macOS privacy permission for python/launchd?)"; exit 2; }
if ! mkdir "$LOCK" 2>/dev/null; then
  # A run killed by SIGKILL, power loss or launchd never reaches the EXIT
  # trap, so its lock would block every later run. Reclaim it unless the
  # recorded PID is still an index-nightly process.
  pid="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null | grep -q 'index-nightly'; then
    echo "another index run (pid $pid) holds $LOCK; skipping"; exit 0
  fi
  echo "removing stale lock $LOCK (pid ${pid:-unknown} is not running)"
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || { echo "another run took $LOCK; skipping"; exit 0; }
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
if pgrep -f "tesseract_mcp.indexer" >/dev/null; then echo "indexer already running; skipping"; exit 0; fi
for i in $(seq 1 20); do
  out="$("$REPO/.venv/bin/python" -m tesseract_mcp.indexer "$VAULT" 2>&1)" || { echo "$out" | tail -5; exit 1; }
  echo "$out" | tail -12
  echo "$out" | grep -q '"remaining": 0' && break
done
echo "[$(date '+%F %T')] index-nightly done"
