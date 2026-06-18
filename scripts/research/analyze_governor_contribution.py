"""Analyze contribution and opportunity cost of the production risk governor."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/governor_contribution")
GOVERNED = "production_governed_vol_position"
GOVERNED_V2 = "production_governed_vol_position_v2"
GOVERNED_V1_1 = "production_governed_vol_position_v1_1_recovery"
GOVERNED_V1_1_PATTERN_VETO = "production_governed_vol_position_v1_1_recovery_pattern_veto"
GOVERNED_V1_2 = "production_governed_vol_position_v1_2_recovery"
GOVERNED_V1_2_PATTERN_VETO = "production_governed_vol_position_v1_2_recovery_pattern_veto"
GOVERNED_V1_2B = "production_governed_vol_position_v1_2b_dynamic_score"
GOVERNED_V1_2B_PATTERN_VETO = "production_governed_vol_position_v1_2b_dynamic_score_pattern_veto"
BASELINE = "baseline_full_liquidity_detail_vol_position"
HORIZONS = (5, 10, 20)
REDUCE_DECISIONS = {"reduce_position", "soft_reduce", "hard_reduce", "recovery_reduce"}


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _daily_nav_returns(nav: pd.DataFrame) -> pd.DataFrame:
    frame = nav[["strategy", "trade_date", "nav", "gross_exposure"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values(["strategy", "trade_date"])
    frame["daily_return"] = frame.groupby("strategy")["nav"].pct_change().fillna(0.0)
    return frame


def _forward_metrics(series: pd.Series, idx: int, horizon: int) -> tuple[float, float]:
    window = series.iloc[idx + 1 : idx + 1 + horizon]
    if len(window) < horizon:
        return np.nan, np.nan
    cumulative = float((1.0 + window).prod() - 1.0)
    curve = (1.0 + window).cumprod()
    drawdown = float((curve / curve.cummax() - 1.0).min())
    return cumulative, drawdown


def build_risk_decision_forward_returns(nav: pd.DataFrame, strategy: str = GOVERNED) -> pd.DataFrame:
    governed = _daily_nav_returns(nav)
    governed = governed[governed["strategy"].eq(strategy)].copy().reset_index(drop=True)
    if governed.empty or "risk_decision" not in nav.columns:
        raise RuntimeError("Missing governed nav or risk_decision columns.")
    meta_cols = ["trade_date", "risk_decision", "position_ratio", "target_position_ratio", "risk_governor_reasons"]
    missing = [col for col in meta_cols if col not in nav.columns]
    if missing:
        raise RuntimeError(f"Missing governed nav columns for attribution: {missing}")
    meta = nav[nav["strategy"].eq(strategy)][meta_cols].copy()
    meta["trade_date"] = pd.to_datetime(meta["trade_date"])
    governed = governed.merge(meta, on="trade_date", how="left")
    rows = []
    returns = governed["daily_return"]
    for idx, row in governed.iterrows():
        out = {
            "trade_date": row["trade_date"].strftime("%Y-%m-%d"),
            "risk_decision": row.get("risk_decision"),
            "gross_exposure": row.get("gross_exposure"),
            "position_ratio": row.get("position_ratio"),
            "target_position_ratio": row.get("target_position_ratio"),
            "risk_governor_reasons": row.get("risk_governor_reasons"),
        }
        for horizon in HORIZONS:
            ret, dd = _forward_metrics(returns, idx, horizon)
            out[f"next_{horizon}d_return"] = ret
            out[f"max_dd_{horizon}d"] = dd
        rows.append(out)
    return pd.DataFrame(rows)


def _split_reasons(value: object) -> list[str]:
    text_value = str(value or "").strip()
    if not text_value or text_value.lower() == "nan":
        return ["missing_reason"]
    return [part.strip() for part in text_value.replace(",", "|").split("|") if part.strip()] or ["missing_reason"]


def build_risk_reason_forward_returns(forward: pd.DataFrame, opportunity: pd.DataFrame | None = None) -> pd.DataFrame:
    if "risk_governor_reasons" not in forward.columns:
        raise RuntimeError("Missing risk_governor_reasons for reason-level attribution.")
    frame = forward.copy()
    if opportunity is not None and not opportunity.empty:
        frame = frame.merge(
            opportunity[["trade_date", "opportunity_cost"]].copy(),
            on="trade_date",
            how="left",
        )
        frame["prevented_loss"] = np.where(pd.to_numeric(frame["opportunity_cost"], errors="coerce") < 0, -pd.to_numeric(frame["opportunity_cost"], errors="coerce"), 0.0)
    rows: list[dict[str, object]] = []
    for row in frame.to_dict("records"):
        for reason in _split_reasons(row.get("risk_governor_reasons")):
            out = dict(row)
            out["risk_reason"] = reason
            rows.append(out)
    return pd.DataFrame(rows)


def build_risk_reason_effectiveness(reason_forward: pd.DataFrame) -> pd.DataFrame:
    if reason_forward.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for reason, part in reason_forward.groupby("risk_reason", dropna=False):
        row: dict[str, object] = {
            "risk_reason": reason,
            "days": int(len(part)),
            "false_positive_days": int(
                (
                    pd.to_numeric(part.get("next_10d_return"), errors="coerce").gt(0.03)
                    | pd.to_numeric(part.get("next_20d_return"), errors="coerce").gt(0.05)
                ).sum()
            ),
        }
        row["false_positive_rate"] = float(row["false_positive_days"] / row["days"]) if row["days"] else np.nan
        for horizon in HORIZONS:
            row[f"avg_next_{horizon}d_return"] = float(pd.to_numeric(part.get(f"next_{horizon}d_return"), errors="coerce").mean())
            row[f"avg_max_dd_{horizon}d"] = float(pd.to_numeric(part.get(f"max_dd_{horizon}d"), errors="coerce").mean())
        row["avg_opportunity_cost"] = float(pd.to_numeric(part.get("opportunity_cost"), errors="coerce").mean()) if "opportunity_cost" in part else np.nan
        row["avg_prevented_loss"] = float(pd.to_numeric(part.get("prevented_loss"), errors="coerce").mean()) if "prevented_loss" in part else np.nan
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["false_positive_rate", "days"], ascending=[False, False])


def build_soft_vs_hard_reduce_compare(nav: pd.DataFrame) -> pd.DataFrame:
    frame = _daily_nav_returns(nav)
    frame = frame[
        frame["strategy"].isin(
            [
                GOVERNED,
                GOVERNED_V2,
                GOVERNED_V1_1,
                GOVERNED_V1_1_PATTERN_VETO,
                GOVERNED_V1_2,
                GOVERNED_V1_2_PATTERN_VETO,
                GOVERNED_V1_2B,
                GOVERNED_V1_2B_PATTERN_VETO,
            ]
        )
    ].copy()
    if frame.empty:
        return pd.DataFrame()
    meta_cols = ["strategy", "trade_date", "risk_decision", "target_position_ratio", "risk_governor_reasons"]
    meta = nav[[col for col in meta_cols if col in nav.columns]].copy()
    meta["trade_date"] = pd.to_datetime(meta["trade_date"])
    frame = frame.merge(meta, on=["strategy", "trade_date"], how="left")
    rows: list[dict[str, object]] = []
    for (strategy, decision), part in frame.groupby(["strategy", "risk_decision"], dropna=False):
        curve = (1.0 + part["daily_return"]).cumprod()
        rows.append(
            {
                "strategy": strategy,
                "risk_decision": decision,
                "days": int(len(part)),
                "avg_target_position_ratio": float(pd.to_numeric(part.get("target_position_ratio"), errors="coerce").mean()),
                "avg_daily_return": float(part["daily_return"].mean()),
                "total_return": float(curve.iloc[-1] - 1.0) if not curve.empty else np.nan,
                "max_drawdown": float((curve / curve.cummax() - 1.0).min()) if not curve.empty else np.nan,
            }
        )
    return pd.DataFrame(rows)


def build_governor_version_compare(nav: pd.DataFrame) -> pd.DataFrame:
    strategies = [
        GOVERNED,
        GOVERNED_V1_1,
        GOVERNED_V1_1_PATTERN_VETO,
        GOVERNED_V1_2,
        GOVERNED_V1_2_PATTERN_VETO,
        GOVERNED_V1_2B,
        GOVERNED_V1_2B_PATTERN_VETO,
        GOVERNED_V2,
    ]
    frame = nav[nav["strategy"].isin(strategies)].copy()
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for strategy, part in frame.groupby("strategy"):
        part = part.sort_values("trade_date").copy()
        total_return = float(part["nav"].iloc[-1] / part["nav"].iloc[0] - 1.0)
        days = max(1, len(part))
        annualized = float((1.0 + total_return) ** (252.0 / days) - 1.0) if total_return > -1 else -1.0
        curve = part["nav"] / part["nav"].cummax()
        worst20 = (
            part["nav"].pct_change().fillna(0.0).add(1.0).rolling(20).apply(lambda s: float(np.prod(s) - 1.0), raw=False).min()
        )
        false_positive_count = 0
        if "risk_decision" in part.columns:
            false_positive_count = len(build_false_positive_reduce_days(build_risk_decision_forward_returns(nav, strategy=strategy)))
        rows.append(
            {
                "strategy": strategy,
                "total_return": total_return,
                "annualized_return": annualized,
                "max_drawdown": float((curve - 1.0).min()),
                "avg_gross_exposure": float(pd.to_numeric(part.get("gross_exposure"), errors="coerce").mean()),
                "worst_20d_return": float(worst20) if worst20 == worst20 else np.nan,
                "soft_reduce_days": int(part.get("risk_decision", pd.Series(index=part.index, dtype=object)).astype(str).eq("soft_reduce").sum()),
                "hard_reduce_days": int(part.get("risk_decision", pd.Series(index=part.index, dtype=object)).astype(str).eq("hard_reduce").sum()),
                "recovery_days": int(part.get("risk_decision", pd.Series(index=part.index, dtype=object)).astype(str).eq("recovery_reduce").sum()),
                "sample_count_fail_days": int(
                    part.get("recovery_status", pd.Series(index=part.index, dtype=object)).astype(str).eq("blocked_dynamic_score_sample_count").sum()
                ),
                "pattern_veto_days": int(
                    part.get("recovery_status", pd.Series(index=part.index, dtype=object))
                    .astype(str)
                    .isin(["blocked_pattern_high_risk", "blocked_bearish_dominance"])
                    .sum()
                ),
                "top_industry_veto_days": int(
                    part.get("recovery_status", pd.Series(index=part.index, dtype=object)).astype(str).eq("blocked_top_industry_weight").sum()
                ),
                "reduce_days": int(part.get("risk_decision", pd.Series(index=part.index, dtype=object)).astype(str).isin(REDUCE_DECISIONS).sum()),
                "false_positive_reduce_days": int(false_positive_count),
            }
        )
    return pd.DataFrame(rows)


def build_selected_strategy_contribution(nav: pd.DataFrame) -> pd.DataFrame:
    frame = nav[nav["strategy"].eq(GOVERNED)].copy()
    if frame.empty:
        raise RuntimeError(f"Missing {GOVERNED} nav rows.")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values("trade_date")
    frame["daily_return"] = frame["nav"].pct_change().fillna(0.0)
    group_col = "selected_strategy" if "selected_strategy" in frame.columns else "risk_decision"
    rows = []
    for key, part in frame.groupby(group_col, dropna=False):
        curve = (1.0 + part["daily_return"]).cumprod()
        rows.append(
            {
                group_col: key,
                "days": int(len(part)),
                "avg_position": float(pd.to_numeric(part.get("gross_exposure"), errors="coerce").mean()),
                "total_return": float(curve.iloc[-1] - 1.0) if not curve.empty else np.nan,
                "avg_daily_return": float(part["daily_return"].mean()),
                "max_dd": float((curve / curve.cummax() - 1.0).min()) if not curve.empty else np.nan,
            }
        )
    return pd.DataFrame(rows)


def build_opportunity_cost(nav: pd.DataFrame) -> pd.DataFrame:
    returns = _daily_nav_returns(nav)
    pivot = returns.pivot(index="trade_date", columns="strategy", values="daily_return")
    exposure = returns[returns["strategy"].eq(GOVERNED)].set_index("trade_date")["gross_exposure"]
    meta_cols = ["risk_decision", "target_position_ratio", "risk_governor_reasons"]
    meta = nav[nav["strategy"].eq(GOVERNED)].copy()
    meta["trade_date"] = pd.to_datetime(meta["trade_date"])
    meta = meta.set_index("trade_date")[[col for col in meta_cols if col in meta.columns]]
    if GOVERNED not in pivot or BASELINE not in pivot:
        raise RuntimeError("Need governed and baseline vol_position nav rows for opportunity cost.")
    out = pd.DataFrame(index=pivot.index)
    out["governed_return"] = pivot[GOVERNED]
    out["full_position_return"] = pivot[BASELINE]
    out["opportunity_cost"] = out["full_position_return"] - out["governed_return"]
    out["governor_position"] = exposure
    out = out.join(meta, how="left").reset_index()
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    return out


def build_prevented_loss(opportunity: pd.DataFrame) -> pd.DataFrame:
    out = opportunity.copy()
    out["prevented_loss"] = np.where(out["opportunity_cost"] < 0, -out["opportunity_cost"], 0.0)
    out["avoided_drawdown"] = out["prevented_loss"]
    return out[out["prevented_loss"] > 0].copy()


def build_false_positive_reduce_days(forward: pd.DataFrame) -> pd.DataFrame:
    out = forward[forward["risk_decision"].astype(str).isin(REDUCE_DECISIONS)].copy()
    mask = pd.to_numeric(out["next_10d_return"], errors="coerce").gt(0.03) | pd.to_numeric(out["next_20d_return"], errors="coerce").gt(0.05)
    out = out[mask].copy()
    out["false_positive_reason"] = "reduced_before_positive_forward_return"
    return out


def run_analysis(backtest_dir: Path, output_root: Path) -> dict[str, object]:
    nav = _read(backtest_dir / "trusted_account_backtest_nav.csv")
    if nav.empty:
        raise RuntimeError(f"Missing nav CSV under {backtest_dir}")
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_governor_contribution")
    out_dir.mkdir(parents=True, exist_ok=True)
    forward = build_risk_decision_forward_returns(nav)
    selected = build_selected_strategy_contribution(nav)
    opportunity = build_opportunity_cost(nav)
    prevented = build_prevented_loss(opportunity)
    false_positive = build_false_positive_reduce_days(forward)
    reason_forward = build_risk_reason_forward_returns(forward, opportunity)
    reason_effectiveness = build_risk_reason_effectiveness(reason_forward)
    soft_vs_hard = build_soft_vs_hard_reduce_compare(nav)
    version_compare = build_governor_version_compare(nav)
    files = {
        "risk_decision_forward_returns": out_dir / "risk_decision_forward_returns.csv",
        "risk_reason_forward_returns": out_dir / "risk_reason_forward_returns.csv",
        "risk_reason_effectiveness": out_dir / "risk_reason_effectiveness.csv",
        "soft_vs_hard_reduce_compare": out_dir / "soft_vs_hard_reduce_compare.csv",
        "governor_version_compare": out_dir / "governor_version_compare.csv",
        "selected_strategy_contribution": out_dir / "selected_strategy_contribution.csv",
        "governor_opportunity_cost": out_dir / "governor_opportunity_cost.csv",
        "governor_prevented_loss": out_dir / "governor_prevented_loss.csv",
        "false_positive_reduce_days": out_dir / "false_positive_reduce_days.csv",
        "summary": out_dir / "summary.json",
    }
    forward.to_csv(files["risk_decision_forward_returns"], index=False)
    reason_forward.to_csv(files["risk_reason_forward_returns"], index=False)
    reason_effectiveness.to_csv(files["risk_reason_effectiveness"], index=False)
    soft_vs_hard.to_csv(files["soft_vs_hard_reduce_compare"], index=False)
    version_compare.to_csv(files["governor_version_compare"], index=False)
    selected.to_csv(files["selected_strategy_contribution"], index=False)
    opportunity.to_csv(files["governor_opportunity_cost"], index=False)
    prevented.to_csv(files["governor_prevented_loss"], index=False)
    false_positive.to_csv(files["false_positive_reduce_days"], index=False)
    decision_text = forward["risk_decision"].astype(str)
    summary = {
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "reduce_days": int(decision_text.isin(REDUCE_DECISIONS).sum()),
        "soft_reduce_days": int(decision_text.eq("soft_reduce").sum()),
        "hard_reduce_days": int(decision_text.eq("hard_reduce").sum()),
        "recovery_days": int(decision_text.eq("recovery_reduce").sum()),
        "normal_days": int(decision_text.eq("normal").sum()),
        "false_positive_reduce_days": int(len(false_positive)),
        "prevented_loss_days": int(len(prevented)),
        "files": {key: str(value) for key, value in files.items() if key != "summary"},
    }
    files["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze production risk-governor contribution.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
