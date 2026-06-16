#!/bin/sh
# Build a drag-to-install WebCLIBridge.dmg from dist/WebCLIBridge.app.
#
# Usage:
#   python3 setup.py py2app   # build dist/WebCLIBridge.app first
#   ./build-dmg.sh            # -> dist/WebCLIBridge-<version>.dmg
#
# Produces a compressed disk image whose root holds WebCLIBridge.app next to a
# symlink to /Applications, so the user just drags the icon across. Uses only
# the system `hdiutil` — no extra tooling.
set -eu

APP_NAME="WebCLIBridge"
APP="dist/${APP_NAME}.app"

[ "$(uname -s)" = "Darwin" ] || { echo "error: macOS only (detected: $(uname -s))" >&2; exit 1; }
[ -d "$APP" ] || {
  echo "error: ${APP} not found — build it first:" >&2
  echo "       python3 -m pip install py2app rumps && python3 setup.py py2app" >&2
  exit 1
}

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || echo 0)"
VOL="${APP_NAME} ${VERSION}"
DMG="dist/${APP_NAME}-${VERSION}.dmg"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT INT TERM

# -R preserves the bundle (symlinks, perms, embedded signature). Clearing
# xattrs keeps the staged copy free of any com.apple.quarantine flag; the
# code signature lives in _CodeSignature/, not in an xattr, so it survives.
cp -R "$APP" "$STAGE/"
xattr -cr "$STAGE/${APP_NAME}.app" 2>/dev/null || true
ln -s /Applications "$STAGE/Applications"

rm -f "$DMG"
hdiutil create \
  -volname "$VOL" \
  -srcfolder "$STAGE" \
  -fs HFS+ \
  -format UDZO \
  -ov \
  "$DMG" >/dev/null

echo "Built ${DMG} ($(du -h "$DMG" | cut -f1))"
