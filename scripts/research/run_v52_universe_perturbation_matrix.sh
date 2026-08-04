#!/usr/bin/env bash
# Formal universe-perturbation matrix: 2 drop ratios x 5 seeds on package 0bedb3cc.
# Each run is a fresh immutable formal run (perturbation is part of run_id).
# Requires clean worktree and homebrew python3.14 (pandas 3.0.0).
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${CHENYIYUN_DB_PASSWORD:?CHENYIYUN_DB_PASSWORD is required — export it from a secure source (never hardcode)}"
PY=/opt/homebrew/opt/python@3.14/bin/python3.14
PREFLIGHT=exports/formal_admissions/2ba43f2a7f41c112e4f36e3b77719e0f32d858aae7d910283294757b79bdabb3/readiness_report.json
PACKAGE=exports/formal_packages/0bedb3cc65c485b6355d00e8d786dc8a8ba690f4d51c1bb6e4695db2f51dd85c
PIT=ede3d2561effb0b7c435225c8b9396fe860cc959f9e4eb250f9a0c449fc1aa01

for ratio in 0.10 0.20; do
  for seed in 1103 2087 3141 4099 7919; do
    echo "=== universe drop ratio=$ratio seed=$seed ==="
    "$PY" scripts/research/run_immutable_formal_backtest.py \
      --preflight "$PREFLIGHT" \
      --package "$PACKAGE" \
      --output-root exports/formal_runs \
      --end-date 2024-12-31 \
      --pit-run-id "$PIT" \
      --package-id 0bedb3cc65c485b6355d00e8d786dc8a8ba690f4d51c1bb6e4695db2f51dd85c \
      --universe-drop-ratio "$ratio" \
      --universe-drop-seed "$seed" \
      > "exports/formal_runs/perturbation_${ratio}_${seed}.log" 2>&1 \
    || { echo "FAILED ratio=$ratio seed=$seed (see log)"; exit 1; }
    tail -1 "exports/formal_runs/perturbation_${ratio}_${seed}.log"
  done
done
echo "MATRIX_DONE"
