#!/bin/bash
set -euo pipefail

DOMAIN="gui/$(id -u)"
PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd -P)"

launchctl print "$DOMAIN/com.chenyiyun.web-console" | grep -q "state = running"
launchctl print "$DOMAIN/com.chenyiyun.task-worker" | grep -q "state = running"
lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:5001/ >/dev/null
manifest="${CHENYIYUN_RELEASE_MANIFEST:-$HOME/Library/Application Support/Chenyiyun2087/production_release.json}"
runtime_python="${CHENYIYUN_RUNTIME_PYTHON:-$(/usr/bin/plutil -extract runtime_python raw -o - "$manifest")}"
"$runtime_python" "$PROJECT_DIR/scripts/ops/check_worker_lease.py"
echo "PASS: Web console and dedicated task worker are healthy"
