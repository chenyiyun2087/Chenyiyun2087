from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research_trusted_strategy_account_backtest import (
    ASHARE_ADAPTIVE_VERSION,
    ASHARE_DEFAULT_WEIGHT_PROFILE,
    ASHARE_ROUTE_CACHE_ROOT,
    ASHARE_WEIGHT_PROFILE_DEFAULTS,
    ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
    DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
    run_account_backtest,
)

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


def _split_csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _split_ints(value: str | None, default: list[int]) -> list[int]:
    if not value:
        return default
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _lookup_metric(rows: list[dict[str, object]], strategy: str, window: str, metric: str) -> float | None:
    for row in rows:
        if row.get("strategy") == strategy and row.get("window") == window:
            raw = row.get(metric)
            return None if raw is None else float(raw)
    return None


def _summary_row(report: dict[str, object], *, profile: str, supplement_limit: int) -> dict[str, object]:
    summary_rows = list(report.get("summary") or [])
    window_rows = list(report.get("window_summary") or [])
    adaptive_summary = next(
        (row for row in summary_rows if row.get("strategy") == ADAPTIVE_MARKET_STYLE_STRATEGY_NAME),
        {},
    )
    return {
        "adaptive_version": ASHARE_ADAPTIVE_VERSION,
        "ashare_weight_profile": profile,
        "ashare_supplement_limit": int(supplement_limit),
        "total_return": adaptive_summary.get("total_return"),
        "annualized_return": adaptive_summary.get("annualized_return"),
        "max_drawdown": adaptive_summary.get("max_drawdown"),
        "avg_gross_exposure": adaptive_summary.get("avg_gross_exposure"),
        "return_3m": _lookup_metric(window_rows, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, "3m", "total_return"),
        "mdd_3m": _lookup_metric(window_rows, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, "3m", "max_drawdown"),
        "return_6m": _lookup_metric(window_rows, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, "6m", "total_return"),
        "mdd_6m": _lookup_metric(window_rows, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, "6m", "max_drawdown"),
        "return_1y": _lookup_metric(window_rows, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, "1y", "total_return"),
        "mdd_1y": _lookup_metric(window_rows, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, "1y", "max_drawdown"),
        "return_3y": _lookup_metric(window_rows, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, "3y", "total_return"),
        "mdd_3y": _lookup_metric(window_rows, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, "3y", "max_drawdown"),
        "report_dir": str(Path(str(report["files"]["json"])).parent),
    }


def run_grid(args: argparse.Namespace) -> dict[str, object]:
    profiles = _split_csv(args.ashare_weight_profiles, [ASHARE_DEFAULT_WEIGHT_PROFILE, "research_stage2"])
    limits = _split_ints(args.ashare_supplement_limits, [2, 3])
    unknown = sorted(set(profiles) - set(ASHARE_WEIGHT_PROFILE_DEFAULTS))
    if unknown:
        raise ValueError(f"Unknown AShare weight profiles: {', '.join(unknown)}")

    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_ashare_weighted_adaptive_v22")
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.ashare_target_cache_dir or ASHARE_ROUTE_CACHE_ROOT)
    cache_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    reports: list[dict[str, object]] = []
    for profile in profiles:
        for limit in limits:
            bt_args = SimpleNamespace(
                start_date=args.start_date,
                end_date=args.end_date,
                risk_profile="adaptive",
                strategies=args.strategies,
                initial_cash=args.initial_cash,
                top_n=args.top_n,
                hold_days=args.hold_days,
                trade_cost_rate=args.trade_cost_rate,
                slippage_rate=args.slippage_rate,
                position_ratio=args.position_ratio,
                hard_stop_loss_pct=0.0,
                lot_size=args.lot_size,
                min_trade_value=args.min_trade_value,
                max_total_positions=args.max_total_positions,
                min_pool_size=args.min_pool_size,
                dynamic_lookback_dates=args.dynamic_lookback_dates,
                ashare_weight_profile=profile,
                ashare_release_tier=None,
                ashare_supplement_limit=limit,
                ashare_target_cache_dir=str(cache_dir),
            )
            report = run_account_backtest(bt_args)
            reports.append(
                {
                    "profile": profile,
                    "supplement_limit": int(limit),
                    "report": report,
                }
            )
            rows.append(_summary_row(report, profile=profile, supplement_limit=limit))

    result = pd.DataFrame(rows).sort_values(["return_6m", "max_drawdown"], ascending=[False, False])
    summary_csv = out_dir / "ashare_weighted_adaptive_v22_grid_summary.csv"
    report_json = out_dir / "ashare_weighted_adaptive_v22_grid_report.json"
    report_md = out_dir / "ashare_weighted_adaptive_v22_grid_report.md"
    result.to_csv(summary_csv, index=False)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": vars(args),
        "adaptive_version": ASHARE_ADAPTIVE_VERSION,
        "cache_dir": str(cache_dir),
        "summary_csv": str(summary_csv),
        "reports": reports,
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    show = result.copy()
    for col in ["total_return", "annualized_return", "max_drawdown", "return_3m", "mdd_3m", "return_6m", "mdd_6m", "return_1y", "mdd_1y", "return_3y", "mdd_3y"]:
        if col in show.columns:
            show[col] = show[col].map(lambda x: "" if pd.isna(x) else f"{float(x) * 100:.2f}%")
    report_md.write_text(
        "\n".join(
            [
                "# AShare 加权增强 Adaptive v2.2 网格研究",
                "",
                f"- 版本：`{ASHARE_ADAPTIVE_VERSION}`",
                f"- 缓存目录：`{cache_dir}`",
                f"- 策略：`{args.strategies}`",
                f"- 窗口：`{args.start_date}` 至 `{args.end_date or 'latest'}`",
                "",
                show.to_markdown(index=False) if not show.empty else "_无结果_",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {"out_dir": str(out_dir), "summary_csv": str(summary_csv), "json": str(report_json), "markdown": str(report_md)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid research for AShare weighted adaptive_market_style v2.2.")
    parser.add_argument("--start-date", default="2023-01-04")
    parser.add_argument("--end-date", default=None)
    parser.add_argument(
        "--strategies",
        default=f"{ADAPTIVE_MARKET_STYLE_STRATEGY_NAME},baseline_full_liquidity_detail_vol_position,ashare_auto_shadow,{DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME}",
    )
    parser.add_argument("--ashare-weight-profiles", default="prod_stage1,research_stage2")
    parser.add_argument("--ashare-supplement-limits", default="2,3")
    parser.add_argument("--ashare-target-cache-dir", default=str(ASHARE_ROUTE_CACHE_ROOT))
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=None)
    parser.add_argument("--trade-cost-rate", type=float, default=0.00075)
    parser.add_argument("--slippage-rate", type=float, default=0.0)
    parser.add_argument("--position-ratio", type=float, default=None)
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--min-trade-value", type=float, default=500.0)
    parser.add_argument("--max-total-positions", type=int, default=None)
    parser.add_argument("--min-pool-size", type=int, default=5000)
    parser.add_argument("--dynamic-lookback-dates", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(run_grid(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
