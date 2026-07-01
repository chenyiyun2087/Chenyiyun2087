#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd -P)"
LABEL="com.chenyiyun.web-console"
SOURCE_PLIST="$PROJECT_DIR/scripts/ops/$LABEL.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="${CHENYIYUN_ENV_FILE:-$HOME/.config/chenyiyun/web.env}"
DOMAIN="gui/$(id -u)"

if [ ! -f "$ENV_FILE" ]; then
  echo "FATAL: missing $ENV_FILE" >&2
  exit 1
fi
mode="$(stat -f '%Lp' "$ENV_FILE")"
if [ "$mode" != "600" ]; then
  echo "FATAL: $ENV_FILE must have mode 600 (current: $mode)" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/Chenyiyun2087" "$PROJECT_DIR/logs/web"
cp "$SOURCE_PLIST" "$TARGET_PLIST"
plutil -lint "$TARGET_PLIST"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TARGET_PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"
echo "Installed and started $LABEL"
