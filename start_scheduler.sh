#!/bin/bash

# 项目路径
PROJECT_DIR="/Users/chenyiyun/PycharmProjects/Chenyiyun2087"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
SCRIPT="scheduler.py"
LOG_FILE="logs/scheduler/scheduler.nohup.log"

cd "$PROJECT_DIR" || exit

# 检查是否已经在运行
if pgrep -f "$SCRIPT" > /dev/null; then
    echo "Scheduler is already running."
    exit 1
fi

echo "Starting scheduler..."
nohup "$VENV_PYTHON" "$SCRIPT" > "$LOG_FILE" 2>&1 &
echo "Scheduler started. Logs: $LOG_FILE"
