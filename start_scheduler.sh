#!/bin/bash

PROJECT_DIR="/Users/chenyiyun/PycharmProjects/Chenyiyun2087"

cd "$PROJECT_DIR" || exit

echo "Standalone scheduler.py is retired."
echo "Production scheduling runs in the dedicated task worker from task_registry/pipeline.yaml."
echo "Install/start both launchd services with scripts/ops/install_web_launchd.sh."
bash "$PROJECT_DIR/scripts/ops/install_web_launchd.sh"
