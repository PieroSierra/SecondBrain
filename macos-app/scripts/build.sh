#!/usr/bin/env bash
# Build SecondBrain.app from the Swift sources with the command-line toolchain.
# No Xcode project required — just the Xcode command line tools (swiftc).
set -euo pipefail

cd "$(dirname "$0")/.."   # macos-app/

APP="build/SecondBrain.app"
CONTENTS="$APP/Contents"

echo "==> Cleaning"
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

echo "==> Generating icon (best-effort)"
./scripts/make-icon.sh || true

echo "==> Assembling bundle"
cp Resources/Info.plist "$CONTENTS/Info.plist"
if [ -f Resources/AppIcon.icns ]; then
  cp Resources/AppIcon.icns "$CONTENTS/Resources/AppIcon.icns"
fi

echo "==> Compiling Swift"
swiftc -O \
  -o "$CONTENTS/MacOS/SecondBrain" \
  Sources/*.swift \
  -framework AppKit \
  -framework WebKit

echo "==> Ad-hoc code signing"
# Ad-hoc signature (-) is enough to launch locally; it is NOT notarization.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || \
  echo "   (codesign skipped — app will still run)"

echo ""
echo "Built $APP"
echo "Install:  cp -R \"$APP\" /Applications/"
echo "Run:      open \"$APP\"   (first launch: right-click → Open to clear Gatekeeper)"
