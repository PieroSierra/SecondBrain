#!/usr/bin/env bash
# Launch the Second Brain dashboard. Idempotent — kills any leftover bridge
# on the default port before starting a fresh one, so re-running this never
# fails with "Address already in use."

set -euo pipefail

# Run from the repo root regardless of where the script is invoked from.
cd "$(dirname "$0")"

PORT="${PORT:-4173}"

# Free the port if a previous bridge is still listening. Quiet on the
# happy path (no orphan = no output). lsof exits 1 when nothing matches;
# guard it so `set -e` doesn't bail on the empty case.
PIDS=$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$PIDS" ]; then
  echo "[run.sh] port $PORT busy — killing leftover process(es): $PIDS"
  # SIGTERM first; give the process a moment to release the socket cleanly.
  kill $PIDS 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    sleep 0.2
    STILL=$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
    [ -z "$STILL" ] && break
  done
  # Anything still holding the port gets SIGKILL.
  STILL=$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$STILL" ]; then
    echo "[run.sh] sending SIGKILL to: $STILL"
    kill -9 $STILL 2>/dev/null || true
    sleep 0.3
  fi
fi

exec python3 dashboard/bridge.py --port "$PORT" "$@"
