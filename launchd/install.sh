#!/usr/bin/env bash
# Install (or reinstall) the bridge LaunchAgent so the dashboard survives
# quitting SecondBrain.app, logging out, and rebooting.
#
#   ./launchd/install.sh            install / reload
#   ./launchd/install.sh --uninstall  remove, back to app-owned bridge
#
# Safe to re-run. See the comments in com.secondbrain.bridge.plist for why the
# job launches through `zsh -ilc` and what adopting the bridge does to the
# app's Engine menu.
set -euo pipefail

LABEL="com.secondbrain.bridge"
SRC="$(cd "$(dirname "$0")" && pwd)/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
PORT=4173

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$DEST"
  echo "uninstalled $LABEL — the app owns the bridge again"
  exit 0
fi

# The vault is the parent of launchd/. Resolve it rather than hardcoding a path,
# so this works from any checkout location.
VAULT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$VAULT/dashboard/bridge.py" ] || {
  echo "not a SecondBrain vault: $VAULT (no dashboard/bridge.py)" >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/SecondBrain"

# Free the port first. A bridge already spawned by the app holds :4173, and
# launchd would otherwise restart-loop against "address already in use".
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "port $PORT busy — stopping the existing bridge"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t | xargs kill 2>/dev/null || true
  for _ in $(seq 1 10); do
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1 || break
    sleep 1
  done
fi

# Fill the template. sed with | as the delimiter so paths containing / are fine.
sed -e "s|__VAULT__|$VAULT|g" -e "s|__HOME__|$HOME|g" -e "s|__PORT__|$PORT|g" \
    "$SRC" > "$DEST"
plutil -lint "$DEST" >/dev/null || { echo "generated plist is invalid" >&2; exit 1; }

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$DEST"
launchctl enable "$DOMAIN/$LABEL"

for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    echo "$LABEL is up on http://127.0.0.1:$PORT"
    exit 0
  fi
  sleep 1
done

echo "bridge did not answer /healthz within 20s — check:" >&2
echo "  tail -20 $HOME/Library/Logs/SecondBrain/bridge.err.log" >&2
exit 1
