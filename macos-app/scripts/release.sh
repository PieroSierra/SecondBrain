#!/usr/bin/env bash
# Build → sign (Developer ID) → notarize → staple → zip a distributable app.
#
# Prerequisites (one-time, done under your Apple Developer account):
#   1. A "Developer ID Application" certificate in your login keychain.
#        Xcode → Settings → Accounts → (your team) → Manage Certificates →
#        + → "Developer ID Application".  (Requires a paid membership; only the
#        Account Holder can create it.)
#   2. Stored notarization credentials as a keychain profile:
#        xcrun notarytool store-credentials "SecondBrain-Notary" \
#          --apple-id "you@example.com" --team-id "YOURTEAMID" \
#          --password "app-specific-password"   # from appleid.apple.com
#
# Usage:
#   DEV_ID="Developer ID Application: Your Name (TEAMID)" ./scripts/release.sh
#   # optional: NOTARY_PROFILE=SecondBrain-Notary (default shown)
set -euo pipefail

cd "$(dirname "$0")/.."   # macos-app/

: "${DEV_ID:?Set DEV_ID to your 'Developer ID Application: Name (TEAMID)' identity (see: security find-identity -v -p codesigning)}"
NOTARY_PROFILE="${NOTARY_PROFILE:-SecondBrain-Notary}"

APP="build/SecondBrain.app"
ZIP="build/SecondBrain.zip"

echo "==> Building (ad-hoc), then re-signing with Developer ID"
./scripts/build.sh >/dev/null

echo "==> Signing: $DEV_ID"
# Hardened runtime (--options runtime) and a secure timestamp are both required
# for notarization. The app has no nested code, so one signature covers it.
codesign --force --options runtime --timestamp --sign "$DEV_ID" "$APP"
codesign --verify --strict --verbose=2 "$APP"

echo "==> Zipping for notarization"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Notarizing (waits for Apple; usually 1–5 min)"
xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait

echo "==> Stapling the ticket onto the app"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

echo "==> Gatekeeper assessment (expect: accepted / Notarized Developer ID)"
spctl -a -vvv "$APP" || true

echo "==> Re-zipping the STAPLED app for distribution"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo ""
echo "Distributable: $(cd "$(dirname "$ZIP")" && pwd)/$(basename "$ZIP")"
echo "Recipients can double-click to run — no right-click, no quarantine prompt."
