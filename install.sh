#!/bin/sh
# WebCLIBridge installer
# Usage: curl -fsSL https://raw.githubusercontent.com/ikangai/web2cli/main/install.sh | sh
# See: https://github.com/ikangai/web2cli
set -eu

REPO="ikangai/web2cli"
APP_NAME="WebCLIBridge"
ASSET_NAME="${APP_NAME}.app.zip"
INSTALL_DIR="/Applications"
TARGET="${INSTALL_DIR}/${APP_NAME}.app"

if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
  BOLD="$(tput bold)"
  RED="$(tput setaf 1)"
  GREEN="$(tput setaf 2)"
  RESET="$(tput sgr0)"
else
  BOLD=""; RED=""; GREEN=""; RESET=""
fi

info() { printf '%s\n' "$*"; }
ok()   { printf '%s%s%s\n' "$GREEN" "$*" "$RESET"; }
err()  { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || err "macOS only (detected: $(uname -s))"

for tool in curl unzip xattr mktemp open sudo; do
  command -v "$tool" >/dev/null 2>&1 || err "missing required tool: $tool"
done

# Reconnect stdin to terminal so sudo can prompt (curl|sh occupies stdin).
if [ ! -t 0 ] && [ -r /dev/tty ]; then
  exec </dev/tty
fi

info "${BOLD}Installing ${APP_NAME}${RESET}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

info "Looking up latest release..."
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
HTTP_CODE="$(curl -sSL -o "$TMP/release.json" -w '%{http_code}' "$API_URL" || echo "000")"
case "$HTTP_CODE" in
  200) ;;
  404) err "no release published yet for ${REPO}. See https://github.com/${REPO}/releases" ;;
  *)   err "could not fetch ${API_URL} (HTTP ${HTTP_CODE})" ;;
esac
RELEASE_JSON="$(cat "$TMP/release.json")"

VERSION="$(printf '%s' "$RELEASE_JSON" | grep '"tag_name"' | head -1 | sed -E 's/.*"tag_name"[^"]*"([^"]+)".*/\1/')"
[ -n "$VERSION" ] || err "release JSON has no tag_name. See https://github.com/${REPO}/releases"

DOWNLOAD_URL="$(printf '%s' "$RELEASE_JSON" | grep '"browser_download_url"' | grep "$ASSET_NAME" | head -1 | sed -E 's/.*"(https[^"]+)".*/\1/')"
[ -n "$DOWNLOAD_URL" ] || err "release ${VERSION} has no ${ASSET_NAME} asset"

info "Found ${VERSION}"

info "Downloading..."
curl -fsSL "$DOWNLOAD_URL" -o "$TMP/$ASSET_NAME" || err "download failed"

info "Extracting..."
unzip -q "$TMP/$ASSET_NAME" -d "$TMP" || err "unzip failed"
[ -d "$TMP/${APP_NAME}.app" ] || err "unexpected archive contents (no ${APP_NAME}.app at top level)"

if [ -e "$TARGET" ]; then
  info "Existing install found, replacing..."
  osascript -e "tell application \"${APP_NAME}\" to quit" 2>/dev/null || true
  sleep 1
fi

info "Installing to ${TARGET} (sudo may prompt for your password)..."
[ ! -e "$TARGET" ] || sudo rm -rf "$TARGET"
sudo mv "$TMP/${APP_NAME}.app" "$INSTALL_DIR/"

sudo xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true

info "Launching..."
open -a "$APP_NAME" || err "installed but could not launch — open it from Spotlight"

ok "Installed ${APP_NAME} ${VERSION}. Look for ⚡ in your menu bar."
