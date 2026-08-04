#!/usr/bin/env bash
# Re-run the alpha v3 validation with real execution-cost evidence:
#   - build_v52_execution_cost_evidence.py (limit-up fill + freeze evidence)
#   - universe-perturbation formal reruns (scanned from exports/formal_runs)
# Requires homebrew python3.14 and clean worktree.
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${CHENYIYUN_DB_PASSWORD:?CHENYIYUN_DB_PASSWORD is required — export it from a secure source (never hardcode)}"
PY=/opt/homebrew/opt/python@3.14/bin/python3.14
RUN=exports/formal_runs/formal-cc3890152ea89790888022b5b3fc6216e0156801eaf1c3775abe22e5e32c016e
EVID=exports/formal_evidence/formal-cc3890152ea89790888022b5b3fc6216e0156801eaf1c3775abe22e5e32c016e
OUT=exports/alpha_v3_validation/v52_cc3890_dynamic_score

# 1) Build the fill/freeze evidence (idempotent, sha-bound to the run).
"$PY" scripts/research/build_v52_execution_cost_evidence.py \
  --formal-run-dir "$RUN" \
  --output "$EVID/execution_cost_evidence.json"

# 2) Re-run the validation with the evidence + perturbation reruns.
"$PY" scripts/research/run_alpha_v3_validation.py \
  --profile alpha_v3_2 \
  --strategy production_governed_vol_position_v1_2b_dynamic_score \
  --nav "$RUN/account_backtest/trusted_account_backtest_nav.csv" \
  --trades "$RUN/account_backtest/trusted_account_backtest_trades.csv" \
  --benchmark-nav "$EVID/benchmark_nav_daily.csv" \
  --factor-panel "$EVID/factor_panel_fwd.csv" \
  --factor-returns "$EVID/factor_returns.csv" \
  --pit-manifest exports/formal_admissions/2ba43f2a7f41c112e4f36e3b77719e0f32d858aae7d910283294757b79bdabb3/pr_b/pr_b_binding.json \
  --walk-forward-evidence exports/formal_oos/20260802_v52_pr_d/formal_oos_robustness.json \
  --execution-cost-evidence "$EVID/execution_cost_evidence.json" \
  --universe-perturbation-dir exports/formal_runs \
  --output-dir "$OUT"
echo "GATE_DONE"
