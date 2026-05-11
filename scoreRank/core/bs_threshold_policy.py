from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any

import pandas as pd


POLICY_VERSION = "dynamic_v1"
SHADOW_VERSION = "consensus_shadow_v1"


@dataclass(frozen=True)
class ThresholdDecision:
    trade_threshold: float
    watch_threshold: float
    version: str
    reason: str


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def resolve_bs_thresholds(market_context: Mapping[str, Any] | None, config: Mapping[str, Any]) -> ThresholdDecision:
    base_trade = float(config.get("bs_v2_trade_threshold", config.get("bs_trade_threshold", config.get("trade_threshold", 75))))
    base_watch = float(config.get("bs_v2_watch_threshold", config.get("bs_watch_threshold", config.get("watch_threshold", 60))))
    if not bool(config.get("bs_dynamic_threshold_enabled", True)):
        return ThresholdDecision(base_trade, base_watch, "static_v1", "static_config")

    ctx = dict(market_context or {})
    regime = str(ctx.get("market_regime") or "neutral")
    hs300_ret20 = _safe_float(ctx.get("market_hs300_ret_20"), 0.0)
    hs300_pct = _safe_float(ctx.get("market_hs300_pct_chg"), 0.0)
    bs_ratio = _safe_float(ctx.get("market_bs_ratio"), 0.0)
    limit_up_rate = _safe_float(ctx.get("market_limit_up_rate"), 0.0)

    trade_adj = 0.0
    watch_adj = 0.0
    reasons: list[str] = []

    if regime == "risk_off" or hs300_ret20 <= -0.04:
        trade_adj += 4.0
        watch_adj += 3.0
        reasons.append("risk_off_tighten")
    elif regime == "risk_on" and hs300_ret20 >= 0.03 and bs_ratio <= 0.08:
        trade_adj -= 2.0
        watch_adj -= 1.0
        reasons.append("risk_on_ease")

    if hs300_pct <= -2.0:
        trade_adj += 2.0
        watch_adj += 1.0
        reasons.append("index_drop_tighten")
    if bs_ratio >= 0.16:
        trade_adj += 2.0
        watch_adj += 1.0
        reasons.append("signal_crowding_tighten")
    if limit_up_rate >= 0.08:
        trade_adj += 1.0
        reasons.append("limit_up_heat_tighten")

    min_trade = float(config.get("bs_dynamic_trade_min", base_trade - 5.0))
    max_trade = float(config.get("bs_dynamic_trade_max", base_trade + 8.0))
    min_watch = float(config.get("bs_dynamic_watch_min", base_watch - 4.0))
    max_watch = float(config.get("bs_dynamic_watch_max", base_watch + 6.0))

    trade = min(max(base_trade + trade_adj, min_trade), max_trade)
    watch = min(max(base_watch + watch_adj, min_watch), max_watch)
    if watch >= trade:
        watch = max(min_watch, trade - 6.0)
    return ThresholdDecision(
        trade_threshold=round(float(trade), 2),
        watch_threshold=round(float(watch), 2),
        version=POLICY_VERSION,
        reason=";".join(reasons) if reasons else "neutral_no_adjustment",
    )


def attach_threshold_columns(df: pd.DataFrame, decision: ThresholdDecision) -> pd.DataFrame:
    out = df.copy()
    out["dynamic_trade_threshold"] = decision.trade_threshold
    out["dynamic_watch_threshold"] = decision.watch_threshold
    out["bs_threshold_version"] = decision.version
    out["bs_threshold_reason"] = decision.reason
    return out


def assign_shadow_pool(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        out["pool_type_shadow"] = []
        out["pool_type_shadow_reason"] = []
        return out

    consensus_trade = float(config.get("bs_consensus_trade_threshold", 66.0))
    consensus_watch = float(config.get("bs_consensus_watch_threshold", 56.0))
    model_trade = float(config.get("bs_model_rank_trade_threshold", 62.0))
    model_watch = float(config.get("bs_model_rank_watch_threshold", 52.0))

    is_bs = pd.to_numeric(out.get("is_bs_candidate", 0), errors="coerce").fillna(0).astype(int) == 1
    gate_label = out.get("bs_gate_label", pd.Series("", index=out.index)).fillna("").astype(str)
    gate_ok = gate_label.ne("过滤")
    consensus = pd.to_numeric(out.get("bs_consensus_score"), errors="coerce").fillna(0.0)
    model_rank = pd.to_numeric(out.get("bs_model_rank_score"), errors="coerce").fillna(0.0)

    trade = is_bs & gate_ok & (consensus >= consensus_trade) & (model_rank >= model_trade)
    watch = is_bs & gate_ok & ~trade & ((consensus >= consensus_watch) | (model_rank >= model_watch))

    out["pool_type_shadow"] = None
    out.loc[trade, "pool_type_shadow"] = "TRADE"
    out.loc[watch, "pool_type_shadow"] = "WATCH"
    out["pool_type_shadow_reason"] = None
    out.loc[trade, "pool_type_shadow_reason"] = SHADOW_VERSION + ": consensus_model_trade"
    out.loc[watch, "pool_type_shadow_reason"] = SHADOW_VERSION + ": consensus_or_model_watch"
    return out
