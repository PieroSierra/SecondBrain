#!/usr/bin/env bash
# Generate Resources/AppIcon.icns from the dashboard's brain logo.
# Best-effort: build.sh still produces a runnable app if this is skipped.
set -euo pipefail

cd "$(dirname "$0")/.."   # macos-app/

SRC="../dashboard/logo.png"
if [ ! -f "$SRC" ]; then
  echo "make-icon: source logo not found at $SRC — skipping icon." >&2
  exit 0
fi

ICONSET="build/AppIcon.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET" Resources

# Standard slots iconutil expects: <name> <pixel size>.
gen() { sips -z "$2" "$2" "$SRC" --out "$ICONSET/$1" >/dev/null; }
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
