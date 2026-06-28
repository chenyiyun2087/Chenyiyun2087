#!/bin/zsh
set -e

PROJECT_DIR="/Users/chenyiyun/PycharmProjects/Chenyiyun2087"
PORT="${1:-5001}"

echo "Stopping Chenyiyun2087 web console on port ${PORT}..."
pkill -f "flask --app web.app run --host 0.0.0.0 --port ${PORT}" 2>/dev/null || true

sleep 1

echo "Starting Chenyiyun2087 web console on port ${PORT}..."
bash "$PROJECT_DIR/start_web_console.sh" "$PORT"

echo "Chenyiyun2087 restarted."
