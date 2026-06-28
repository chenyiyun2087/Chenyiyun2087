#!/bin/bash

PROJECT_DIR="/Users/chenyiyun/PycharmProjects/Chenyiyun2087"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="logs/web"
LOG_FILE="$LOG_DIR/web_app.nohup.log"
HOST="0.0.0.0"
PORT="${1:-5001}"

cd "$PROJECT_DIR" || exit 1
mkdir -p "$LOG_DIR"
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_DIR/.env"
    set +a
fi
if [ -z "${CHENYIYUN_DB_URL:-}" ] && [ -z "${CHENYIYUN_DB_PASSWORD:-}" ]; then
    echo "FATAL: set CHENYIYUN_DB_PASSWORD/CHENYIYUN_DB_URL or create an ignored .env file." >&2
    exit 1
fi

if pgrep -f "flask --app web.app run --host $HOST --port $PORT" > /dev/null; then
    echo "Web console is already running on port $PORT."
    exit 1
fi

echo "Starting web console on $HOST:$PORT ..."
nohup "$VENV_PYTHON" -m flask --app web.app run --host "$HOST" --port "$PORT" --no-debugger --no-reload < /dev/null > "$LOG_FILE" 2>&1 &
WEB_PID=$!
disown "$WEB_PID" 2>/dev/null || true
echo "Web console started. PID: $WEB_PID, logs: $LOG_FILE"
