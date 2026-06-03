"""Run trusted strategy optimization matrices without changing production.

This script wraps the account-level T+1 trusted backtest and ranks experiments
by risk-adjusted metrics. It is intentionally research-only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research_trusted_strategy_account_backtest import run_account_backtest


OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
CORE_STRATEGIES = (
    "tiered_liquidity_then_bs_v2",
    "baseline_full_liquidity_detail",
    "baseline_full_dynamic_factor_industry_cap2",
    "baseline_full_score",
)
INDUSTRY_STRATEGIES = (
    "tiered_liquidity_then_bs_v2",
    "tiered_liquidity_then_bs_v2_industry_cap2",
    "tiered_liquidity_then_bs_v2_industry_cap1",
    "tiered_liquidity_then_bs_v2_industry_penalty_5pt",
)
MARKET_GATE_STRATEGIES = (
    "tiered_liquidity_then_bs_v2",
    "tiered_liquidity_then_bs_v2_market_gate",
    "baseline_full_liquidity_detail_market_gate",
)
ADAPTIVE_STRATEGIES = (
    "tiered_liquidity_then_bs_v2",
    "baseline_full_liquidity_detail",
    "baseline_full_score",
    "adaptive_style_switch",
    "adaptive_style_switch_dynamic_position",
)


@dataclass(frozen=True)
class Experiment:
    group: str
    name: str
    strategies: tuple[str, ...]
    hold_days: int = 10
    trade_cost_rate: float = 0.00075
    slippage_rate: float = 0.0
    hard_stop_loss_pct: float = 0.0
    position_ratio: float = 1.0


def _parse_csv_numbers(raw: str, cast=float) -> list:
    return [cast(item.strip()) for item in str(raw).split(",") if item.strip()]


def _experiment_args(base: argparse.Namespace, exp: Experiment) -> SimpleNamespace:
    return SimpleNamespace(
        start_date=base.start_date,
        end_date=base.end_date,
        strategies=",".join(exp.strategies),
        initial_cash=float(base.initial_cash),
        top_n=int(base.top_n),
        hold_days=int(exp.hold_days),
        trade_cost_rate=float(exp.trade_cost_rate),
        slippage_rate=float(exp.slippage_rate),
        position_ratio=float(exp.position_ratio),
        hard_stop_loss_pct=float(exp.hard_stop_loss_pct),
        lot_size=int(base.lot_size),
        min_trade_value=float(base.min_trade_value),
        max_total_positions=int(base.max_total_positions),
        min_pool_size=int(base.min_pool_size),
        dynamic_lookback_dates=int(base.dynamic_lookback_dates),
    )


def _build_experiments(args: argparse.Namespace) -> list[Experiment]:
    if args.preset == "smoke":
        return [
            Experiment("smoke", "current_baseline", ("tiered_liquidity_then_bs_v2",)),
            Experiment("smoke", "industry_cap2", ("tiered_liquidity_then_bs_v2_industry_cap2",)),
        ]

    hold_days = _parse_csv_numbers(args.hold_days_grid, int)
    costs = _parse_csv_numbers(args.trade_cost_grid, float)
    slippages = _parse_csv_numbers(args.slippage_grid, float)
    stops = _parse_csv_numbers(args.stop_loss_grid, float)
    position_ratios = _parse_csv_numbers(args.position_ratio_grid, float)

    experiments: list[Experiment] = []
    if args.preset in {"core", "full"}:
        for hold in hold_days:
            for cost in costs:
                slip_values = slippages if args.preset == "full" else [slippages[0]]
                for slip in slip_values:
                    experiments.append(
                        Experiment(
                            "hold_cost",
                            f"hold{hold}_cost{cost:g}_slip{slip:g}",
                            CORE_STRATEGIES,
                            hold_days=hold,
                            trade_cost_rate=cost,
                            slippage_rate=slip,
                        )
                    )
        for stop in stops:
            experiments.append(
                Experiment(
                    "stop_loss",
                    f"stop{stop:g}",
                    ("tiered_liquidity_then_bs_v2",),
                    hard_stop_loss_pct=stop,
                )
            )
        experiments.append(
            Experiment("industry", "tiered_industry_controls", INDUSTRY_STRATEGIES)
        )
        for ratio in position_ratios:
            experiments.append(
                Experiment(
                    "market_gate_position",
                    f"market_gate_pos{ratio:g}",
                    MARKET_GATE_STRATEGIES,
                    position_ratio=ratio,
                )
            )
        experiments.append(
            Experiment("adaptive", "attack_defensive_fallback_review", ADAPTIVE_STRATEGIES)
        )
    groups = {item.strip() for item in str(args.groups or "").split(",") if item.strip()}
    if groups:
        experiments = [exp for exp in experiments if exp.group in groups]
    return experiments


def _load_frame(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def _industry_metrics(positions: pd.DataFrame, strategy: str) -> dict[str, float]:
    if positions.empty or "industry" not in positions.columns or "weight" not in positions.columns:
        return {
            "avg_top_industry_weight": math.nan,
            "max_top_industry_weight": math.nan,
            "avg_max_industry_names": math.nan,
            "max_industry_names": math.nan,
        }
    d = positions[positions["strategy"].astype(str).eq(strategy)].copy()
    if d.empty:
        return {
            "avg_top_industry_weight": math.nan,
            "max_top_industry_weight": math.nan,
            "avg_max_industry_names": math.nan,
            "max_industry_names": math.nan,
        }
    d["industry"] = d["industry"].fillna("未知").astype(str)
    d["weight"] = pd.to_numeric(d["weight"], errors="coerce").fillna(0.0)
    exposure = d.groupby(["trade_date", "industry"], as_index=False).agg(
        industry_weight=("weight", "sum"),
        industry_names=("symbol", "nunique"),
    )
    daily = exposure.groupby("trade_date", as_index=False).agg(
        top_industry_weight=("industry_weight", "max"),
        max_industry_names=("industry_names", "max"),
    )
    return {
        "avg_top_industry_weight": float(daily["top_industry_weight"].mean()),
        "max_top_industry_weight": float(daily["top_industry_weight"].max()),
        "avg_max_industry_names": float(daily["max_industry_names"].mean()),
        "max_industry_names": float(daily["max_industry_names"].max()),
    }


def _industry_exposure_rows(positions: pd.DataFrame, exp: Experiment, experiment_id: int) -> list[dict]:
    if positions.empty or "industry" not in positions.columns or "weight" not in positions.columns:
        return []
    d = positions.copy()
    d["industry"] = d["industry"].fillna("未知").astype(str)
    d["weight"] = pd.to_numeric(d["weight"], errors="coerce").fillna(0.0)
    exposure = d.groupby(["strategy", "trade_date", "industry"], as_index=False).agg(
        industry_weight=("weight", "sum"),
        industry_names=("symbol", "nunique"),
    )
    rows = []
    for row in exposure.to_dict("records"):
        rows.append(
            {
                "experiment_id": int(experiment_id),
                "experiment_group": exp.group,
                "experiment_name": exp.name,
                **row,
            }
        )
    return rows


def _segment_return(nav: pd.DataFrame, strategy: str, days: int) -> float:
    if nav.empty or "equity" not in nav.columns:
        return math.nan
    d = nav[nav["strategy"].astype(str).eq(strategy)].copy()
    if d.empty:
        return math.nan
    d = d.sort_values("trade_date")
    tail = d.tail(int(days))
    if len(tail) < 2:
        return math.nan
    start = float(tail["equity"].iloc[0])
    end = float(tail["equity"].iloc[-1])
    return end / start - 1.0 if start else math.nan


def _augment_summary(
    summary: pd.DataFrame,
    nav: pd.DataFrame,
    positions: pd.DataFrame,
    exp: Experiment,
    report: dict,
) -> pd.DataFrame:
    rows = []
    for row in summary.to_dict("records"):
        strategy = str(row.get("strategy") or "")
        max_drawdown = float(row.get("max_drawdown") or 0.0)
        total_return = float(row.get("total_return") or 0.0)
        annualized = float(row.get("annualized_return") or 0.0)
        total_cost = float(row.get("total_cost") or 0.0)
        final_equity = float(row.get("final_equity") or 0.0)
        initial_cash = float(row.get("initial_cash") or 0.0)
        risk_base = abs(max_drawdown)
        industry = _industry_metrics(positions, strategy)
        out = {
            **row,
            "experiment_group": exp.group,
            "experiment_name": exp.name,
            "experiment_strategies": ",".join(exp.strategies),
            "calmar": annualized / risk_base if risk_base > 0 else math.nan,
            "return_drawdown_ratio": total_return / risk_base if risk_base > 0 else math.nan,
            "max_single_day_loss": float(row.get("worst_day")) if pd.notna(row.get("worst_day")) else math.nan,
            "cost_to_profit_ratio": total_cost / max(final_equity - initial_cash, 1.0) if final_equity > initial_cash else math.nan,
            "segment_return_last_126d": _segment_return(nav, strategy, 126),
            "segment_return_last_63d": _segment_return(nav, strategy, 63),
            "source_dir": Path(report["files"]["summary_csv"]).parent.as_posix(),
            "pit_status": "trusted",
        }
        out.update(industry)
        rows.append(out)
    return pd.DataFrame(rows)


def _write_report(out_dir: Path, results: pd.DataFrame, experiments: list[Experiment], args: argparse.Namespace) -> None:
    show_cols = [
        "experiment_group",
        "experiment_name",
        "strategy",
        "final_equity",
        "total_return",
        "annualized_return",
        "max_drawdown",
        "calmar",
        "return_drawdown_ratio",
        "trade_count",
        "turnover",
        "total_cost",
        "max_single_day_loss",
        "avg_top_industry_weight",
        "max_industry_names",
    ]
    show = results.sort_values(["calmar", "return_drawdown_ratio"], ascending=False)[show_cols].head(30).copy()
    for col in ("total_return", "annualized_return", "max_drawdown", "max_single_day_loss", "avg_top_industry_weight"):
        show[col] = show[col].map(lambda x: "" if pd.isna(x) else f"{float(x) * 100:.2f}%")
    for col in ("calmar", "return_drawdown_ratio"):
        show[col] = show[col].map(lambda x: "" if pd.isna(x) else f"{float(x):.3f}")
    lines = [
        "# 可信策略优化矩阵报告",
        "",
        "## 口径",
        "",
        f"- 回测窗口：{args.start_date} 至 {args.end_date or 'latest'}",
        f"- 初始资金：{float(args.initial_cash):,.2f}",
        f"- TopN：{int(args.top_n)}；最多持仓：{int(args.max_total_positions)}",
        "- 信号：T 日评分；执行：T+1 开盘。",
        "- 可信口径：不使用 `bs_model_*`，动态权重只使用已完成持有期样本。",
        f"- 实验数：{len(experiments)}",
        "",
        "## Calmar 排名前 30",
        "",
        show.to_markdown(index=False) if not show.empty else "_无结果_",
        "",
        "## 输出文件",
        "",
        "- `optimization_summary.csv`：完整结果。",
        "- `optimization_ranked_by_calmar.csv`：按 Calmar 排序。",
        "- `optimization_industry_exposure.csv`：每日行业暴露。",
        "- `optimization_experiments.json`：实验参数与回测子目录。",
    ]
    (out_dir / "optimization_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_matrix(args: argparse.Namespace) -> dict:
    out_dir = OUT_ROOT / datetime.now().strftime("trusted_strategy_optimization_%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    experiments = _build_experiments(args)
    if args.dry_run:
        payload = {"out_dir": str(out_dir), "experiments": [exp.__dict__ for exp in experiments]}
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return payload

    all_results: list[pd.DataFrame] = []
    all_industry_rows: list[dict] = []
    experiment_records: list[dict] = []
    for idx, exp in enumerate(experiments, start=1):
        report = run_account_backtest(_experiment_args(args, exp))
        summary = _load_frame(report["files"]["summary_csv"])
        nav = _load_frame(report["files"]["nav_csv"])
        positions = _load_frame(report["files"]["positions_csv"])
        augmented = _augment_summary(summary, nav, positions, exp, report)
        augmented.insert(0, "experiment_id", idx)
        all_results.append(augmented)
        all_industry_rows.extend(_industry_exposure_rows(positions, exp, idx))
        experiment_records.append(
            {
                "experiment_id": idx,
                **exp.__dict__,
                "source_dir": str(Path(report["files"]["summary_csv"]).parent),
            }
        )

    results = pd.concat(all_results, ignore_index=True) if all_results else pd.DataFrame()
    summary_path = out_dir / "optimization_summary.csv"
    ranked_path = out_dir / "optimization_ranked_by_calmar.csv"
    industry_path = out_dir / "optimization_industry_exposure.csv"
    experiments_path = out_dir / "optimization_experiments.json"
    results.to_csv(summary_path, index=False)
    results.sort_values(["calmar", "return_drawdown_ratio"], ascending=False).to_csv(ranked_path, index=False)
    pd.DataFrame(all_industry_rows).to_csv(industry_path, index=False)
    experiments_path.write_text(json.dumps(experiment_records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_report(out_dir, results, experiments, args)
    payload = {
        "out_dir": str(out_dir),
        "summary_csv": str(summary_path),
        "ranked_csv": str(ranked_path),
        "industry_exposure_csv": str(industry_path),
        "report_md": str(out_dir / "optimization_report.md"),
        "experiments_json": str(experiments_path),
        "experiment_count": len(experiments),
        "result_rows": int(len(results)),
        "pit_control": [
            "Signals use score_rank_daily rows on signal date T.",
            "Execution uses T+1 open.",
            "Trusted specs exclude bs_model_* model-risk strategies by default.",
            "Dynamic weights and adaptive performance use completed samples only.",
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trusted strategy optimization matrix for research.")
    parser.add_argument("--preset", choices=["smoke", "core", "full"], default="core")
    parser.add_argument(
        "--groups",
        default="",
        help=(
            "Comma-separated experiment groups to run after preset expansion. "
            "Examples: hold_cost,stop_loss,industry,market_gate_position,adaptive."
        ),
    )
    parser.add_argument("--start-date", default="2025-06-02")
    parser.add_argument("--end-date", default="2026-05-29")
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--max-total-positions", type=int, default=5)
    parser.add_argument("--hold-days-grid", default="8,10,12,15")
    parser.add_argument("--trade-cost-grid", default="0.00075,0.0015,0.002")
    parser.add_argument("--slippage-grid", default="0,0.0005,0.001")
    parser.add_argument("--stop-loss-grid", default="0,8,10,12,15")
    parser.add_argument("--position-ratio-grid", default="1.0,0.7,0.5")
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--min-trade-value", type=float, default=500.0)
    parser.add_argument("--min-pool-size", type=int, default=5000)
    parser.add_argument("--dynamic-lookback-dates", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_matrix(args)


if __name__ == "__main__":
    main()
