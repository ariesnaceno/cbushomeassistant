#!/usr/bin/env bash
# Install or update the C-Bus custom integration into Home Assistant,
# without HACS. Run this from the Home Assistant "Terminal & SSH" add-on
# (or any shell that can see the HA /config directory).
#
#   bash <(curl -s https://raw.githubusercontent.com/ariesnaceno/cbushomeassistant/main/scripts/install.sh)
#
# or, if you've cloned the repo:  bash scripts/install.sh
#
# After it finishes, restart Home Assistant.

set -euo pipefail

REPO="https://github.com/ariesnaceno/cbushomeassistant"
# HA config dir: honour $HA_CONFIG, else the usual /config, else current dir.
CONFIG_DIR="${HA_CONFIG:-/config}"
if [ ! -d "$CONFIG_DIR" ]; then
  CONFIG_DIR="$(pwd)"
fi

DEST="$CONFIG_DIR/custom_components"
TMP="$(mktemp -d)"

echo "==> Home Assistant config dir: $CONFIG_DIR"
echo "==> Fetching latest from $REPO"
git clone --depth 1 "$REPO" "$TMP" >/dev/null 2>&1

echo "==> Installing custom_components/cbus -> $DEST/cbus"
mkdir -p "$DEST"
rm -rf "$DEST/cbus"
cp -r "$TMP/custom_components/cbus" "$DEST/cbus"

rm -rf "$TMP"

VERSION="$(grep -o '"version": *"[^"]*"' "$DEST/cbus/manifest.json" | head -1 | cut -d'"' -f4)"
echo
echo "==> Done. Installed C-Bus integration v${VERSION}."
echo "==> RESTART Home Assistant for it to take effect."
echo "    (Settings -> System -> top-right power menu -> Restart Home Assistant)"
