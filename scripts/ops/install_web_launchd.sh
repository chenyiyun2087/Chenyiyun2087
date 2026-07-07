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
  removed=0
  for attempt in 1 2 3 4 5; do
    if ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      removed=1
      break
    fi
    sleep 1
  done
  if [ "$removed" -ne 1 ]; then
    echo "FATAL: old $LABEL did not leave launchd after bounded wait" >&2
    exit 1
  fi
  # launchd may return EIO while still registering the job asynchronously.
  # After the old label is observably gone, treat the new observable job state
  # as authoritative and retry only when the label is genuinely absent.
  registered=0
  for attempt in 1 2 3 4 5; do
    launchctl bootstrap "$DOMAIN" "$TARGET_PLIST" 2>/dev/null || true
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      registered=1
      break
    fi
    sleep 1
  done
  if [ "$registered" -ne 1 ]; then
    echo "FATAL: failed to register $LABEL after bounded retries" >&2
    exit 1
  fi
  launchctl enable "$DOMAIN/$LABEL"
  launchctl kickstart -k "$DOMAIN/$LABEL"
  echo "Installed and started $LABEL"
done
