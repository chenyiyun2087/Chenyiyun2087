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

import yaml

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

# ── Alpha-challenger support (2026-08-04 pre-registration) ─────────────────
# A challenger config (config/alpha_challengers/<id>.yaml) overrides the
# frozen-champion defaults below: factor weights/signs (passed to
# build_formal_scores as the strategy definition), execution parameters,
# cost model, and — when present — portfolio_construction / regime_control /
# crowding_control stages.  Without --challenger-config the pipeline runs the
# frozen baseline exactly as before (backwards compatible).

CHALLENGER_MANIFEST = PROJECT_ROOT / "config" / "experiments" / "alpha_rebuild_202608.yaml"


def _load_challenger_config(path: Path) -> dict:
    """Load + validate a pre-registered challenger manifest (fail-closed)."""
    if not path.exists():
        raise FileNotFoundError(f"challenger config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = ("schema_version", "challenger_id", "experiment_id",
                "factor_weights", "factor_signs", "execution",
                "selection_window", "untouched_evaluation_window")
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"challenger config {path} missing fields: {missing}")
    return data


def _verify_challenger_preregistration(challenger: dict, config_path: Path) -> None:
    """Fail closed if the challenger file drifted from its pre-registered sha.

    The pre-registration SHA is recorded in config/experiments/alpha_rebuild
    _202608.yaml (or any sibling experiment manifest).  Drift means the file
    was edited after pre-registration — the run is BLOCKED, never silently
    executed with changed parameters.
    """
    import hashlib

    if not CHALLENGER_MANIFEST.exists():
        raise FileNotFoundError(
            f"experiment manifest not found: {CHALLENGER_MANIFEST} — "
            "pre-registration SHA verification is mandatory")
    manifest = yaml.safe_load(CHALLENGER_MANIFEST.read_text(encoding="utf-8")) or {}
    shas = manifest.get("pre_registration_shas", {})
    cid = challenger["challenger_id"]
    expected = shas.get(cid)
    if not expected:
        raise ValueError(
            f"challenger {cid} not found in {CHALLENGER_MANIFEST} "
            "pre_registration_shas — register before running")
    actual = hashlib.sha256(config_path.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"challenger {cid} DRIFTED from pre-registration sha "
            f"(expected {expected}, got {actual}) — file edited after "
            "pre-registration; run BLOCKED.")


def _challenger_execution(challenger: dict) -> dict:
    """Merge challenger execution params over frozen-champion defaults."""
    base = {
        "top_n": TOP_N, "max_positions": MAX_POSITIONS, "hold_days": HOLD_DAYS,
        "score_buffer": SCORE_BUFFER, "drift_band": DRIFT_BAND,
        "cost_rate": COST_RATE, "slippage_bps": SLIPPAGE_BPS,
        "initial_cash": INITIAL_CASH,
    }
    exec_block = challenger.get("execution") or {}
    # YAML schema uses max_total_positions; the backtest CLI flag and the
    # base dict use max_positions — translate.
    if "max_total_positions" in exec_block:
        base["max_positions"] = int(exec_block["max_total_positions"])
    base.update({k: exec_block[k] for k in exec_block if k in base})
    return base


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
         # v5.3: the builder writes factor_panel_daily.parquet (the
         # qualified daily panel); scores consume that exact file.
         "--factor-panel", str(panel_dir / "factor_panel_daily.parquet"),
         "--output-dir", str(scores_dir)],
        "stage3: build_formal_scores (frozen VLS)",
    )
    return scores_dir


