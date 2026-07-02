#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
USER_ENV_FILE="${CHENYIYUN_ENV_FILE:-$HOME/.config/chenyiyun/web.env}"
LOG_DIR="logs/web"
LOG_FILE="$LOG_DIR/web_app.nohup.log"
HOST="0.0.0.0"
PORT="${1:-5001}"
RUN_MODE="${2:-background}"

cd "$PROJECT_DIR" || exit 1
mkdir -p "$LOG_DIR"
# User-level configuration is preferred so launchd can start the service
# without storing credentials in the repository or plist.
if [ -f "$USER_ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$USER_ENV_FILE"
    set +a
elif [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_DIR/.env"
    set +a
fi
if [ -z "${CHENYIYUN_DB_URL:-}" ] && [ -z "${CHENYIYUN_DB_PASSWORD:-}" ]; then
    echo "FATAL: set CHENYIYUN_DB_PASSWORD/CHENYIYUN_DB_URL in $USER_ENV_FILE or $PROJECT_DIR/.env." >&2
    exit 1
fi
export CHENYIYUN_RUNTIME_ROLE=web

if [ "$RUN_MODE" != "--foreground" ] && pgrep -f "flask --app web.app run --host $HOST --port $PORT" > /dev/null; then
    echo "Web console is already running on port $PORT."
    exit 1
fi

echo "Starting web console on $HOST:$PORT ..."
if [ "$RUN_MODE" = "--foreground" ]; then
    exec "$VENV_PYTHON" -m flask --app web.app run --host "$HOST" --port "$PORT" --no-debugger --no-reload
fi
nohup "$VENV_PYTHON" -m flask --app web.app run --host "$HOST" --port "$PORT" --no-debugger --no-reload < /dev/null > "$LOG_FILE" 2>&1 &
WEB_PID=$!
disown "$WEB_PID" 2>/dev/null || true
echo "Web console started. PID: $WEB_PID, logs: $LOG_FILE"
