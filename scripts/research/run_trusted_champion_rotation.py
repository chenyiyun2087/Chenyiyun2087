"""Run trusted_champion_rotation_v1 from an immutable account backtest bundle.

This is a read-only research/disabled-shadow runner.  It never writes database
tables, creates orders, or changes production configuration.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.statistical_robustness import analyze_strategy_robustness
from scripts.research.trusted_champion_rotation import (
    STRATEGY_ID,
    RotationConfig,
    build_disabled_shadow_status,
    build_earnings_density,
    build_execution_hard_block_evidence,
    build_exposure_evidence,
    build_rotation_nav,
    load_rotation_config,
    performance_metrics,
    run_rotation_decisions,
    sha256_file,
    write_json,
)


POLICIES = ("pure_63", "pure_126", "no_earnings", "full")


def _read_csv(path: Path, required: bool = True) -> pd.DataFrame:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return pd.DataFrame()
    return pd.read_csv(path)


def _source_provenance(source: Path) -> dict[str, Any]:
    report_path = source / "trusted_account_backtest_report.json"
    if not report_path.exists():
        return {"strict_ledger_status": "MISSING", "corporate_action_coverage": None,
                "t_plus_one_violations": None, "order_conservation_errors": None}
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    provenance = payload.get("provenance") or {}
    return {
        "strict_ledger_status": provenance.get("ledger_implementation_status") or payload.get("strict_ledger_status") or "MISSING",
        "corporate_action_coverage": provenance.get("corporate_action_coverage"),
        "corporate_action_coverage_status": provenance.get("corporate_action_coverage_status"),
        "t_plus_one_violations": provenance.get("t_plus_1_fill_violations"),
        "order_conservation_errors": provenance.get("order_conservation_errors"),
        "lifecycle_session_coverage": provenance.get("lifecycle_session_coverage"),
        "strict_evidence_derived": provenance.get("strict_evidence_derived", False),
        "reproducibility_status": provenance.get("reproducibility_status") or "UNVERIFIED",
        "source_report_git_sha": provenance.get("report_git_sha"),
        "source_reproducibility_status": provenance.get("reproducibility_status") or "UNVERIFIED",
    }


def _normalize_curve(frame: pd.DataFrame, strategy: str, *, equity_col: str = "total_equity") -> pd.DataFrame:
    out = frame[frame["strategy"].astype(str).eq(strategy)].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out["equity"] = pd.to_numeric(out[equity_col], errors="coerce")
    out = out.dropna(subset=["trade_date", "equity"]).sort_values("trade_date")
    if out.empty:
        return pd.DataFrame()
    out["equity"] = out["equity"] / float(out["equity"].iloc[0])
    out["nav"] = out["equity"]
    out["switch_executed"] = 0
    out["extra_switch_cost"] = 0.0
    return out[["trade_date", "equity", "nav", "switch_executed", "extra_switch_cost"]]


def _quarterly_metrics(curve: pd.DataFrame, label: str, start: pd.Timestamp) -> pd.DataFrame:
    d = curve[curve["trade_date"].ge(start)].copy()
    if d.empty:
        return pd.DataFrame(columns=["curve", "quarter", "start", "end", "trading_days", "total_return", "max_drawdown"])
    d["quarter"] = d["trade_date"].dt.to_period("Q").astype(str)
    rows = []
    for quarter, group in d.groupby("quarter", sort=True):
        equity = group["equity"].astype(float)
        rows.append({
            "curve": label, "quarter": quarter, "start": group["trade_date"].min(),
            "end": group["trade_date"].max(), "trading_days": len(group),
            "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0),
            "max_drawdown": float((equity / equity.cummax() - 1.0).min()),
        })
    return pd.DataFrame(rows)


def _rolling_window_metrics(curve: pd.DataFrame, label: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    ordered = curve.sort_values("trade_date")
    for days in (30, 60, 90, 252):
        for end_index in range(days - 1, len(ordered)):
            window = ordered.iloc[end_index - days + 1:end_index + 1]
            equity = window["equity"].astype(float)
            daily = equity.pct_change().dropna()
            total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
            max_drawdown = float((equity / equity.cummax() - 1.0).min())
            rows.append({
                "curve": label, "window_days": days, "start": window.trade_date.iloc[0],
                "end": window.trade_date.iloc[-1], "total_return": total_return,
                "max_drawdown": max_drawdown,
                "volatility": float(daily.std(ddof=0) * np.sqrt(252.0)) if not daily.empty else 0.0,
                "sharpe": float(daily.mean() / daily.std(ddof=0) * np.sqrt(252.0))
                if not daily.empty and daily.std(ddof=0) > 0 else 0.0,
            })
    return pd.DataFrame(rows)


def _walk_forward_folds(curve: pd.DataFrame, config: RotationConfig) -> pd.DataFrame:
    """Report fixed-rule OOS folds; no parameter is fitted inside the folds."""
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(curve["trade_date"]).unique()))
    if dates.empty:
        return pd.DataFrame()
    wf = config.raw["walk_forward"]
    anchor = dates.min() + pd.DateOffset(months=int(wf["train_months"]))
    rows: list[dict[str, Any]] = []
    while anchor <= dates.max():
        validation_start = anchor + pd.offsets.BDay(int(wf["embargo_trading_days"]))
        validation_end = validation_start + pd.DateOffset(months=int(wf["validation_months"]))
        fold = curve[curve["trade_date"].between(validation_start, validation_end, inclusive="left")]
        if not fold.empty:
            metric = performance_metrics(fold)
            metric.update({
                "train_end": anchor - pd.offsets.BDay(int(wf["purge_trading_days"])),
                "validation_start": fold.trade_date.min(), "validation_end": fold.trade_date.max(),
                "fixed_rules_no_refit": True,
            })
            rows.append(metric)
        anchor += pd.DateOffset(months=int(wf["step_months"]))
    return pd.DataFrame(rows)


def _turnover_evidence(
    trades: pd.DataFrame, nav: pd.DataFrame, decisions: pd.DataFrame, config: RotationConfig,
) -> dict[str, Any]:
    required = {"strategy", "trade_date", "gross_amount"}
    if trades.empty or not required <= set(trades.columns):
        return {"status": "MISSING", "rotation_turnover": None, "baseline_turnover": None, "ratio": None}
    tx = trades.copy()
    tx["trade_date"] = pd.to_datetime(tx["trade_date"], errors="coerce")
    tx["gross_amount"] = pd.to_numeric(tx["gross_amount"], errors="coerce").fillna(0.0).abs()
    selection = decisions[["execution_date", "selected_strategy"]].dropna().copy()
    selection["execution_date"] = pd.to_datetime(selection["execution_date"], errors="coerce")
    picked = tx.merge(selection, left_on=["trade_date", "strategy"], right_on=["execution_date", "selected_strategy"], how="inner")
    equity = nav[["strategy", "trade_date", "total_equity"]].copy()
    equity["trade_date"] = pd.to_datetime(equity["trade_date"], errors="coerce")
    picked = picked.merge(equity, on=["strategy", "trade_date"], how="left")
    rotation_turnover = float((picked["gross_amount"] / pd.to_numeric(picked["total_equity"], errors="coerce")).sum())
    base = tx[tx.strategy.astype(str).eq(config.fallback_strategy)].merge(
        equity[equity.strategy.astype(str).eq(config.fallback_strategy)], on=["strategy", "trade_date"], how="left"
    )
    baseline_turnover = float((base["gross_amount"] / pd.to_numeric(base["total_equity"], errors="coerce")).sum())
    ratio = rotation_turnover / baseline_turnover if baseline_turnover > 0 else None
    return {"status": "ACCOUNT_TRADE_EVIDENCE", "rotation_turnover": rotation_turnover,
            "baseline_turnover": baseline_turnover, "ratio": ratio}


def _execution_diagnostics(trades: pd.DataFrame, candidates: pd.DataFrame, nav: pd.DataFrame) -> dict[str, Any]:
    impact = pd.to_numeric(candidates.get("estimated_turnover_impact", pd.Series(dtype=float)), errors="coerce").dropna()
    reject = trades.get("reject_reason", pd.Series(dtype=object)).fillna("").astype(str)
    signal = pd.to_datetime(trades.get("signal_date", pd.Series(dtype=object)), errors="coerce")
    execution = pd.to_datetime(trades.get("trade_date", pd.Series(dtype=object)), errors="coerce")
    delayed = int((execution - signal).dt.days.gt(3).sum()) if isinstance(signal, pd.Series) and isinstance(execution, pd.Series) else 0
    cash_ratio = pd.to_numeric(nav.get("unexpected_cash_residual_ratio", pd.Series(dtype=float)), errors="coerce").dropna()
    return {
        "candidate_count": int(len(candidates)), "trade_count": int(len(trades)),
        "execution_reject_count": int(reject.ne("").sum()),
        "t1_not_tradable_count": int(reject.eq("t1_not_tradable").sum()),
        "limit_block_count": int(reject.eq("limit_block").sum()),
        "delayed_execution_proxy_count": delayed,
        "estimated_impact_p95": float(impact.quantile(0.95)) if not impact.empty else None,
        "estimated_impact_max": float(impact.max()) if not impact.empty else None,
        "unexpected_cash_residual_ratio_mean": float(cash_ratio.mean()) if not cash_ratio.empty else None,
        "unexpected_cash_residual_ratio_max": float(cash_ratio.max()) if not cash_ratio.empty else None,
    }


def _oos_start(curve: pd.DataFrame, config: RotationConfig) -> pd.Timestamp:
    first = pd.Timestamp(curve["trade_date"].min())
    target = first + pd.DateOffset(months=int(config.raw["walk_forward"]["train_months"]))
    eligible = curve[curve["trade_date"].ge(target)]
    return pd.Timestamp(eligible["trade_date"].min()) if not eligible.empty else pd.NaT


def _metrics_after(curve: pd.DataFrame, start: pd.Timestamp) -> dict[str, Any]:
    if pd.isna(start):
        return {"status": "INSUFFICIENT_24M_WARMUP", "trading_days": 0}
    d = curve[curve["trade_date"].ge(start)].copy()
    result = performance_metrics(d)
    result["status"] = "PASS_WINDOW" if len(d) else "EMPTY"
    result["start"] = str(d["trade_date"].min().date()) if len(d) else None
    result["end"] = str(d["trade_date"].max().date()) if len(d) else None
    return result


def _forward_return(curve: pd.DataFrame, start: pd.Timestamp, sessions: int = 20) -> float | None:
    d = curve[curve["trade_date"].ge(start)].head(sessions + 1)
    if len(d) < sessions + 1:
        return None
    return float(d["equity"].iloc[-1] / d["equity"].iloc[0] - 1.0)


def _rotation_diagnostics(
    rotation: pd.DataFrame,
    decisions: pd.DataFrame,
    source_nav: pd.DataFrame,
    config: RotationConfig,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    curve = rotation.copy()
    curve["trade_date"] = pd.to_datetime(curve["trade_date"], errors="coerce")
    curve["signal_date"] = pd.to_datetime(curve["signal_date"], errors="coerce")
    decision_fields = decisions[["signal_date", "market_regime", "earnings_season"]].copy()
    decision_fields["signal_date"] = pd.to_datetime(decision_fields["signal_date"], errors="coerce")
    curve = curve.merge(decision_fields, on="signal_date", how="left")
    curve["quarter"] = curve["trade_date"].dt.to_period("Q").astype(str)
    curve["log_return"] = np.log1p(curve["daily_return"].clip(lower=-0.999999))
    selection = curve.groupby("selected_strategy").agg(
        trading_days=("trade_date", "size"), log_return_contribution=("log_return", "sum")
    ).reset_index()
    selection["selection_ratio"] = selection["trading_days"] / max(len(curve), 1)
    positive_strategy = selection[selection["log_return_contribution"].gt(0)]
    positive_total = float(positive_strategy["log_return_contribution"].sum())
    max_strategy_contribution = (
        float(positive_strategy["log_return_contribution"].max() / positive_total)
        if positive_total > 0 and not positive_strategy.empty else 1.0
    )
    quarter_log = curve.groupby("quarter")["log_return"].sum()
    positive_quarter = quarter_log[quarter_log.gt(0)]
    max_quarter_contribution = (
        float(positive_quarter.max() / positive_quarter.sum()) if positive_quarter.sum() > 0 else 1.0
    )
    earnings_attr = curve.groupby(curve["earnings_season"].fillna(0).astype(int))["log_return"].sum().to_dict()
    style_matrix = (
        curve.groupby(["market_regime", "selected_strategy"]).size().rename("trading_days").reset_index()
    )
    allowed = config.allowed_regimes
    style_matrix["regime_allowed"] = style_matrix.apply(
        lambda row: int(str(row.market_regime) in allowed.get(str(row.selected_strategy), set())), axis=1
    )
    switches = curve[curve["switch_executed"].eq(1)].copy()
    source = source_nav[source_nav["strategy"].astype(str).isin(config.strategy_ids)].copy()
    source["trade_date"] = pd.to_datetime(source["trade_date"], errors="coerce")
    source["equity"] = pd.to_numeric(source["total_equity"], errors="coerce")
    switch_rows: list[dict[str, Any]] = []
    for idx, event in switches.iterrows():
        date = pd.Timestamp(event.trade_date)
        current = str(event.selected_strategy)
        previous_rows = curve[curve["trade_date"].lt(date)]
        previous = str(previous_rows.iloc[-1].selected_strategy) if not previous_rows.empty else current
        selected_curve = source[source["strategy"].eq(current)][["trade_date", "equity"]].sort_values("trade_date")
        previous_curve = source[source["strategy"].eq(previous)][["trade_date", "equity"]].sort_values("trade_date")
        selected_forward = _forward_return(selected_curve, date)
        previous_forward = _forward_return(previous_curve, date)
        rotation_forward = _forward_return(curve[["trade_date", "equity"]], date)
        before = curve[curve["trade_date"].lt(date)].tail(21)
        pre_return = float(before.equity.iloc[-1] / before.equity.iloc[0] - 1.0) if len(before) >= 21 else None
        wrong = (
            selected_forward is not None and previous_forward is not None
            and selected_forward <= previous_forward
        )
        switch_rows.append({
            "execution_date": date, "from_strategy": previous, "to_strategy": current,
            "pre_20d_rotation_return": pre_return, "post_20d_rotation_return": rotation_forward,
            "selected_strategy_post_20d_return": selected_forward,
            "previous_strategy_post_20d_return": previous_forward,
            "wrong_switch_20d": int(wrong) if selected_forward is not None and previous_forward is not None else np.nan,
        })
    switch_events = pd.DataFrame(switch_rows)
    assessed = pd.to_numeric(switch_events.get("wrong_switch_20d"), errors="coerce").dropna() if not switch_events.empty else pd.Series(dtype=float)
    diagnostics = {
        "switch_count": int(len(switch_events)),
        "assessed_switch_count": int(len(assessed)),
        "wrong_switch_rate_20d": float(assessed.mean()) if not assessed.empty else None,
        "max_single_strategy_selection_ratio": float(selection["selection_ratio"].max()) if not selection.empty else 1.0,
        "max_single_strategy_profit_contribution": max_strategy_contribution,
        "max_single_quarter_profit_contribution": max_quarter_contribution,
        "earnings_season_log_return": float(earnings_attr.get(1, 0.0)),
        "non_earnings_log_return": float(earnings_attr.get(0, 0.0)),
        "regime_disallowed_days": int(style_matrix.loc[style_matrix.regime_allowed.eq(0), "trading_days"].sum()),
    }
    return diagnostics, switch_events, style_matrix.merge(selection, on="selected_strategy", how="left")


def _acceptance(
    full_curve: pd.DataFrame,
    baseline_curve: pd.DataFrame,
    stress_curve: pd.DataFrame,
    quarters: pd.DataFrame,
    config: RotationConfig,
    provenance: dict[str, Any],
    robustness: dict[str, Any] | None,
    diagnostics: dict[str, Any],
    turnover_evidence: dict[str, Any],
) -> dict[str, Any]:
    start = _oos_start(full_curve, config)
    full = _metrics_after(full_curve, start)
    baseline = _metrics_after(baseline_curve, start)
    stress = _metrics_after(stress_curve, start)
    q = quarters[(quarters["curve"].eq("full")) & quarters["start"].ge(start)] if not quarters.empty and pd.notna(start) else pd.DataFrame()
    positive_quarters = float((q["total_return"] > 0).mean()) if not q.empty else 0.0
    thresholds = config.raw["acceptance"]
    rotation_calmar = float(full.get("calmar") or 0.0)
    baseline_calmar = float(baseline.get("calmar") or 0.0)
    calmar_improvement = ((rotation_calmar / baseline_calmar - 1.0) * 100.0) if baseline_calmar > 0 else -np.inf
    strict_verified = provenance.get("strict_ledger_status") == "VERIFIED"
    ca_coverage = provenance.get("corporate_action_coverage")
    checks = {
        "oos_24m_warmup_available": full.get("status") == "PASS_WINDOW",
        "oos_annualized_return": float(full.get("annualized_return") or 0.0) >= float(thresholds["min_oos_annualized_return"]),
        "return_above_baseline": float(full.get("annualized_return") or 0.0) > float(baseline.get("annualized_return") or 0.0),
        "oos_max_drawdown": abs(float(full.get("max_drawdown") or 0.0)) <= float(thresholds["max_oos_drawdown_abs"]),
        "positive_quarters": positive_quarters >= float(thresholds["min_positive_quarter_ratio"]),
        "calmar": rotation_calmar >= float(thresholds["min_calmar"]),
        "calmar_improvement": calmar_improvement >= float(thresholds["min_calmar_improvement_pct"]),
        "stress_return": float(stress.get("annualized_return") or 0.0) >= float(thresholds["min_stress_annualized_return"]),
        "dsr": robustness is not None and float(robustness.get("deflated_sharpe_confidence") or 0.0) >= float(thresholds["min_dsr_confidence"]),
        "pbo": robustness is not None and float(robustness.get("pbo") or 1.0) <= float(thresholds["max_pbo"]),
        "corporate_actions": ca_coverage is not None and float(ca_coverage) >= float(thresholds["required_corporate_action_coverage"]),
        "strict_ledger": strict_verified,
        "strict_evidence_derived": provenance.get("strict_evidence_derived") is True,
        "lifecycle_coverage": float(provenance.get("lifecycle_session_coverage") or 0.0) >= 1.0,
        "reproducibility": provenance.get("reproducibility_status") == "REPRODUCIBLE",
        "t_plus_one": provenance.get("t_plus_one_violations") == int(thresholds["max_t_plus_one_violations"]),
        "order_conservation": provenance.get("order_conservation_errors") == int(thresholds["max_order_conservation_errors"]),
        "quarter_profit_concentration": float(diagnostics["max_single_quarter_profit_contribution"]) <= float(thresholds["max_single_quarter_profit_contribution"]),
        "strategy_profit_concentration": float(diagnostics["max_single_strategy_profit_contribution"]) <= float(thresholds["max_single_strategy_profit_contribution"]),
        "enough_switches_for_attribution": int(diagnostics["assessed_switch_count"]) >= int(thresholds["min_switches_for_attribution"]),
        "regime_compatibility": int(diagnostics["regime_disallowed_days"]) == 0,
        "turnover": turnover_evidence.get("ratio") is not None and float(turnover_evidence["ratio"]) <= float(thresholds["max_turnover_ratio"]),
    }
    return {
        "passed": bool(checks) and all(checks.values()), "checks": checks,
        "blockers": [name for name, passed in checks.items() if not passed],
        "oos_metrics": full, "baseline_oos_metrics": baseline, "stress_oos_metrics": stress,
        "positive_quarter_ratio": positive_quarters, "calmar_improvement_pct": calmar_improvement,
        "evidence_status": "RESEARCH_PROXY_LEDGER_REQUIRED",
        "turnover_evidence": turnover_evidence,
    }


def _markdown_report(summary: dict[str, Any], comparisons: pd.DataFrame, acceptance: dict[str, Any], shadow: dict[str, Any]) -> str:
    show = comparisons.copy()
    for col in ("total_return", "annualized_return", "max_drawdown", "annualized_volatility"):
        if col in show:
            show[col] = show[col].map(lambda value: f"{float(value):.2%}")
    return "\n".join([
        "# Trusted Champion Rotation v1 研究报告", "",
        f"- 生成时间：`{summary['generated_at']}`",
        f"- 数据区间：`{summary['data_start']}` 至 `{summary['data_end']}`",
        "- 状态：`RESEARCH_PROXY_LEDGER_REQUIRED`",
        "- 本运行未生成订单、未写数据库、未修改生产配置。", "",
        "## 对照曲线", "", show.to_markdown(index=False), "",
        "## 晋级门禁", "",
        f"- 通过：`{acceptance['passed']}`",
        f"- 阻塞：`{' | '.join(acceptance['blockers']) or 'none'}`",
        f"- OOS正收益季度比例：`{acceptance['positive_quarter_ratio']:.2%}`", "",
        "## Disabled Shadow", "",
        f"- 观察交易日：`{shadow['observed_trade_days']}/{shadow['required_trade_days']}`",
        f"- 晋级准备：`{shadow['promotion_ready']}`",
        f"- 阻塞：`{' | '.join(shadow['blockers']) or 'none'}`", "",
        "## 解释边界", "",
        "轮动净值使用已保存底层账户净值，并在策略切换时额外扣除双边成本和滑点。",
        "它可以验证冠军选择的因果时序；换手从账户交易明细计算，但账本门禁仍要求来源报告明确 VERIFIED。",
    ])


def run(args: argparse.Namespace) -> Path:
    source = Path(args.source_dir).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"immutable output directory already exists: {output}")
    output.mkdir(parents=True)
    config_path = Path(args.config).resolve()
    config = load_rotation_config(config_path)
    paths = {
        "nav": source / "trusted_account_backtest_nav.csv",
        "positions": source / "trusted_account_backtest_positions.csv",
        "market": source / "trusted_account_backtest_market_environment.csv",
        "trades": source / "trusted_account_backtest_trades.csv",
        "candidates": source / "trusted_account_backtest_candidates.csv",
        "earnings": Path(args.earnings_announcements).resolve(),
        "universe": Path(args.eligible_universe).resolve(),
        "config": config_path,
    }
    nav = _read_csv(paths["nav"])
    positions = _read_csv(paths["positions"])
    market = _read_csv(paths["market"])
    announcements = _read_csv(paths["earnings"])
    universe = _read_csv(paths["universe"])
    candidates = _read_csv(paths["candidates"], required=False)
    trades = _read_csv(paths["trades"], required=False)
    exposure = build_exposure_evidence(positions, config.strategy_ids)
    execution_evidence = build_execution_hard_block_evidence(candidates)
    trade_dates = sorted(pd.to_datetime(nav.loc[nav["strategy"].astype(str).isin(config.strategy_ids), "trade_date"], errors="coerce").dropna().unique())
    earnings = build_earnings_density(trade_dates, announcements, universe, int(config.raw["earnings_season"]["lookback_trade_days"]))
    curves: dict[str, pd.DataFrame] = {}
    decisions_by_policy: dict[str, pd.DataFrame] = {}
    for policy in POLICIES:
        decisions = run_rotation_decisions(nav, market, earnings, exposure, config, policy=policy)
        curve = build_rotation_nav(nav, decisions, config)
        curve["curve"] = policy
        decisions_by_policy[policy] = decisions
        curves[policy] = curve
    baseline = _normalize_curve(nav, config.fallback_strategy)
    baseline["curve"] = "production_core"
    curves["production_core"] = baseline
    adaptive = _normalize_curve(nav, "adaptive_market_style")
    if not adaptive.empty:
        adaptive["curve"] = "adaptive_market_style"
        curves["adaptive_market_style"] = adaptive
    stress_raw = copy.deepcopy(config.raw)
    stress_raw["execution"]["base_cost_rate"] = stress_raw["execution"]["stress_cost_rate"]
    stress_raw["execution"]["base_slippage_bps"] = stress_raw["execution"]["stress_slippage_bps"]
    stress_curve = build_rotation_nav(nav, decisions_by_policy["full"], RotationConfig(stress_raw))
    stress_curve["curve"] = "full_stress_switch_cost_proxy"
    curves["full_stress_switch_cost_proxy"] = stress_curve

    comparisons = []
    quarters = []
    for label, curve in curves.items():
        metric = performance_metrics(curve)
        metric["curve"] = label
        comparisons.append(metric)
        start = _oos_start(curve, config)
        if pd.notna(start):
            quarters.append(_quarterly_metrics(curve, label, start))
    comparison_frame = pd.DataFrame(comparisons)
    quarter_frame = pd.concat(quarters, ignore_index=True) if quarters else pd.DataFrame()
    rolling_frame = pd.concat(
        [_rolling_window_metrics(curve, label) for label, curve in curves.items()], ignore_index=True
    )
    walk_forward_frame = _walk_forward_folds(curves["full"], config)
    robustness_dict = None
    if not args.skip_robustness and len(curves["full"]) >= 60:
        report = analyze_strategy_robustness(curves["full"]["daily_return"].astype(float).tolist(), strategy_name=STRATEGY_ID, n_trials=4)
        robustness_dict = asdict(report)
    provenance = _source_provenance(source)
    diagnostics, switch_events, style_attribution = _rotation_diagnostics(
        curves["full"], decisions_by_policy["full"], nav, config
    )
    turnover_evidence = _turnover_evidence(trades, nav, decisions_by_policy["full"], config)
    execution_diagnostics = _execution_diagnostics(trades, candidates, nav)
    acceptance = _acceptance(
        curves["full"], baseline, stress_curve, quarter_frame, config,
        provenance, robustness_dict, diagnostics, turnover_evidence,
    )
    shadow = build_disabled_shadow_status(
        decisions_by_policy["full"], curves["full"], config,
        corporate_action_coverage=provenance.get("corporate_action_coverage"),
        strict_ledger_status=provenance.get("strict_ledger_status"),
        t_plus_one_violations=provenance.get("t_plus_one_violations"),
        order_conservation_errors=provenance.get("order_conservation_errors"),
        observation_start=args.shadow_observation_start,
        execution_evidence=execution_evidence,
    )
    input_hashes = {name: sha256_file(path) for name, path in paths.items() if path.exists()}
    summary = {
        "strategy_id": STRATEGY_ID, "generated_at": datetime.now().isoformat(),
        "source_dir": str(source), "output_dir": str(output),
        "data_start": str(pd.to_datetime(nav["trade_date"]).min().date()),
        "data_end": str(pd.to_datetime(nav["trade_date"]).max().date()),
        "execution_mode": "signal_close_select; next_trade_date_return; order_ledger_required",
        "production_mutation_enabled": False, "order_generation_enabled": False,
        "input_hashes": input_hashes, "source_provenance": provenance,
        "acceptance": acceptance, "disabled_shadow": shadow,
        "diagnostics": diagnostics,
        "execution_diagnostics": execution_diagnostics,
    }
    decisions_by_policy["full"].to_csv(output / "champion_rotation_decisions.csv", index=False)
    pd.concat(curves.values(), ignore_index=True, sort=False).to_csv(output / "champion_rotation_nav.csv", index=False)
    comparison_frame.to_csv(output / "champion_rotation_comparison.csv", index=False)
    quarter_frame.to_csv(output / "champion_rotation_quarterly_oos.csv", index=False)
    rolling_frame.to_csv(output / "champion_rotation_rolling_30_60_90_252.csv", index=False)
    walk_forward_frame.to_csv(output / "champion_rotation_walk_forward_oos.csv", index=False)
    switch_events.to_csv(output / "champion_rotation_switch_attribution.csv", index=False)
    style_attribution.to_csv(output / "champion_rotation_style_attribution.csv", index=False)
    exposure.to_csv(output / "champion_rotation_exposure_evidence.csv", index=False)
    earnings.to_csv(output / "champion_rotation_earnings_density.csv", index=False)
    if robustness_dict is not None:
        write_json(output / "champion_rotation_robustness.json", robustness_dict)
    write_json(output / "champion_rotation_acceptance.json", acceptance)
    write_json(output / "champion_rotation_diagnostics.json", diagnostics)
    write_json(output / "champion_rotation_execution_diagnostics.json", execution_diagnostics)
    write_json(output / "disabled_shadow_status.json", shadow)
    write_json(output / "champion_rotation_manifest.json", summary)
    (output / "champion_rotation_report.md").write_text(
        _markdown_report(summary, comparison_frame, acceptance, shadow), encoding="utf-8"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trusted champion rotation research and disabled-shadow audit.")
    parser.add_argument("--source-dir", required=True, help="Saved trusted account backtest bundle containing all four exact strategies.")
    parser.add_argument("--earnings-announcements", required=True, help="CSV with symbol, ann_date; ann_date is the point-in-time availability field.")
    parser.add_argument("--eligible-universe", required=True, help="CSV with trade_date+symbol or trade_date+eligible_universe_count.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "trusted_champion_rotation_v1.yaml"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "exports" / "trusted_champion_rotation" / datetime.now().strftime("%Y%m%d_%H%M%S")))
    parser.add_argument("--skip-robustness", action="store_true")
    parser.add_argument("--shadow-observation-start", default=None, help="First real disabled-shadow signal date; omitted means historical rows count as zero real days.")
    args = parser.parse_args()
    output = run(args)
    print(json.dumps({"status": "SUCCESS_RESEARCH_ONLY", "output_dir": str(output), "orders_generated": False, "production_modified": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
