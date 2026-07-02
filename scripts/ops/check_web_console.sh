#!/bin/bash
set -euo pipefail

DOMAIN="gui/$(id -u)"

launchctl print "$DOMAIN/com.chenyiyun.web-console" >/dev/null
launchctl print "$DOMAIN/com.chenyiyun.task-worker" >/dev/null
lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:5001/ >/dev/null
echo "PASS: Web console and dedicated task worker are healthy"
