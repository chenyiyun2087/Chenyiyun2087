#!/bin/bash

PROJECT_DIR="/Users/chenyiyun/PycharmProjects/Chenyiyun2087"

cd "$PROJECT_DIR" || exit

echo "Standalone scheduler.py is retired."
echo "Production scheduling now runs inside the Web task center from task_registry/pipeline.yaml."
echo "Starting Web console instead..."
bash "$PROJECT_DIR/start_web_console.sh"
