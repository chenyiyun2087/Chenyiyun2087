#!/bin/bash
set -euo pipefail

LABEL="com.chenyiyun.web-console"
DOMAIN="gui/$(id -u)"

launchctl print "$DOMAIN/$LABEL" >/dev/null
lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:5001/ >/dev/null
echo "PASS: launchd service and Web console are healthy on port 5001"
