from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import (
    add_dynamic_factor_score,
    add_dynamic_ic_factor_score,
    add_forward_returns,
    add_liquidity_derived_features,
    attach_market_environment,
    build_market_environment,
    build_strategy_specs,
    filter_strategy_specs,
    load_prices,
    load_scores,
)
from scripts.research_trusted_strategy_account_backtest import (
    ADAPTIVE_UNDERLYING,
    CORE_STRATEGY_NAMES,
    _adaptive_position_scale,
    _build_adaptive_perf_table,
    _build_targets_cache,
    _choose_adaptive_role,
    _day_style_features,
    _max_drawdown,
    _safe_float,
)


OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
DEFAULT_GROUP_COLUMNS = [
    "index_bucket",
    "market_liquidity_bucket",
    "amount_state",
    "industry_concentration_bucket",
    "volatility_bucket",
]
CORE_FIXED_STRATEGIES = [name for name in CORE_STRATEGY_NAMES if name != "adaptive_market_style"]


def _parse_csv(raw: str | None, default: list[str]) -> list[str]:
    if not raw:
        return default
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _strategy_specs(names: list[str]) -> dict[str, object]:
    trusted = {spec.name: spec for spec in filter_strategy_specs(build_strategy_specs(), trusted_only=True)}
    missing = [name for name in names if name not in trusted and name != "adaptive_market_style"]
    if missing:
        available = ", ".join(sorted(trusted))
        raise ValueError(f"Unknown trusted core strategy: {', '.join(missing)}. Available: {available}")
    return {name: trusted[name] for name in names if name in trusted}