def stage_snapshots(release_dir: Path, work_dir: Path) -> Path:
    """Build immutable formal snapshots from the PIT release.

    The strict-ledger backtest in --formal-mode consumes six immutable
    snapshots (scores, prices, tradable universe, adjustment factors,
    corporate actions + manifest, security lifecycle + manifest, trade
    calendar).  This stage emits them from the release parquets, CUT to the
    panel's PIT-complete core (>= coverage_ready_date) — pre-ramp rows would
    otherwise fail the ledger's fail-closed universe/lifecycle merges.  The
    corporate-action and lifecycle manifests are sha-bound so the backtest's
    _verified_snapshot rejects any drift.
    """
    import hashlib
    from datetime import datetime, timezone as _tz

    import pandas as pd

    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshots_dir = work_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    ready = None
    panel_report = work_dir / "panel" / "pit_factor_panel_builder_report.json"
    if panel_report.exists():
        ready = json.loads(panel_report.read_text(encoding="utf-8")).get(
            "coverage_ready_date")

    market = pd.read_parquet(release_dir / "market.parquet")
    universe = pd.read_parquet(release_dir / "universe.parquet")
    adjustment = pd.read_parquet(release_dir / "adjustment.parquet")
    if ready:
        market = market[market["trade_date"] >= ready].copy()
        universe = universe[universe["trade_date"] >= ready].copy()
        adjustment = adjustment[adjustment["trade_date"] >= ready].copy()

    # Prices: both price regimes (raw from ods_daily, adjusted from
    # dwd_stock_daily_standard).  prev_adj_close/prev_raw_close are computed
    # by the backtest shift; the first core session falls back to the REAL
    # raw_pre_close (previous session's raw close, carried in the release).
    price_cols = ["trade_date", "symbol", "open", "high", "low", "close",
                  "adj_open", "adj_high", "adj_low", "adj_close",
                  "pre_close", "raw_pre_close",
                  "raw_open", "raw_high", "raw_low", "raw_close",
                  "volume", "amount", "circ_mv", "market_available_at"]
    prices = market[[c for c in price_cols if c in market.columns]].copy()

    # v5.3: the tradable universe is authoritative for lifecycle status, and
    # the label table does NOT cover delisting-transition stocks (e.g.
    # 300379.SZ 东通退 — 1,365 rows, zero label rows ever) nor brand-new
    # IPOs still in label-ingestion lag (2026-02+ listings, 920*.BJ etc. —
    # verified 2026-08-03: 5,366 orphan price rows / 74 symbols).  The strict
    # ledger fails closed on any price row without lifecycle status
    # (universe_snapshot_missing_price_rows), so restrict prices to
    # universe-covered (trade_date, symbol) rows.  The dropped rows are
    # counted and reported — honest exclusion, not silent filtering.
    uni_keys = universe[["trade_date", "symbol"]].drop_duplicates().copy()
    uni_keys["_key_date"] = pd.to_datetime(
        uni_keys["trade_date"], errors="coerce").dt.date
    prices["_key_date"] = pd.to_datetime(
        prices["trade_date"], errors="coerce").dt.date
    price_rows_before = len(prices)
    prices = prices.merge(uni_keys, on=["_key_date", "symbol"], how="left",
                          suffixes=("", "_uni"))
    orphan_rows = int(prices["trade_date_uni"].isna().sum())
    prices = prices[prices["trade_date_uni"].notna()].drop(
        columns=["_key_date", "trade_date_uni"])
    if orphan_rows:
        print(f"  stage4a: excluded {orphan_rows} price rows without "
              f"lifecycle status (delisting-transition / IPO label-lag "
              f"symbols, of {price_rows_before} total)")
    # zstd (not snappy): a 582MB snappy frame written by pyarrow on macOS
    # arm64 reproduced "Corrupt snappy compressed data" on read (flaky
    # codec race, verified 2026-08-03 — identical in-memory DataFrame wrote
    # cleanly with zstd).  The ledger reads parquet codec-agnostically.
    prices.to_parquet(snapshots_dir / "prices.parquet", index=False,
                      compression="zstd")

    uni_cols = ["trade_date", "symbol", "is_listed", "is_st", "is_suspended",
                "limit_status", "security_status_transition",
                "universe_available_at"]
    universe[[c for c in uni_cols if c in universe.columns]].to_parquet(
        snapshots_dir / "tradable_universe.parquet", index=False,
        compression="zstd")

    adj_cols = ["trade_date", "symbol", "adj_factor", "adjustment_available_at"]
    adjustment[[c for c in adj_cols if c in adjustment.columns]].to_parquet(
        snapshots_dir / "adjustment_factor.parquet", index=False,
        compression="zstd")

    # Calendar: the ledger reads it as CSV with exactly cal_date/exchange/
    # is_open/source; the release carries those columns already.
    calendar = pd.read_parquet(release_dir / "trade_calendar.parquet")
    calendar.to_csv(snapshots_dir / "trade_calendar.csv", index=False)

    generated_at = datetime.now(_tz.utc).isoformat()

    def _family_manifest(family: str, snapshot_key: str, source_key: str) -> dict:
        file_path = release_dir / manifest["families"][family]["filename"]
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        return {
            "snapshot_schema_version": f"{family}_snapshot_v1",
            "dataset_version": f"release_{release_dir.name}",
            "generated_at": generated_at,
            snapshot_key: digest,
            source_key: digest,
        }

    ca_manifest = _family_manifest("corporate_actions",
                                   "snapshot_sha256", "source_sha256")
    (snapshots_dir / "corporate_actions_manifest.json").write_text(
        json.dumps(ca_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    lc_manifest = _family_manifest("security_lifecycle",
                                   "lifecycle_snapshot_sha256",
                                   "lifecycle_source_sha256")
    (snapshots_dir / "security_lifecycle_manifest.json").write_text(
        json.dumps(lc_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"stage4a: formal snapshots written to {snapshots_dir} "
          f"(core >= {ready})")
    return snapshots_dir


def build_split_inputs(
    work_dir: Path, scores_dir: Path, snapshots_dir: Path,
) -> tuple[Path, dict[str, tuple[Path, Path]]]:
    """Cut frozen scores + prices to each TIME_SPLIT window.

    The backtest's snapshot branch never filters by --start-date (the
    simulation starts at the first score row of the scores snapshot), so
    every split would otherwise accumulate from the earliest score date —
    later splits become supersets and the window metrics are invalid.
    Returns (split_inputs_dir, {label: (scores_path, prices_path)}).
    """
    import pandas as pd
    scores_all = pd.read_parquet(scores_dir / "formal_scores.parquet")
    prices_all = pd.read_parquet(snapshots_dir / "prices.parquet")
    split_inputs_dir = work_dir / "split_inputs"
    split_inputs_dir.mkdir(parents=True, exist_ok=True)
    split_files: dict[str, tuple[Path, Path]] = {}
    for label, start, end in TIME_SPLITS:
        start_d = pd.Timestamp(start).date()
        end_d = pd.Timestamp(end).date()
        scores_all["_d"] = pd.to_datetime(scores_all["trade_date"], errors="coerce").dt.date
        prices_all["_d"] = pd.to_datetime(prices_all["trade_date"], errors="coerce").dt.date
        win_scores = scores_all[(scores_all["_d"] >= start_d) & (scores_all["_d"] <= end_d)]
        win_prices = prices_all[(prices_all["_d"] >= start_d) & (prices_all["_d"] <= end_d)]
        scores_path = split_inputs_dir / f"{label}_scores.parquet"
        prices_path = split_inputs_dir / f"{label}_prices.parquet"
        win_scores.drop(columns=["_d"]).to_parquet(
            scores_path, index=False, compression="zstd")
        win_prices.drop(columns=["_d"]).to_parquet(
            prices_path, index=False, compression="zstd")
        split_files[label] = (scores_path, prices_path)
    return split_inputs_dir, split_files


def stage_runs(work_dir: Path, release_dir: Path, scores_dir: Path,
               *, strategy_id: str = "vls_mom_contrarian_v1_frozen",
               top_n: int = TOP_N, max_positions: int = MAX_POSITIONS,
               hold_days: int = HOLD_DAYS, score_buffer: float = SCORE_BUFFER,
               drift_band: float = DRIFT_BAND, cost_rate: float = COST_RATE,
               slippage_bps: int = SLIPPAGE_BPS,
               initial_cash: float = INITIAL_CASH,
               portfolio_constraints: dict | None = None) -> list[dict]:
    """Run strict-ledger backtests per time split with frozen inputs.

    Defaults reproduce the frozen champion exactly; a challenger config
    overrides strategy_id + execution parameters.  A portfolio_constraints
    dict (alpha challengers P1/P2/P3) is staged to a YAML file in the work
    dir and passed to the backtest via --portfolio-constraints.
    """
    runs_dir = work_dir / "runs"
    snapshots_dir = stage_snapshots(release_dir, work_dir)
    pc_flag: list[str] = []
    if portfolio_constraints:
        pc_path = work_dir / "portfolio_constraints.yaml"
        pc_path.write_text(
            yaml.safe_dump(portfolio_constraints, sort_keys=False),
            encoding="utf-8")
        pc_flag = ["--portfolio-constraints", str(pc_path)]
    results = []
    split_inputs_dir, split_files = build_split_inputs(work_dir, scores_dir, snapshots_dir)
    for label, start, end in TIME_SPLITS:
        out = runs_dir / label
        win_scores, win_prices = split_files[label]
        _run(
            [PY, "scripts/research_trusted_strategy_account_backtest.py",
             "--risk-profile", "adaptive",
             "--strategies", strategy_id,
             "--execution-mode", "strict_t1_open_precommit",
             "--start-date", start, "--end-date", end,
             "--trade-cost-rate", str(cost_rate), "--slippage-rate", str(slippage_bps / 10_000),
             "--initial-cash", str(initial_cash),
             "--output-dir", str(out),
             "--scores-snapshot", str(split_inputs_dir / f"{label}_scores.parquet"),
             "--prices-snapshot", str(split_inputs_dir / f"{label}_prices.parquet"),
             "--tradable-universe-snapshot", str(snapshots_dir / "tradable_universe.parquet"),
             "--adjustment-factor-snapshot", str(snapshots_dir / "adjustment_factor.parquet"),
             "--corporate-action-snapshot", str(release_dir / "corporate_actions.parquet"),
             "--corporate-action-manifest", str(snapshots_dir / "corporate_actions_manifest.json"),
             "--security-lifecycle-snapshot", str(release_dir / "security_lifecycle.parquet"),
             "--security-lifecycle-manifest", str(snapshots_dir / "security_lifecycle_manifest.json"),
             "--trade-calendar-snapshot", str(snapshots_dir / "trade_calendar.csv"),
             "--top-n", str(top_n), "--max-total-positions", str(max_positions),
             "--hold-days", str(hold_days),
             "--rebalance-score-buffer", str(score_buffer),
             "--rebalance-weight-drift-band", str(drift_band),
             "--require-verified-evidence", "--formal-mode", "--force-strict-ledger",
             *pc_flag],
            f"stage4: strict backtest {label}",
        )
        results.append({"split": label, "start": start, "end": end, "output": str(out)})
    return results


def stage_report(results: list[dict], output_root: Path, release_dir: Path,
                 *, strategy_label: str = "vls_mom_contrarian_v1_frozen",
                 params_label: str | None = None,
                 report_prefix: str = "vls_oos_validation") -> Path:
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

    report_path = output_root / f"{report_prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    lines = ["# VLS OOS Validation", "",
             f"Strategy: {strategy_label}"
             + (f" ({params_label})" if params_label else ""),
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
    ]
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    consistent = bool(manifest.get("consistent_snapshot", False))
    release_id = release_dir.name
    lines += [
        f"- Data tier: release {release_id} is "
        f"{'CONSISTENT-SNAPSHOT' if consistent else 'DIAGNOSTIC'} "
        f"(consistent_snapshot={'true' if consistent else 'false'} — the local "
        "MySQL has log_bin=0). Directional OOS evidence only; formal E3 runs "
        "require a binlog-enabled server or a relaxed contract.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n=== stage5: report written to {report_path} ===")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--strategy-def", type=Path,
                        default=PROJECT_ROOT / "config/strategy_definitions/vls_mom_contrarian_v1_frozen.yaml")
    parser.add_argument("--challenger-config", type=Path, default=None,
                        help="Pre-registered alpha challenger YAML "
                             "(config/alpha_challengers/<id>.yaml). Overrides "
                             "strategy definition, execution parameters, and "
                             "output root; pre-registration SHA is verified "
                             "fail-closed before any stage runs.")
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "exports/formal_evidence/vls_oos")
    parser.add_argument("--stages", default="adapter,panel,scores,runs,report")
    args = parser.parse_args()

    stages = [s.strip() for s in args.stages.split(",")]
    release_dir = args.release_dir.resolve()
    if not (release_dir / "manifest.json").exists():
        print(f"FATAL: no manifest.json in {release_dir}")
        return 2

    # ── Challenger overrides (pre-registration verified fail-closed) ──
    challenger: dict | None = None
    exec_params: dict = {}
    strategy_def_path = args.strategy_def
    strategy_id = "vls_mom_contrarian_v1_frozen"
    params_label = "FROZEN 2026-08-03"
    report_prefix = "vls_oos_validation"
    if args.challenger_config is not None:
        challenger = _load_challenger_config(args.challenger_config.resolve())
        _verify_challenger_preregistration(challenger, args.challenger_config.resolve())
        strategy_id = str(challenger.get("challenger_id"))
        exec_params = _challenger_execution(challenger)
        params_label = (
            f"challenger {strategy_id} (TopN={exec_params['top_n']}, "
            f"hold={exec_params['hold_days']}, "
            f"buffer={exec_params['score_buffer']}, band={exec_params['drift_band']})"
        )
        report_prefix = f"challenger_{strategy_id}"
        # Challenger outputs go to their own evidence directory; the
        # challenger YAML itself doubles as the strategy definition because
        # factor_weights/factor_signs live at top level.
        strategy_def_path = args.challenger_config.resolve()
        if args.output_root == parser.get_default("output_root"):
            args.output_root = PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers" / strategy_id
        print(f"challenger {strategy_id}: pre-registration verified, "
              f"output -> {args.output_root}")

    work_dir = args.output_root
    work_dir.mkdir(parents=True, exist_ok=True)

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
        if not (panel_dir / "factor_panel_daily.parquet").exists():
            print("FATAL: factor_panel.parquet missing — run --stages panel first")
            return 2
        scores_dir = stage_scores(strategy_def_path, panel_dir, work_dir)
    if "runs" in stages:
        if scores_dir is None:
            scores_dir = work_dir / "scores"
        if not (scores_dir / "formal_scores.parquet").exists():
            print("FATAL: formal_scores.parquet missing — run --stages scores first")
            return 2
        pc = challenger.get("portfolio_constraints") if challenger else None
        results = stage_runs(
            work_dir, release_dir, scores_dir,
            strategy_id=strategy_id, portfolio_constraints=pc,
            **exec_params)
    if "report" in stages:
        if not results:
            results = []
            for label, start, end in TIME_SPLITS:
                results.append({"split": label, "start": start, "end": end,
                                "output": str(work_dir / "runs" / label)})
        stage_report(results, args.output_root, release_dir,
                     strategy_label=strategy_id, params_label=params_label,
                     report_prefix=report_prefix)

    print("\nVLS_OOS_VALIDATION_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
