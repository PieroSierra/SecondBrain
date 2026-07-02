#!/usr/bin/env bash
# Generate Resources/AppIcon.icns from the dashboard's brain logo.
# The dashboard logo is a TRANSPARENT brain (correct for the cream web header),
# but a transparent app icon renders on a gray plate in the Dock. So for the app
# icon we composite the brain onto an opaque WHITE rounded tile. dashboard/logo.png
# is never modified. Best-effort: build.sh still produces a runnable app if this
# is skipped or the compositor is unavailable.
set -euo pipefail

cd "$(dirname "$0")/.."   # macos-app/

SRC="../dashboard/logo.png"
if [ ! -f "$SRC" ]; then
  echo "make-icon: source logo not found at $SRC — skipping icon." >&2
  exit 0
fi

ICONSET="build/AppIcon.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET" Resources build

# Build a white-tile master: scale the brain to leave padding, then composite it
# onto a 1024 white rounded-rect tile. Fall back to the raw (transparent) logo if
# either step fails, so the build never breaks over the icon.
ICON_SRC="$SRC"
BRAIN="build/icon-brain.png"   # brain scaled down (padding) on a transparent canvas
MASTER="build/icon-src.png"    # 1024 white rounded tile + centered brain
if sips -z 820 820 "$SRC" --out "$BRAIN" >/dev/null 2>&1 \
   && python3 scripts/compose-icon.py "$BRAIN" "$MASTER" 1024 >/dev/null 2>&1; then
  ICON_SRC="$MASTER"
else
  echo "make-icon: white-tile compositor unavailable — using transparent logo." >&2
fi

# Standard slots iconutil expects: <name> <pixel size>.
gen() { sips -z "$2" "$2" "$ICON_SRC" --out "$ICONSET/$1" >/dev/null; }
gen icon_16x16.png        16
gen icon_16x16@2x.png     32
gen icon_32x32.png        32
gen icon_32x32@2x.png     64
gen icon_128x128.png      128
gen icon_128x128@2x.png   256
gen icon_256x256.png      256
gen icon_256x256@2x.png   512
gen icon_512x512.png      512
gen icon_512x512@2x.png   1024

iconutil -c icns "$ICONSET" -o Resources/AppIcon.icns
echo "make-icon: wrote Resources/AppIcon.icns"