def _prepare_scores(args: argparse.Namespace, strategies: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    engine = create_engine(build_sqlalchemy_url())
    scores = load_scores(
        engine,
        start_date=args.start_date,
        end_date=args.end_date,
        min_pool_size=args.min_pool_size,
    )
    if scores.empty:
        raise RuntimeError("No score rows loaded after filters.")
    prices = load_prices(engine, scores["trade_date"].min(), scores["trade_date"].max(), args.hold_days)
    if prices.empty:
        raise RuntimeError("No price rows loaded.")
    max_nav_date = pd.Timestamp(args.end_date).date() if args.end_date else max(scores["trade_date"].dropna())
    prices = prices[prices["trade_date"] <= max_nav_date].copy()
    scores = add_liquidity_derived_features(scores, prices)
    scores = add_forward_returns(scores, prices, args.hold_days)
    needs_dynamic = any(
        getattr(spec, "sort_col", "") in {"dynamic_factor_score", "dynamic_ic_factor_score"}
        for spec in strategies.values()
    )
    if needs_dynamic:
        scores, factor_weights = add_dynamic_factor_score(
            scores,
            lookback_dates=args.dynamic_lookback_dates,
            top_n=args.top_n,
        )
        scores, ic_weights = add_dynamic_ic_factor_score(
            scores,
            lookback_dates=args.dynamic_lookback_dates,
        )
        if not factor_weights.empty:
            factor_weights["method"] = "long_topn_return"
        _ = pd.concat([factor_weights, ic_weights], ignore_index=True, sort=False)
    market_env = build_market_environment(scores, prices)
    scores = attach_market_environment(scores, market_env)
    return scores, prices, market_env


def _cycle_summary(returns: pd.Series, exposure: pd.Series | None = None) -> dict[str, object]:
    ret = pd.to_numeric(returns, errors="coerce").dropna()
    if ret.empty:
        return {}
    exposure = pd.to_numeric(exposure, errors="coerce") if exposure is not None else pd.Series(1.0, index=ret.index)
    total_return = float((1.0 + ret).prod() - 1.0)
    return {
        "periods": int(len(ret)),
        "total_return": total_return,
        "avg_cycle_return": float(ret.mean()),
        "max_drawdown": _max_drawdown((1.0 + ret).cumprod()),
        "annualized_volatility": float(ret.std(ddof=0) * np.sqrt(252.0 / 10.0)),
        "win_rate": float((ret > 0).mean()),
        "avg_gross_exposure": float(exposure.reindex(ret.index).mean()),
    }


def _bucketize(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["amount_state"] = np.select(
        [out["market_amount_ratio_20"] < 0.8, out["market_amount_ratio_20"] > 1.2],
        ["amount_low", "amount_high"],
        default="amount_normal",
    )
    out["industry_concentration_bucket"] = np.select(
        [out["top_industry_weight"] >= 0.6, out["top_industry_weight"] >= 0.4],
        ["industry_high_concentration", "industry_mid_concentration"],
        default="industry_diversified",
    )
    out["volatility_bucket"] = np.select(
        [out["avg_vol_20"] >= 0.055, out["avg_vol_20"] >= 0.04],
        ["vol_high", "vol_mid"],
        default="vol_low",
    )
    return out


def _build_core_cycles(scores: pd.DataFrame, strategies: dict[str, object], top_n: int) -> tuple[pd.DataFrame, dict]:
    scores_by_date = {day: group.copy() for day, group in scores.groupby("trade_date", sort=True)}
    targets_cache = _build_targets_cache(scores_by_date, strategies, top_n=top_n)
    rows: list[dict[str, object]] = []
    for signal_date, day_scores in scores_by_date.items():
        for strategy_name, spec in strategies.items():
            targets = targets_cache.get((signal_date, strategy_name), pd.DataFrame())
            if targets.empty or "forward_ret" not in targets.columns:
                continue
            weights = pd.to_numeric(targets.get("effective_weight", 1.0), errors="coerce").fillna(0.0)
            returns = pd.to_numeric(targets["forward_ret"], errors="coerce")
            valid = returns.notna() & weights.gt(0)
            if not valid.any():
                continue
            cycle_ret = float((returns[valid] * weights[valid]).sum() / weights[valid].sum())
            rows.append(
                {
                    "strategy": strategy_name,
                    "signal_date": signal_date,
                    "exit_date": targets["exit_date_for_label"].dropna().max(),
                    "cycle_ret": cycle_ret,
                    "selected_count": int(len(targets)),
                    "gross_exposure": float(weights.sum()),
                    **_day_style_features(day_scores, targets),
                }
            )
    return _bucketize(pd.DataFrame(rows)), targets_cache


def _build_group_summary(cycles: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for col in group_columns:
        if col not in cycles.columns:
            raise ValueError(f"Unknown grouping column `{col}`. Available columns include: {', '.join(sorted(cycles.columns))}")
        for (strategy, bucket), group in cycles.groupby(["strategy", col], dropna=False):
            summary = _cycle_summary(group["cycle_ret"], group["gross_exposure"])
            if not summary:
                continue
            rows.append({"group_column": col, "bucket": bucket, "strategy": strategy, **summary})
    return pd.DataFrame(rows)


def _build_adaptive_event_report(
    scores: pd.DataFrame,
    targets_cache: dict,
    strategies: dict[str, object],
    top_n: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    day_indices = scores.groupby("trade_date", sort=True).indices
    scores_by_date = {day: group.copy() for day, group in scores.groupby("trade_date", sort=True)}
    underlying_specs = {role: strategies[name] for role, name in ADAPTIVE_UNDERLYING.items() if name in strategies}
    perf = _build_adaptive_perf_table(scores, day_indices, underlying_specs, top_n=top_n, targets_cache=targets_cache)
    current_role: str | None = None
    current_role_days = 0
    decisions: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    cycle_lookup = {
        (row["signal_date"], row["role"]): row
        for row in perf.to_dict("records")
    }
    for signal_date, day_scores in scores_by_date.items():
        decision = _choose_adaptive_role(signal_date, day_scores, perf, current_role, current_role_days)
        active_role = str(decision["active_role"])
        if active_role == current_role:
            current_role_days += 1
        else:
            current_role = active_role
            current_role_days = 1
        position_scale, position_reason = _adaptive_position_scale(decision)
        decision["adaptive_position_scale"] = float(position_scale)
        decision["adaptive_position_reason"] = position_reason
        decision["target_position_ratio"] = float(position_scale)
        decision["current_role_days_after"] = int(current_role_days)
        decisions.append(decision)
        cycle = cycle_lookup.get((signal_date, active_role), {})
        cycle_ret = _safe_float(cycle.get("cycle_ret"), np.nan)
        event_rows.append(
            {
                "strategy": "adaptive_market_style_event",
                "signal_date": signal_date,
                "exit_date": cycle.get("exit_date"),
                "active_role": active_role,
                "selected_strategy": decision.get("selected_strategy"),
                "target_position_ratio": float(position_scale),
                "cycle_ret": cycle_ret * float(position_scale) if np.isfinite(cycle_ret) else np.nan,
                "raw_cycle_ret": cycle_ret,
                "reason": decision.get("reason"),
                "gross_exposure": float(position_scale),
                **{k: v for k, v in decision.items() if k not in {"strategy", "cycle_ret"}},
            }
        )
    return _bucketize(pd.DataFrame(event_rows)), pd.DataFrame(decisions)


def _build_window_summary(cycles: pd.DataFrame, end_date: object) -> pd.DataFrame:
    windows = [
        ("3m", pd.Timestamp(end_date) - pd.DateOffset(months=3)),
        ("6m", pd.Timestamp(end_date) - pd.DateOffset(months=6)),
        ("1y", pd.Timestamp(end_date) - pd.DateOffset(years=1)),
        ("3y", pd.Timestamp(end_date) - pd.DateOffset(years=3)),
    ]
    rows: list[dict[str, object]] = []
    d = cycles.copy()
    d["signal_date"] = pd.to_datetime(d["signal_date"], errors="coerce")
    for strategy, group in d.groupby("strategy", sort=False):
        for window, start_ts in windows:
            w = group[group["signal_date"].ge(start_ts)].copy()
            summary = _cycle_summary(w["cycle_ret"], w["gross_exposure"])
            if not summary:
                continue
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "window_start": str(w["signal_date"].min().date()),
                    "window_end": str(w["signal_date"].max().date()),
                    **summary,
                }
            )
    return pd.DataFrame(rows)


def _grid_search(cycles: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    amount_thresholds = [0.8, 1.0, 1.2]
    vol_thresholds = [0.04, 0.05, 0.06]
    concentration_thresholds = [0.4, 0.6]
    pivot = cycles.pivot_table(
        index="signal_date",
        columns="strategy",
        values="cycle_ret",
        aggfunc="first",
    )
    meta = cycles.drop_duplicates("signal_date").set_index("signal_date")
    required = set(CORE_FIXED_STRATEGIES[:3])
    if not required.issubset(set(pivot.columns)):
        return pd.DataFrame()
    for amount_threshold in amount_thresholds:
        for vol_threshold in vol_thresholds:
            for concentration_threshold in concentration_thresholds:
                selected_returns = []
                exposures = []
                for signal_date, row in pivot.iterrows():
                    env = meta.loc[signal_date]
                    if (
                        _safe_float(env.get("market_amount_ratio_20"), np.nan) >= amount_threshold
                        and str(env.get("index_bucket")) == "index_strong"
                    ):
                        strategy = "tiered_liquidity_then_bs_v2"
                        exposure = 1.0
                    elif (
                        _safe_float(env.get("avg_vol_20"), np.nan) >= vol_threshold
                        and _safe_float(env.get("top_industry_weight"), 0.0) < concentration_threshold
                    ):
                        strategy = "baseline_full_liquidity_detail_vol_position"
                        exposure = 0.7
                    elif (
                        _safe_float(env.get("market_amount_ratio_20"), np.nan) < 0.8
                        or _safe_float(env.get("top_industry_weight"), 0.0) >= concentration_threshold
                    ):
                        strategy = "baseline_full_liquidity"
                        exposure = 0.5
                    else:
                        strategy = "baseline_full_liquidity_detail_market_gate"
                        exposure = 0.8
                    ret = _safe_float(row.get(strategy), np.nan)
                    if np.isfinite(ret):
                        selected_returns.append(ret * exposure)
                        exposures.append(exposure)
                summary = _cycle_summary(pd.Series(selected_returns), pd.Series(exposures))
                if summary:
                    rows.append(
                        {
                            "amount_threshold": amount_threshold,
                            "vol_threshold": vol_threshold,
                            "industry_concentration_threshold": concentration_threshold,
                            **summary,
                        }
                    )
    return pd.DataFrame(rows).sort_values(["max_drawdown", "total_return"], ascending=[False, False])


def run(args: argparse.Namespace) -> dict[str, object]:
    strategies = _strategy_specs(_parse_csv(args.strategies, CORE_FIXED_STRATEGIES))
    scores, _prices, market_env = _prepare_scores(args, strategies)
    cycles, targets_cache = _build_core_cycles(scores, strategies, args.top_n)
    adaptive_cycles, adaptive_decisions = _build_adaptive_event_report(scores, targets_cache, strategies, args.top_n)
    combined_cycles = pd.concat([cycles, adaptive_cycles], ignore_index=True, sort=False)
    group_summary = _build_group_summary(cycles, _parse_csv(args.group_columns, DEFAULT_GROUP_COLUMNS))
    window_summary = _build_window_summary(combined_cycles, args.end_date or scores["trade_date"].max())
    grid = _grid_search(cycles) if args.grid_search else pd.DataFrame()

    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_core_strategy_style_research")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "cycles_csv": out_dir / "core_strategy_style_cycles.csv",
        "group_summary_csv": out_dir / "core_strategy_style_group_summary.csv",
        "window_summary_csv": out_dir / "core_strategy_style_window_summary.csv",
        "grid_search_csv": out_dir / "core_strategy_style_grid_search.csv",
        "adaptive_decisions_csv": out_dir / "core_strategy_style_adaptive_decisions.csv",
        "market_environment_csv": out_dir / "core_strategy_style_market_environment.csv",
        "json": out_dir / "core_strategy_style_report.json",
        "markdown": out_dir / "core_strategy_style_report.md",
    }
    cycles.to_csv(paths["cycles_csv"], index=False)
    group_summary.to_csv(paths["group_summary_csv"], index=False)
    window_summary.to_csv(paths["window_summary_csv"], index=False)
    grid.to_csv(paths["grid_search_csv"], index=False)
    adaptive_decisions.to_csv(paths["adaptive_decisions_csv"], index=False)
    market_env.to_csv(paths["market_environment_csv"], index=False)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": vars(args),
        "strategies": list(strategies),
        "window_summary": window_summary.to_dict("records"),
        "grid_search_top": grid.head(20).to_dict("records") if not grid.empty else [],
        "files": {key: str(value) for key, value in paths.items()},
        "pit_control": [
            "Only trusted core strategies are included by default.",
            "Adaptive decisions use T-day market fields and completed cycle performance where exit_date < signal_date.",
            "model_risk and bs_model_* strategies are excluded from production rule discovery.",
        ],
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    show = window_summary.copy()
    for col in ("total_return", "max_drawdown", "annualized_volatility", "win_rate", "avg_gross_exposure"):
        if col in show.columns:
            show[col] = show[col].map(lambda x: "" if pd.isna(x) else f"{float(x) * 100:.2f}%")
    lines = [
        "# 核心策略风格研究报告",
        "",
        "## 窗口收益风险",
        "",
        show.to_markdown(index=False) if not show.empty else "_无结果_",
        "",
        "## 网格搜索 Top10",
        "",
        grid.head(10).to_markdown(index=False) if not grid.empty else "_未启用或无结果_",
        "",
        "## 输出文件",
        "",
        *[f"- {key}: `{value}`" for key, value in report["files"].items()],
    ]
    paths["markdown"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Research market/industry/liquidity fit for core trusted strategies.")
    parser.add_argument("--start-date", default="2023-01-04")
    parser.add_argument("--end-date", default="2026-06-02")
    parser.add_argument("--strategies", default=",".join(CORE_FIXED_STRATEGIES))
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--min-pool-size", type=int, default=0)
    parser.add_argument("--dynamic-lookback-dates", type=int, default=20)
    parser.add_argument("--group-columns", default=",".join(DEFAULT_GROUP_COLUMNS))
    parser.add_argument("--grid-search", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2, default=str))
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc


if __name__ == "__main__":
    main()
