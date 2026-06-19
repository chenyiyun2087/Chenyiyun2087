"""Simulate execution-safe recovery uplift for v1.2b research shadow candidates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.run_research_shadow_candidate_monitor import CANDIDATES_FILE, EXECUTION_PROXY_COLUMNS, NAV_FILE
from scripts.research.execution_risk_severity import add_execution_severity_columns
from scripts.research.analyze_pattern_veto_coverage import _rank_candidates


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/execution_safe_recovery_uplift")
DEFAULT_EVENT_LOG = Path("reports/production_monitor/research_shadow_event_log.csv")


def _read_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"Missing {label}: {path}")
    frame = pd.read_csv(path, low_memory=False)
    if frame.empty:
        raise RuntimeError(f"{label} is empty: {path}")
    return frame


def _date_col(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "trade_date" not in out.columns:
        if "execution_date" in out.columns:
            out["trade_date"] = out["execution_date"]
        elif "signal_date" in out.columns:
            out["trade_date"] = out["signal_date"]
        else:
            raise RuntimeError("missing trade_date/execution_date/signal_date column")
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.strftime("%Y-%m-%d")
    return out


def _strategy_nav(nav: pd.DataFrame, strategy: str) -> pd.DataFrame:
    part = nav[nav["strategy"].astype(str).eq(strategy)].copy()
    if part.empty:
        raise RuntimeError(f"nav missing strategy: {strategy}")
    part = _date_col(part).sort_values("trade_date")
    part["theory_return"] = pd.to_numeric(part["nav"], errors="coerce").pct_change().fillna(0.0)
    return part[["trade_date", "nav", "theory_return"]]


def _event_mask(frame: pd.DataFrame) -> pd.Series:
    position_diff = pd.to_numeric(frame.get("position_diff"), errors="coerce").fillna(0.0)
    risk_diff = frame.get("risk_decision_diff", pd.Series(False, index=frame.index)).astype(bool)
    recovery_decision = frame.get("shadow_risk_decision", pd.Series("", index=frame.index)).astype(str).eq("recovery_reduce")
    recovery_status = frame.get("shadow_recovery_status", pd.Series("", index=frame.index)).astype(str).str.contains("recover", case=False, na=False)
    shadow_position = pd.to_numeric(frame.get("shadow_target_position"), errors="coerce").fillna(0.0)
    production_position = pd.to_numeric(frame.get("production_target_position"), errors="coerce").fillna(0.0)
    return recovery_decision | recovery_status | position_diff.ne(0) | risk_diff | shadow_position.gt(production_position)


def _blocked_share(candidates: pd.DataFrame, strategy: str, trade_date: str, mode: str) -> float:
    part = candidates[candidates["strategy"].astype(str).eq(strategy) & candidates["trade_date"].astype(str).eq(trade_date)].copy()
    if part.empty:
        return 0.0
    ranked = _rank_candidates(part)
    top5 = ranked[pd.to_numeric(ranked["candidate_rank"], errors="coerce").le(5)].copy()
    if top5.empty:
        return 0.0
    weights = pd.Series(0.0, index=top5.index)
    for weight_col in ("adjusted_target_weight", "target_weight", "raw_effective_weight"):
        if weight_col in top5.columns:
            weights = pd.to_numeric(top5[weight_col], errors="coerce").abs().fillna(0.0)
            if float(weights.sum()) > 0:
                break
    total = float(weights.sum())
    if total <= 0:
        weights = pd.Series(1.0, index=top5.index)
        total = float(weights.sum())
    if mode == "open_gap":
        mask = pd.to_numeric(top5.get("open_gap_proxy"), errors="coerce").abs().gt(0.05)
    elif mode == "large_slippage":
        mask = pd.to_numeric(top5.get("large_slippage_proxy"), errors="coerce").gt(0.03)
    elif mode == "hard_block":
        mask = (
            pd.to_numeric(top5.get("open_gap_proxy"), errors="coerce").abs().gt(0.05)
            | pd.to_numeric(top5.get("limit_up_buy_ratio"), errors="coerce").gt(0.20)
            | pd.to_numeric(top5.get("limit_down_sell_ratio"), errors="coerce").gt(0.20)
            | pd.to_numeric(top5.get("estimated_turnover_impact"), errors="coerce").gt(0.03)
        )
    else:
        mask = pd.Series(False, index=top5.index)
    return max(0.0, min(1.0, float(weights[mask].sum()) / total))


def _nav_from_returns(frame: pd.DataFrame, return_col: str) -> pd.Series:
    returns = pd.to_numeric(frame[return_col], errors="coerce").fillna(0.0)
    return (1.0 + returns).cumprod()


def _metrics(frame: pd.DataFrame, return_col: str, nav_col: str) -> dict[str, float]:
    nav = pd.to_numeric(frame[nav_col], errors="coerce").dropna()
    returns = pd.to_numeric(frame[return_col], errors="coerce").fillna(0.0)
    if nav.empty:
        return {"total_return": 0.0, "annualized_return": 0.0, "max_drawdown": 0.0}
    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0) if nav.iloc[0] else 0.0
    years = max(len(nav) / 252.0, 1 / 252.0)
    annualized = float((1.0 + total_return) ** (1.0 / years) - 1.0) if total_return > -1 else -1.0
    drawdown = nav / nav.cummax() - 1.0
    return {
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": float(drawdown.min()),
        "mean_daily_return": float(returns.mean()),
    }


def build_uplift_simulation(
    nav: pd.DataFrame,
    monitor: pd.DataFrame,
    candidates: pd.DataFrame,
    production_strategy: str,
    shadow_strategy: str,
) -> dict[str, pd.DataFrame]:
    prod = _strategy_nav(nav, production_strategy).rename(columns={"nav": "production_nav", "theory_return": "production_return"})
    shadow = _strategy_nav(nav, shadow_strategy).rename(columns={"nav": "shadow_nav", "theory_return": "shadow_return"})
    monitor = _date_col(monitor)
    candidates = _date_col(candidates)
    frame = prod.merge(shadow, on="trade_date", how="inner").merge(monitor, on="trade_date", how="inner", suffixes=("", "_monitor"))
    if frame.empty:
        raise RuntimeError("No overlapping dates among nav and shadow monitor.")
    frame = add_execution_severity_columns(frame)
    frame["is_uplift_event"] = _event_mask(frame)
    frame["incremental_gap"] = pd.to_numeric(frame["shadow_return"], errors="coerce").fillna(0.0) - pd.to_numeric(frame["production_return"], errors="coerce").fillna(0.0)
    frame["blocked_share_open_gap"] = [
        _blocked_share(candidates, shadow_strategy, str(day), "open_gap") for day in frame["trade_date"]
    ]
    frame["blocked_share_large_slippage"] = [
        _blocked_share(candidates, shadow_strategy, str(day), "large_slippage") for day in frame["trade_date"]
    ]
    frame["blocked_share_hard_block"] = [
        _blocked_share(candidates, shadow_strategy, str(day), "hard_block") for day in frame["trade_date"]
    ]
    event = frame["is_uplift_event"].astype(bool)
    hard = frame["execution_hard_block"].astype(bool) | frame["blocked_share_hard_block"].gt(0)
    frame["sim_original_return"] = frame["shadow_return"]
    frame["sim_hard_block_fallback_return"] = frame["shadow_return"].where(~(event & hard), frame["production_return"])
    frame["sim_open_gap_downweight_return"] = frame["production_return"] + frame["incremental_gap"] * (1.0 - frame["blocked_share_open_gap"].where(event, 0.0))
    frame["sim_large_slippage_downweight_return"] = frame["production_return"] + frame["incremental_gap"] * (1.0 - frame["blocked_share_large_slippage"].where(event, 0.0))
    for col in (
        "production_return",
        "shadow_return",
        "sim_original_return",
        "sim_hard_block_fallback_return",
        "sim_open_gap_downweight_return",
        "sim_large_slippage_downweight_return",
    ):
        frame[col.replace("_return", "_nav_index")] = _nav_from_returns(frame, col)
    events = frame[event].copy()
    promotion_valid = events[~events["execution_hard_block"].astype(bool)].copy()
    promotion_valid = promotion_valid[promotion_valid["blocked_share_hard_block"].le(0)].copy()
    summary_rows: list[dict[str, object]] = []
    for label, ret_col in [
        ("production", "production_return"),
        ("shadow_original", "shadow_return"),
        ("hard_block_fallback", "sim_hard_block_fallback_return"),
        ("open_gap_downweight", "sim_open_gap_downweight_return"),
        ("large_slippage_downweight", "sim_large_slippage_downweight_return"),
    ]:
        nav_col = ret_col.replace("_return", "_nav_index")
        row = {"scenario": label, **_metrics(frame, ret_col, nav_col)}
        scenario_gap = pd.to_numeric(frame[ret_col], errors="coerce").fillna(0.0) - pd.to_numeric(frame["production_return"], errors="coerce").fillna(0.0)
        event_gap = scenario_gap[event]
        row.update(
            {
                "event_count": int(event.sum()),
                "event_theory_gap": float(event_gap.sum()),
                "event_positive_rate": float(event_gap.gt(0).mean()) if len(event_gap) else 0.0,
                "hard_block_event_count": int((event & hard).sum()),
                "promotion_valid_event_count": int(len(promotion_valid)),
            }
        )
        summary_rows.append(row)
    promotion_gap = pd.to_numeric(promotion_valid["shadow_return"], errors="coerce").fillna(0.0) - pd.to_numeric(promotion_valid["production_return"], errors="coerce").fillna(0.0)
    promotion_summary = pd.DataFrame(
        [
            {
                "promotion_valid_event_count": int(len(promotion_valid)),
                "promotion_valid_positive_rate": float(promotion_gap.gt(0).mean()) if len(promotion_gap) else 0.0,
                "promotion_valid_cumulative_gap": float(promotion_gap.sum()) if len(promotion_gap) else 0.0,
                "promotion_valid_event_window_gap": float(promotion_gap.sum()) if len(promotion_gap) else 0.0,
                "promotion_valid_hard_block_count": int((event & hard).sum()),
                "promotion_valid_slippage_warning_count": int(promotion_valid["execution_slippage_warning"].astype(bool).sum()) if not promotion_valid.empty else 0,
            }
        ]
    )
    return {
        "simulated_nav": frame,
        "uplift_counterfactual_by_day": events,
        "uplift_counterfactual_summary": pd.DataFrame(summary_rows),
        "promotion_valid_events": promotion_valid,
        "promotion_valid_event_summary": promotion_summary,
    }


def _markdown(summary: dict[str, object], tables: dict[str, pd.DataFrame]) -> str:
    lines = [
        "# Execution-Safe Recovery Uplift Report",
        "",
        f"- generated_at: `{summary.get('generated_at')}`",
        f"- production_strategy: `{summary.get('production_strategy')}`",
        f"- shadow_strategy: `{summary.get('shadow_strategy')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
        "## Counterfactual Summary",
        "",
        "| scenario | total_return | annualized_return | max_drawdown | event_count | event_theory_gap | hard_block_event_count |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in tables["uplift_counterfactual_summary"].to_dict("records"):
        lines.append(
            "| {scenario} | {total_return} | {annualized_return} | {max_drawdown} | {event_count} | {event_theory_gap} | {hard_block_event_count} |".format(
                **row
            )
        )
    promo = tables["promotion_valid_event_summary"].iloc[0].to_dict()
    lines.extend(
        [
            "",
            "## Promotion Valid Events",
            "",
            "| metric | value |",
            "|---|---:|",
        ]
    )
    for key, value in promo.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "This simulation is research-only and does not change production, shadow config, orders, or governor parameters."])
    return "\n".join(lines) + "\n"


def run_analysis(
    backtest_dir: Path,
    monitor_csv: Path,
    production_strategy: str,
    shadow_strategy: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, object]:
    nav = _read_csv(backtest_dir / NAV_FILE, "backtest nav")
    candidates = _read_csv(backtest_dir / CANDIDATES_FILE, "backtest candidates")
    monitor = _read_csv(monitor_csv, "shadow monitor")
    tables = build_uplift_simulation(nav, monitor, candidates, production_strategy, shadow_strategy)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_execution_safe_recovery_uplift")
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for name, table in tables.items():
        path = out_dir / f"{name}.csv"
        table.to_csv(path, index=False)
        files[name] = str(path)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_dir": str(backtest_dir),
        "monitor_csv": str(monitor_csv),
        "production_strategy": production_strategy,
        "shadow_strategy": shadow_strategy,
        "output_dir": str(out_dir),
        "files": files,
    }
    summary.update(tables["promotion_valid_event_summary"].iloc[0].to_dict())
    summary_path = out_dir / "execution_safe_recovery_uplift_report.json"
    md_path = out_dir / "execution_safe_recovery_uplift_report.md"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(summary, tables), encoding="utf-8")
    summary["files"]["execution_safe_recovery_uplift_report_json"] = str(summary_path)
    summary["files"]["execution_safe_recovery_uplift_report_md"] = str(md_path)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate execution-safe recovery uplift counterfactuals.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--monitor-csv", required=True)
    parser.add_argument("--production-strategy", default="production_governed_vol_position")
    parser.add_argument("--shadow-strategy", default="production_governed_vol_position_v1_2b_gate_tuned")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(
        json.dumps(
            run_analysis(
                Path(args.backtest_dir),
                Path(args.monitor_csv),
                args.production_strategy,
                args.shadow_strategy,
                Path(args.output_root),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
