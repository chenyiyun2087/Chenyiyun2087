#!/usr/bin/env python3
"""VLS Frozen OOS Validation — time-split validation on truly unseen data.

Per the 2026-08-03 evaluation (P0: "用真正未见数据验证VLS"), the frozen
champion config (vls_mom_contrarian_v1_frozen, TopN=10 / h20 / buffer 0.10 /
band 0.0) is validated on data windows that were NEVER used for parameter
selection:

    2018-2021  training / factor-direction confirmation (NO re-tuning)
    2022       validation
    2023       first out-of-sample
    2024       crisis stress (small-cap liquidity shock)
    2025-2026  final blind test (no parameter changes permitted)

Pipeline stages (each can be run alone for resume):

  stage1  adapter   — normalize the PIT release into a QUALIFIED source
                      manifest (pit_data_adapter.py)
  stage2  panel     — build the long-horizon factor panel
                      (pit_factor_panel_builder.py)
  stage3  scores    — frozen VLS scores from the frozen strategy definition
                      (build_formal_scores.py)
  stage4  runs      — strict-ledger account backtests per time split
                      (research_trusted_strategy_account_backtest.py,
                      --force-strict-ledger --require-verified-evidence)
  stage5  report    — aggregate per-period metrics + benchmarks into
                      reports/vls_oos_validation_YYYYMMDD.md

Usage:
  python scripts/research/run_vls_oos_validation.py \
      --release-dir data/pit/releases/20260803_test \
      --strategy-def config/strategy_definitions/vls_mom_contrarian_v1_frozen.yaml \
      --output-root exports/formal_evidence/vls_oos \
      [--stages adapter,panel,scores,runs,report]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Frozen champion parameters (P0 freeze 2026-08-03 — do not edit).
TOP_N = 10
MAX_POSITIONS = 10
HOLD_DAYS = 20
SCORE_BUFFER = 0.10
DRIFT_BAND = 0.0
COST_RATE = 0.00075
SLIPPAGE_BPS = 10
INITIAL_CASH = 500_000.0

# Time splits: (label, start, end) — 2025-2026 is the blind test.
# v5.3: 2018-2019 is NOT available in this database — dwd_stock_label_daily
# (universe) and dws_fina_pit_daily (financial statements) both start
# 2020-01-02/03 (verified 2026-08-03; the old "2018+ data exists" claim held
# only for dwd_stock_daily_standard / ods_index_daily / ods_dividend).
# dws_fina_pit_daily then RAMPS during 2020-01..2020-04 (coverage 0.4% ->
# 95% by 2020-04-30, verified); the panel builder's coverage_ready_date
# defines the PIT-complete core start.  The first split is pre-tuning
# history rather than a 2018-2021 training window.
TIME_SPLITS = [
    ("pre_history_2020_2021", "2020-04-30", "2021-12-31"),
    ("validation_2022", "2022-01-01", "2022-12-31"),
    ("oos1_2023", "2023-01-01", "2023-12-31"),
    ("crisis_2024", "2024-01-01", "2024-12-31"),
    ("blind_2025_2026", "2025-01-01", "2026-07-31"),
]

PY = "/opt/homebrew/opt/python@3.14/bin/python3.14"


def _run(cmd: list[str], label: str) -> None:
    print(f"\n=== {label} ===")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    print(result.stdout[-4000:])
    if result.returncode != 0:
        print(result.stderr[-2000:])
        raise RuntimeError(f"stage failed: {label} (exit {result.returncode})")


def stage_adapter(release_dir: Path, work_dir: Path) -> dict:
    """Build adapter_config.json for the release and run the adapter."""
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    config = {
        "adapter_type": "FILE",
        "evidence_origin": "HISTORICAL_REAL",
        "require_canonical_sources": True,
        "snapshot_token": manifest.get("gtid", ""),
        "snapshot_id": manifest.get("gtid", ""),
        "field_definition_hash": manifest.get("semantic_contract_sha256", ""),
        "provider": "mysql_tushare_stock",
        "release": f"v5.3_{release_dir.name}",
        "retrieved_at": manifest.get("snapshot_started_at", ""),
        "schema_semantic_version": "ashare_pit_semantics_v1",
        "evidence_attestation": {
            "database": "chenyiyun",
            "gtid": manifest.get("gtid", ""),
            "data_source_version": "tushare_stock.mysql_pit_v1",
            "revision_chain_proof": "DATA_E1_frozen_snapshot",
            "availability_time_proof": "DATA_E1_frozen_snapshot",
        },
        # v5.3: REAL booleans — the adapter's completeness check is
        # `explicit is not True` (strict identity); JSON strings "True" never
        # pass and caused source_completeness_missing blockers.
        "source_completeness": {
            "corporate_actions": True,
            "security_lifecycle": True,
        },
        "sources": {
            family: {
                "path": str(release_dir / info["filename"]),
                # v5.3: per-source version required by the adapter's
                # source_version_missing check — derived from the manifest's
                # per-family content sha256 (immutable release evidence).
                "version": f"mysql_pit_v1-{str(info.get('sha256', ''))[:12]}",
            }
            for family, info in manifest.get("families", {}).items()
            if info.get("status") == "EXTRACTED"
        },
    }
    config_dir = work_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "adapter_config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
    adapter_dir = work_dir / "adapter"
    _run(
        [PY, "scripts/research/pit_data_adapter.py",
         "--config", str(config_path), "--output-dir", str(adapter_dir)],
        "stage1: pit_data_adapter",
    )
    return {"config": config_path, "adapter_dir": adapter_dir}


def stage_panel(work_dir: Path, release_dir: Path) -> Path:
    """Build the 2018+ factor panel from the qualified manifest."""
    adapter_dir = work_dir / "adapter"
    manifest = json.loads(
        (adapter_dir / "pit_source_manifest.json").read_text(encoding="utf-8"))
    panel_dir = work_dir / "panel"
    args = [PY, "scripts/research/pit_factor_panel_builder.py"]
    # v5.3: argparse long options use dashes (--trade-calendar); underscore
    # spellings are rejected ("unrecognized arguments").
    for family, fname in [
        ("market", "market.parquet"), ("universe", "universe.parquet"),
        ("financial", "financial.parquet"), ("industry", "industry.parquet"),
        ("adjustment", "adjustment.parquet"),
        ("trade-calendar", "trade_calendar.parquet"),
        ("security-lifecycle", "security_lifecycle.parquet"),
        ("corporate-actions", "corporate_actions.parquet"),
    ]:
        args += [f"--{family}", str(release_dir / fname)]
    args += [
        "--source-manifest", str(adapter_dir / "pit_source_manifest.json"),
        # v5.3: the adapter writes pit_adapter_report.json (not
        # adapter_report.json) — passing the wrong name failed the
        # adapter_report_required_for_historical_real check.
        "--adapter-report", str(adapter_dir / "pit_adapter_report.json"),
        "--output-dir", str(panel_dir),
        "--profile", "formal_v5_0",
    ]
    _run(args, "stage2: pit_factor_panel_builder")
    return panel_dir


def stage_scores(strategy_def: Path, panel_dir: Path, work_dir: Path) -> Path:
    """Build frozen VLS scores 2018+ from the frozen strategy definition."""
    scores_dir = work_dir / "scores"
    _run(
        [PY, "scripts/research/build_formal_scores.py",
         "--strategy-definition", str(strategy_def),
         "--factor-panel", str(panel_dir / "factor_panel.parquet"),
         "--output-dir", str(scores_dir)],
        "stage3: build_formal_scores (frozen VLS)",
    )
    return scores_dir


def stage_runs(work_dir: Path, release_dir: Path, scores_dir: Path) -> list[dict]:
    """Run strict-ledger backtests per time split with frozen inputs."""
    runs_dir = work_dir / "runs"
    results = []
    for label, start, end in TIME_SPLITS:
        out = runs_dir / label
        _run(
            [PY, "scripts/research_trusted_strategy_account_backtest.py",
             "--risk-profile", "adaptive",
             "--strategies", "vls_mom_contrarian_v1_frozen",
             "--execution-mode", "strict_t1_open_precommit",
             "--start-date", start, "--end-date", end,
             "--trade-cost-rate", str(COST_RATE), "--slippage-rate", str(SLIPPAGE_BPS / 10_000),
             "--initial-cash", str(INITIAL_CASH),
             "--output-dir", str(out),
             "--scores-snapshot", str(scores_dir / "scores.csv"),
             "--top-n", str(TOP_N), "--max-total-positions", str(MAX_POSITIONS),
             "--hold-days", str(HOLD_DAYS),
             "--rebalance-score-buffer", str(SCORE_BUFFER),
             "--rebalance-weight-drift-band", str(DRIFT_BAND),
             "--require-verified-evidence", "--formal-mode", "--force-strict-ledger"],
            f"stage4: strict backtest {label}",
        )
        results.append({"split": label, "start": start, "end": end, "output": str(out)})
    return results


def stage_report(results: list[dict], output_root: Path) -> Path:
    """Aggregate per-split metrics into the OOS report."""
    rows = []
    for r in results:
        summary_path = Path(r["output"]) / "trusted_account_backtest_summary.csv"
        if not summary_path.exists():
            rows.append({"split": r["split"], "error": "summary missing"})
            continue
        import pandas as pd
        s = pd.read_csv(summary_path).iloc[0]
        rows.append({
            "split": r["split"],
            "start": r["start"], "end": r["end"],
            "total_return": s.get("total_return"),
            "annualized_return": s.get("annualized_return"),
            "max_drawdown": s.get("max_drawdown"),
            "sharpe": s.get("sharpe_ratio", s.get("sharpe")),
            "trade_count": s.get("trade_count"),
            "total_cost": s.get("total_cost"),
        })

    report_path = output_root / f"vls_oos_validation_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    lines = ["# VLS Frozen OOS Validation", "",
             f"Strategy: vls_mom_contrarian_v1_frozen (TopN={TOP_N}, hold={HOLD_DAYS}, "
             f"buffer={SCORE_BUFFER}, band={DRIFT_BAND}) — FROZEN 2026-08-03",
             "", "| Split | Period | Total | Annual | MDD | Trades | Cost |", "|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {row['split']} | {row.get('start','')}..{row.get('end','')} | "
            f"{row.get('total_return', '')} | {row.get('annualized_return', '')} | "
            f"{row.get('max_drawdown', '')} | {row.get('trade_count', '')} | "
            f"{row.get('total_cost', '')} |")
    lines += [
        "", "## Honest caveats", "",
        "- Parameter freeze is ABSOLUTE: no re-tuning on any window, including the",
        "  2022 validation split (its role is checking factor-direction stability).",
        "- 2025-2026 blind test uses real data through 2026-07-31 (ods_index_daily,",
        "  dwd_stock_daily_standard coverage verified 2018-2026).",
        "- Data tier: the 20260803_test release is DIAGNOSTIC (consistent_snapshot=",
        "  false — the local MySQL has log_bin=0). Directional OOS evidence only;",
        "  formal E3 runs require a binlog-enabled server or a relaxed contract.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n=== stage5: report written to {report_path} ===")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--strategy-def", type=Path,
                        default=PROJECT_ROOT / "config/strategy_definitions/vls_mom_contrarian_v1_frozen.yaml")
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "exports/formal_evidence/vls_oos")
    parser.add_argument("--stages", default="adapter,panel,scores,runs,report")
    args = parser.parse_args()

    stages = [s.strip() for s in args.stages.split(",")]
    work_dir = args.output_root
    work_dir.mkdir(parents=True, exist_ok=True)

    release_dir = args.release_dir.resolve()
    if not (release_dir / "manifest.json").exists():
        print(f"FATAL: no manifest.json in {release_dir}")
        return 2

    adapter_info: dict = {}
    panel_dir: Path | None = None
    scores_dir: Path | None = None
    results: list[dict] = []

    if "adapter" in stages:
        adapter_info = stage_adapter(release_dir, work_dir)
    if "panel" in stages:
        panel_dir = stage_panel(work_dir, release_dir)
    if "scores" in stages:
        if panel_dir is None:
            panel_dir = work_dir / "panel"
        if not (panel_dir / "factor_panel.parquet").exists():
            print("FATAL: factor_panel.parquet missing — run --stages panel first")
            return 2
        scores_dir = stage_scores(args.strategy_def, panel_dir, work_dir)
    if "runs" in stages:
        if scores_dir is None:
            scores_dir = work_dir / "scores"
        if not (scores_dir / "scores.csv").exists():
            print("FATAL: scores.csv missing — run --stages scores first")
            return 2
        results = stage_runs(work_dir, release_dir, scores_dir)
    if "report" in stages:
        if not results:
            results = []
            for label, start, end in TIME_SPLITS:
                results.append({"split": label, "start": start, "end": end,
                                "output": str(work_dir / "runs" / label)})
        stage_report(results, args.output_root)

    print("\nVLS_OOS_VALIDATION_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
