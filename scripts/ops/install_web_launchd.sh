#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd -P)"
LABELS=("com.chenyiyun.web-console" "com.chenyiyun.task-worker")
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
for LABEL in "${LABELS[@]}"; do
  SOURCE_PLIST="$PROJECT_DIR/scripts/ops/$LABEL.plist"
  TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  cp "$SOURCE_PLIST" "$TARGET_PLIST"
  plutil -lint "$TARGET_PLIST"
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  # launchd may briefly retain the old label after bootout. A bounded retry
  # avoids leaving production without either service during an upgrade.
  if ! launchctl bootstrap "$DOMAIN" "$TARGET_PLIST"; then
    sleep 2
    launchctl bootstrap "$DOMAIN" "$TARGET_PLIST"
  fi
  launchctl enable "$DOMAIN/$LABEL"
  launchctl kickstart -k "$DOMAIN/$LABEL"
  echo "Installed and started $LABEL"
done
