from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.config import CONFIG
from scoreRank.core.db_config import build_sqlalchemy_url


OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"

SCORE_COLUMNS = [
    "score",
    "score_liq_breakout_adj",
    "score_liq_breakout_adj_50p_50d",
    "score_liq_breakout_adj_40p_30d",
    "liquidity_detail_score",
    "dynamic_factor_score",
    "dynamic_ic_factor_score",
    "s_liquidity",
    "s_breakout",
    "s_rs",
    "s_relative_amount",
    "s_amount_ratio_5_20",
    "s_low_impact_cost",
    "s_amount_stability",
    "bs_score_v2",
    "bs_consensus_score",
    "bs_model_rank_score",
    "pattern_score",
]

FACTOR_COLUMNS = [
    "score",
    "s_liquidity",
    "liquidity_detail_score",
    "score_liq_breakout_adj",
    "s_rs",
    "s_breakout",
]


@dataclass(frozen=True)
class StrategySpec:
    name: str
    pool: str
    sort_col: str
    liquidity_top_pct: float | None = None
    b_bonus: float = 0.0
    max_per_industry: int | None = None
    industry_penalty_step: float = 0.0
    pit_status: str = "trusted"
    risk_note: str = "uses_signal_date_or_prior_data"
    breakout_liquidity_pct: float | None = None
    breakout_discount: float = 0.5
    market_gate: bool = False
    position_mode: str = "equal"
    candidate_pool: str = "generic"
    allowed_regimes: tuple[str, ...] = ("strong_risk_on", "normal_risk_on", "neutral", "risk_off")
    pool_role: str = "research"


def _symbol_from_ts_code(ts_code: str) -> str:
    return str(ts_code or "").split(".")[0].zfill(6)


def _safe_float(value: object, default: float = np.nan) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _industry_key(value: object, symbol: object) -> str:
    industry = str(value or "").strip()
    if industry and industry.lower() != "nan":
        return industry
    return f"UNKNOWN_{str(symbol or '').zfill(6)}"


def build_strategy_specs() -> list[StrategySpec]:
    specs = [
        StrategySpec("baseline_full_score", "full", "score"),
        StrategySpec("baseline_b_score_v2", "b_candidate", "bs_score_v2"),
        StrategySpec("baseline_b_consensus", "b_candidate", "bs_consensus_score", pit_status="model_risk", risk_note="consensus_uses_latest_model_backfilled_to_history"),
        StrategySpec("baseline_full_liquidity", "full", "s_liquidity"),
        StrategySpec("baseline_full_liq_breakout_adj", "full", "score_liq_breakout_adj"),
        StrategySpec("baseline_full_liq_breakout_adj_50p_50d", "full", "score_liq_breakout_adj_50p_50d"),
        StrategySpec("baseline_full_liq_breakout_adj_40p_30d", "full", "score_liq_breakout_adj_40p_30d"),
        StrategySpec("baseline_full_liquidity_detail", "full", "liquidity_detail_score"),
        StrategySpec("baseline_full_dynamic_factor", "full", "dynamic_factor_score"),
        # v5.2 challenger: value+size+liquidity sleeve (weights from VLS definition)
        StrategySpec("vls_value_size_liquidity_v1", "full", "dynamic_factor_score"),
        # v5.2: VLS with liquidity floor (exclude bottom 40% illiquid) + industry cap
        StrategySpec("vls_liq_floor60", "full", "dynamic_factor_score", liquidity_top_pct=0.60),
        StrategySpec("vls_liq_floor60_incap2", "full", "dynamic_factor_score", liquidity_top_pct=0.60, max_per_industry=2),
        StrategySpec("baseline_full_dynamic_ic_factor", "full", "dynamic_ic_factor_score"),
        StrategySpec("liq_top_10_then_score", "full", "score", liquidity_top_pct=0.10),
        StrategySpec("liq_top_20_then_score", "full", "score", liquidity_top_pct=0.20),
        StrategySpec("liq_top_30_then_score", "full", "score", liquidity_top_pct=0.30),
        StrategySpec("liq_top_10_then_liq_breakout_adj", "full", "score_liq_breakout_adj", liquidity_top_pct=0.10),
        StrategySpec("liq_top_20_then_liq_breakout_adj", "full", "score_liq_breakout_adj", liquidity_top_pct=0.20),
        StrategySpec("liq_top_30_then_liq_breakout_adj", "full", "score_liq_breakout_adj", liquidity_top_pct=0.30),
        StrategySpec("liq_top_10_then_liquidity_detail", "full", "liquidity_detail_score", liquidity_top_pct=0.10),
        StrategySpec("liq_top_20_then_liquidity_detail", "full", "liquidity_detail_score", liquidity_top_pct=0.20),
        StrategySpec("liq_top_30_then_liquidity_detail", "full", "liquidity_detail_score", liquidity_top_pct=0.30),
        StrategySpec("liq_top_10_then_dynamic_factor", "full", "dynamic_factor_score", liquidity_top_pct=0.10),
        StrategySpec("liq_top_20_then_dynamic_factor", "full", "dynamic_factor_score", liquidity_top_pct=0.20),
        StrategySpec("liq_top_30_then_dynamic_factor", "full", "dynamic_factor_score", liquidity_top_pct=0.30),
        StrategySpec("liq_top_20_then_dynamic_ic_factor", "full", "dynamic_ic_factor_score", liquidity_top_pct=0.20),
        StrategySpec("liq_top_20_then_bs_v2", "full", "bs_score_v2", liquidity_top_pct=0.20),
        StrategySpec("liq_top_30_then_bs_v2", "full", "bs_score_v2", liquidity_top_pct=0.30),
        StrategySpec("liq_top_20_then_model_rank", "full", "bs_model_rank_score", liquidity_top_pct=0.20, pit_status="model_risk", risk_note="latest_active_model_used_for_historical_dates"),
        StrategySpec("liq_top_20_then_consensus", "full", "bs_consensus_score", liquidity_top_pct=0.20, pit_status="model_risk", risk_note="consensus_uses_latest_model_backfilled_to_history"),
        StrategySpec("tiered_liquidity_then_score", "liquidity_tiered", "score"),
        StrategySpec("tiered_liquidity_then_liq_breakout_adj", "liquidity_tiered", "score_liq_breakout_adj"),
        StrategySpec("tiered_liquidity_then_liquidity_detail", "liquidity_tiered", "liquidity_detail_score"),
        StrategySpec("tiered_liquidity_then_dynamic_factor", "liquidity_tiered", "dynamic_factor_score"),
        StrategySpec("tiered_liquidity_then_dynamic_ic_factor", "liquidity_tiered", "dynamic_ic_factor_score"),
        StrategySpec(
            "tiered_liquidity_then_bs_v2",
            "liquidity_tiered",
            "bs_score_v2",
            candidate_pool="trend_continuation",
            allowed_regimes=("strong_risk_on",),
            pool_role="attack_challenger",
        ),
        StrategySpec("baseline_full_score_market_gate", "full", "score", market_gate=True),
        StrategySpec("baseline_full_liquidity_detail_market_gate", "full", "liquidity_detail_score", market_gate=True),
        StrategySpec("tiered_liquidity_then_bs_v2_market_gate", "liquidity_tiered", "bs_score_v2", market_gate=True),
        StrategySpec("baseline_full_liquidity_detail_hist_mdd_position", "full", "liquidity_detail_score", position_mode="hist_mdd_20"),
        StrategySpec("baseline_full_score_hist_mdd_position", "full", "score", position_mode="hist_mdd_20"),
        StrategySpec(
            "baseline_full_liquidity_detail_vol_position",
            "full",
            "liquidity_detail_score",
            position_mode="vol_20",
            candidate_pool="liquidity_quality",
            allowed_regimes=("strong_risk_on", "normal_risk_on", "neutral", "risk_off"),
            pool_role="champion_core",
        ),
        StrategySpec("baseline_full_score_expected_mdd_position", "full", "score", position_mode="expected_mdd", pit_status="model_risk", risk_note="uses_latest_model_expected_mdd_backfilled_to_history"),
        StrategySpec("baseline_full_liquidity_industry_cap2", "full", "s_liquidity", max_per_industry=2),
        StrategySpec("baseline_full_liquidity_industry_penalty_0p10pt", "full", "s_liquidity", industry_penalty_step=0.10),
        StrategySpec("baseline_full_liquidity_industry_penalty_0p25pt", "full", "s_liquidity", industry_penalty_step=0.25),
        StrategySpec("baseline_full_liquidity_industry_penalty_0p50pt", "full", "s_liquidity", industry_penalty_step=0.50),
        StrategySpec("baseline_full_liquidity_industry_penalty_1pt", "full", "s_liquidity", industry_penalty_step=1.0),
        StrategySpec("baseline_full_liquidity_industry_penalty_2pt", "full", "s_liquidity", industry_penalty_step=2.0),
        StrategySpec("baseline_full_dynamic_factor_industry_cap2", "full", "dynamic_factor_score", max_per_industry=2),
        StrategySpec("baseline_full_dynamic_factor_industry_cap2_market_gate", "full", "dynamic_factor_score", max_per_industry=2, market_gate=True),
        StrategySpec("liq_top_10_then_score_industry_cap2", "full", "score", liquidity_top_pct=0.10, max_per_industry=2),
        StrategySpec("liq_top_10_then_score_industry_penalty_5pt", "full", "score", liquidity_top_pct=0.10, industry_penalty_step=5.0),
        StrategySpec("liq_top_10_then_score_industry_penalty_10pt", "full", "score", liquidity_top_pct=0.10, industry_penalty_step=10.0),
        StrategySpec("tiered_liquidity_then_score_industry_cap2", "liquidity_tiered", "score", max_per_industry=2),
        StrategySpec("tiered_liquidity_then_score_industry_penalty_5pt", "liquidity_tiered", "score", industry_penalty_step=5.0),
        StrategySpec("tiered_liquidity_then_bs_v2_industry_cap2", "liquidity_tiered", "bs_score_v2", max_per_industry=2),
        StrategySpec("tiered_liquidity_then_bs_v2_industry_cap1", "liquidity_tiered", "bs_score_v2", max_per_industry=1),
        StrategySpec("tiered_liquidity_then_bs_v2_industry_penalty_5pt", "liquidity_tiered", "bs_score_v2", industry_penalty_step=5.0),
    ]
    for sort_col in ("score", "bs_score_v2", "bs_model_rank_score"):
        pit_status = "model_risk" if sort_col == "bs_model_rank_score" else "trusted"
        risk_note = "latest_active_model_used_for_historical_dates" if sort_col == "bs_model_rank_score" else "uses_signal_date_or_prior_data"
        for bonus in (0.00, 0.03, 0.05, 0.08):
            pct = int(round(bonus * 100))
            specs.append(
                StrategySpec(
                    name=f"liq20_{sort_col}_b_bonus_{pct}pct",
                    pool="full",
                    sort_col=sort_col,
                    liquidity_top_pct=0.20,
                    b_bonus=bonus,
                    pit_status=pit_status,
                    risk_note=risk_note,
                )
            )
    for sort_col in ("score", "bs_score_v2"):
        for top_pct in (0.20, 0.30):
            for bonus in (0.03, 0.05):
                specs.append(
                    StrategySpec(
                        name=f"liq{int(top_pct * 100)}_{sort_col}_b_bonus_{int(bonus * 100)}pct_market_gate",
                        pool="full",
                        sort_col=sort_col,
                        liquidity_top_pct=top_pct,
                        b_bonus=bonus,
                        market_gate=True,
                    )
                )
    return specs


def filter_strategy_specs(specs: list[StrategySpec], trusted_only: bool = False) -> list[StrategySpec]:
    if not trusted_only:
        return specs
    return [spec for spec in specs if spec.pit_status == "trusted"]


def load_scores(
    engine,
    start_date: str | None = None,
    end_date: str | None = None,
    min_pool_size: int = 5000,
) -> pd.DataFrame:
    try:
        with engine.connect() as conn:
            score_columns = {str(row["Field"]) for row in conn.execute(text("SHOW COLUMNS FROM score_rank_daily")).mappings()}
    except Exception:
        score_columns = set()

    def score_expr(column: str, default: str = "NULL") -> str:
        if not score_columns or column in score_columns:
            return f"s.{column}"
        return f"{default} AS {column}"

    where = []
    params: dict[str, object] = {}
    if start_date:
        where.append("s.trade_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        where.append("s.trade_date <= :end_date")
        params["end_date"] = end_date
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    sql = f"""
        SELECT
            s.trade_date,
            s.symbol,
            s.name,
            COALESCE(NULLIF(TRIM(s.industry), ''), ds.industry) AS industry,
            s.is_bs_candidate,
            s.score,
            {score_expr("opt_score")},
            {score_expr("claude_score")},
            s.s_breakout,
            s.s_liquidity,
            s.s_rs,
            s.bs_score_v2,
            s.bs_consensus_score,
            {score_expr("bs_model_rank_score")},
            {score_expr("bs_model_prob")},
            {score_expr("bs_model_expected_mdd")},
            {score_expr("pattern_score")},
            {score_expr("pattern_sentiment")},
            {score_expr("pattern_risk_level")},
            {score_expr("pattern_pass_count")},
            {score_expr("bullish_pattern_count")},
            {score_expr("bearish_pattern_count")},
            {score_expr("top_pattern_ids")},
            {score_expr("ashare_signal_keys")},
            s.market_hs300_pct_chg,
            s.market_hs300_ret_20,
            s.market_bs_ratio,
            s.pool_type,
            s.bs_gate_label
        FROM score_rank_daily s
        LEFT JOIN tushare_stock.dim_stock ds
          ON ds.ts_code = CASE
            WHEN s.symbol REGEXP '^[69]' THEN CONCAT(s.symbol, '.SH')
            WHEN s.symbol REGEXP '^[48]' THEN CONCAT(s.symbol, '.BJ')
            ELSE CONCAT(s.symbol, '.SZ')
          END
        {where_sql}
    """
    frame = pd.read_sql(text(sql), engine, params=params)
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    market_cols = ["market_hs300_pct_chg", "market_hs300_ret_20", "market_bs_ratio"]
    for col in SCORE_COLUMNS + ["bs_model_prob", "bs_model_expected_mdd"] + market_cols:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    counts = frame.groupby("trade_date")["symbol"].transform("count")
    frame = frame[counts >= int(min_pool_size)].copy()
    # These repeat across every trading date.  Categoricals materially reduce
    # the resident set for multi-year account-level research without changing
    # their downstream scalar values.
    for column in ("symbol", "name", "industry", "pool_type"):
        if column in frame.columns:
            frame[column] = frame[column].astype("category")
    frame["liquidity_rank_pct"] = frame.groupby("trade_date")["s_liquidity"].rank(pct=True, ascending=False)
    breakout_weight = float(CONFIG.get("weights", {}).get("breakout", 0.22))
    for suffix, liquidity_pct, discount in (
        ("", 0.40, 0.50),
        ("_50p_50d", 0.50, 0.50),
        ("_40p_30d", 0.40, 0.30),
    ):
        liquid_enough = frame["liquidity_rank_pct"] <= liquidity_pct
        breakout_adj = frame["s_breakout"].where(liquid_enough, frame["s_breakout"] * discount)
        frame[f"score_liq_breakout_adj{suffix}"] = (
            frame["score"] - breakout_weight * frame["s_breakout"] + breakout_weight * breakout_adj
        ).clip(0, 100)
    return frame


def load_prices(engine, min_date: object, max_date: object, extra_days: int) -> pd.DataFrame:
    start_key = (pd.Timestamp(min_date) - pd.Timedelta(days=120)).strftime("%Y%m%d")
    end_key = (pd.Timestamp(max_date) + pd.Timedelta(days=max(45, extra_days * 3))).strftime("%Y%m%d")
    table = CONFIG["table"]
    sql = f"""
        SELECT
            p.trade_date, p.ts_code, p.adj_open, p.adj_high, p.adj_low, p.adj_close, p.amount,
            r.open AS raw_open, r.high AS raw_high, r.low AS raw_low, r.close AS raw_close,
            r.pre_close AS raw_pre_close, r.vol AS raw_volume, r.amount AS raw_amount,
            COALESCE(l.is_st, 0) AS is_st, b.circ_mv, ds.list_date, ds.delist_date,
            af.adj_factor, u.is_tradable AS universe_is_tradable, u.is_suspended, u.is_listed,
            CASE WHEN l.ts_code IS NOT NULL AND ds.ts_code IS NOT NULL THEN 1 ELSE 0 END AS security_status_available,
            CASE WHEN r.ts_code IS NOT NULL AND r.vol > 0 AND u.is_tradable = 1 AND u.is_suspended = 0 AND u.is_listed = 1 THEN 1 ELSE 0 END AS execution_tradable
        FROM {table} p
        LEFT JOIN tushare_stock.dwd_daily r
          ON r.ts_code = p.ts_code AND r.trade_date = p.trade_date
        LEFT JOIN tushare_stock.dwd_stock_label_daily l
          ON l.ts_code = p.ts_code AND l.trade_date = p.trade_date
        LEFT JOIN tushare_stock.dwd_daily_basic b
          ON b.ts_code = p.ts_code AND b.trade_date = p.trade_date
        LEFT JOIN tushare_stock.dim_stock ds
          ON ds.ts_code = p.ts_code
        LEFT JOIN tushare_stock.dwd_adj_factor af
          ON af.ts_code = p.ts_code AND af.trade_date = p.trade_date
        LEFT JOIN tushare_stock.ads_universe_daily u
          ON u.ts_code = p.ts_code AND u.trade_date = p.trade_date
        WHERE p.trade_date BETWEEN :start_key AND :end_key
    """
    frame = pd.read_sql(text(sql), engine, params={"start_key": int(start_key), "end_key": int(end_key)})
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d").dt.date
    frame["symbol"] = frame["ts_code"].map(_symbol_from_ts_code)
    for col in ("adj_open", "adj_high", "adj_low", "adj_close", "amount", "raw_open", "raw_high", "raw_low", "raw_close", "raw_pre_close", "raw_volume", "raw_amount", "circ_mv", "adj_factor", "universe_is_tradable", "is_suspended", "is_listed"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.drop(columns=["ts_code"], errors="ignore").dropna(subset=["trade_date", "symbol", "adj_open", "adj_close"])


def add_liquidity_derived_features(scores: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    if scores.empty or prices.empty:
        return scores
    p = prices.sort_values(["symbol", "trade_date"]).copy()
    grouped = p.groupby("symbol", group_keys=False)
    p["amount_ma5"] = grouped["amount"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    p["amount_ma20"] = grouped["amount"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    p["relative_amount"] = p["amount"] / p["amount_ma20"].replace(0, np.nan)
    p["amount_ratio_5_20"] = p["amount_ma5"] / p["amount_ma20"].replace(0, np.nan)
    p["amplitude"] = (p["adj_high"] - p["adj_low"]) / p["adj_close"].replace(0, np.nan)
    p["impact_cost_raw"] = p["amplitude"] / p["amount"].replace(0, np.nan)
    amount_change = grouped["amount"].transform(lambda s: s.pct_change())
    p["amount_stability_raw"] = 1.0 / (amount_change.groupby(p["symbol"]).transform(lambda s: s.rolling(20, min_periods=10).std()) + 1e-9)
    p["ret_1"] = grouped["adj_close"].transform(lambda s: s.pct_change())
    p["vol_20"] = grouped["ret_1"].transform(lambda s: s.rolling(20, min_periods=10).std())
    p["_rolling_peak_20"] = grouped["adj_close"].transform(lambda s: s.rolling(20, min_periods=10).max())
    p["hist_mdd_20"] = (p["adj_close"] / p["_rolling_peak_20"].replace(0, np.nan) - 1.0).clip(upper=0.0)
    feature_cols = [
        "trade_date",
        "symbol",
        "relative_amount",
        "amount_ratio_5_20",
        "impact_cost_raw",
        "amount_stability_raw",
        # Keep the raw one-day return available to downstream risk controls.
        # It is deliberately not imputed: a missing history must remain visible
        # to fail-closed consumers such as the strict precommit cap.
        "ret_1",
        "vol_20",
        "hist_mdd_20",
    ]
    out = scores.merge(p[feature_cols], on=["trade_date", "symbol"], how="left")
    out["s_relative_amount"] = out.groupby("trade_date")["relative_amount"].rank(pct=True, ascending=True) * 100.0
    out["s_amount_ratio_5_20"] = out.groupby("trade_date")["amount_ratio_5_20"].rank(pct=True, ascending=True) * 100.0
    out["s_low_impact_cost"] = (1.0 - out.groupby("trade_date")["impact_cost_raw"].rank(pct=True, ascending=True)) * 100.0
    out["s_amount_stability"] = out.groupby("trade_date")["amount_stability_raw"].rank(pct=True, ascending=True) * 100.0
    normalized_liquidity = (out["s_liquidity"] / 30.0 * 100.0).clip(0, 100)
    out["liquidity_detail_score"] = (
        0.40 * normalized_liquidity
        + 0.20 * out["s_relative_amount"].fillna(50.0)
        + 0.15 * out["s_amount_ratio_5_20"].fillna(50.0)
        + 0.15 * out["s_low_impact_cost"].fillna(50.0)
        + 0.10 * out["s_amount_stability"].fillna(50.0)
    ).clip(0, 100)
    # v5.2: snapshot path must also expose liquidity_rank_pct (used by
    # liquidity_tiered pool selection)
    if "liquidity_rank_pct" not in out.columns:
        out["liquidity_rank_pct"] = out.groupby("trade_date")[
            "s_liquidity"].rank(pct=True, ascending=False)
    return out


def add_forward_returns(scores: pd.DataFrame, prices: pd.DataFrame, hold_days: int) -> pd.DataFrame:
    if scores.empty or prices.empty:
        return scores
    calendar = sorted(prices["trade_date"].dropna().unique().tolist())
    by_date = {}
    for signal_date in sorted(scores["trade_date"].dropna().unique().tolist()):
        entry_date = _next_trade_date(calendar, signal_date)
        exit_date = _exit_trade_date(calendar, entry_date, hold_days) if entry_date is not None else None
        by_date[signal_date] = {"entry_date": entry_date, "exit_date": exit_date}
    out = scores.copy()
    out["entry_date_for_label"] = out["trade_date"].map(lambda d: by_date.get(d, {}).get("entry_date"))
    out["exit_date_for_label"] = out["trade_date"].map(lambda d: by_date.get(d, {}).get("exit_date"))

    entry_prices = prices[["trade_date", "symbol", "adj_open"]].rename(
        columns={"trade_date": "entry_date_for_label", "adj_open": "forward_entry_open"}
    )
    exit_prices = prices[["trade_date", "symbol", "adj_close"]].rename(
        columns={"trade_date": "exit_date_for_label", "adj_close": "forward_exit_close"}
    )
    out = out.merge(entry_prices, on=["entry_date_for_label", "symbol"], how="left")
    out = out.merge(exit_prices, on=["exit_date_for_label", "symbol"], how="left")
    out["forward_ret"] = out["forward_exit_close"] / out["forward_entry_open"].replace(0, np.nan) - 1.0
    return out


def add_dynamic_factor_score(
    scores: pd.DataFrame,
    factor_cols: Iterable[str] = FACTOR_COLUMNS,
    lookback_dates: int = 20,
    top_n: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if scores.empty:
        return scores, pd.DataFrame()
    out = scores.copy()
    usable_factors = [c for c in factor_cols if c in out.columns]
    for col in usable_factors:
        out[f"_pct_{col}"] = out.groupby("trade_date")[col].rank(pct=True, ascending=True) * 100.0
    out["dynamic_factor_score"] = np.nan

    dates = sorted(out["trade_date"].dropna().unique().tolist())
    date_groups = {day: group.copy() for day, group in out.groupby("trade_date", sort=True)}
    index_by_date = {day: group.index for day, group in out.groupby("trade_date", sort=True)}
    exit_by_date = {
        day: group["exit_date_for_label"].dropna().max()
        for day, group in date_groups.items()
        if "exit_date_for_label" in group.columns
    }
    weight_rows: list[dict] = []
    for day in dates:
        history_dates = [d for d in dates if d < day and pd.notna(exit_by_date.get(d)) and exit_by_date[d] < day]
        history_dates = history_dates[-int(lookback_dates):]
        raw_weights: dict[str, float] = {}
        for col in usable_factors:
            if not history_dates or col not in out.columns:
                raw_weights[col] = 0.0
                continue
            daily_rets = []
            for history_day in history_dates:
                group = date_groups.get(history_day, pd.DataFrame()).dropna(subset=[col, "forward_ret"])
                top = group.sort_values(col, ascending=False).head(int(top_n))
                if not top.empty:
                    daily_rets.append(float(top["forward_ret"].mean()))
            raw_weights[col] = max(float(np.nanmean(daily_rets)), 0.0) if daily_rets else 0.0
        total = sum(raw_weights.values())
        if total <= 0:
            weights = {col: 1.0 / max(1, len(usable_factors)) for col in usable_factors}
        else:
            weights = {col: raw_weights[col] / total for col in usable_factors}
        day_index = index_by_date.get(day, pd.Index([]))
        score = pd.Series(0.0, index=day_index)
        for col in usable_factors:
            score = score + out.loc[day_index, f"_pct_{col}"].fillna(50.0) * float(weights[col])
        out.loc[day_index, "dynamic_factor_score"] = score.clip(0, 100)
        row = {
            "trade_date": day,
            "history_dates": len(history_dates),
            "history_start": str(min(history_dates)) if history_dates else None,
            "history_end": str(max(history_dates)) if history_dates else None,
        }
        row.update({f"weight_{col}": float(weights[col]) for col in usable_factors})
        row.update({f"raw_{col}": float(raw_weights[col]) for col in usable_factors})
        weight_rows.append(row)

    drop_cols = [c for c in out.columns if c.startswith("_pct_")]
    out = out.drop(columns=drop_cols)
    return out, pd.DataFrame(weight_rows)


def add_dynamic_ic_factor_score(
    scores: pd.DataFrame,
    factor_cols: Iterable[str] = FACTOR_COLUMNS,
    lookback_dates: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if scores.empty:
        return scores, pd.DataFrame()
    out = scores.copy()
    usable_factors = [c for c in factor_cols if c in out.columns]
    for col in usable_factors:
        out[f"_pct_ic_{col}"] = out.groupby("trade_date")[col].rank(pct=True, ascending=True) * 100.0
    out["dynamic_ic_factor_score"] = np.nan
    dates = sorted(out["trade_date"].dropna().unique().tolist())
    date_groups = {day: group.copy() for day, group in out.groupby("trade_date", sort=True)}
    index_by_date = {day: group.index for day, group in out.groupby("trade_date", sort=True)}
    exit_by_date = {
        day: group["exit_date_for_label"].dropna().max()
        for day, group in date_groups.items()
        if "exit_date_for_label" in group.columns
    }
    weight_rows: list[dict] = []
    for day in dates:
        history_dates = [d for d in dates if d < day and pd.notna(exit_by_date.get(d)) and exit_by_date[d] < day]
        history_dates = history_dates[-int(lookback_dates):]
        raw_weights: dict[str, float] = {}
        for col in usable_factors:
            daily_ic = []
            for history_day in history_dates:
                group = date_groups.get(history_day, pd.DataFrame()).dropna(subset=[col, "forward_ret"])
                if len(group) < 20:
                    continue
                ic = group[col].corr(group["forward_ret"], method="spearman")
                if pd.notna(ic):
                    daily_ic.append(float(ic))
            raw_weights[col] = max(float(np.nanmean(daily_ic)), 0.0) if daily_ic else 0.0
        total = sum(raw_weights.values())
        if total <= 0:
            weights = {col: 1.0 / max(1, len(usable_factors)) for col in usable_factors}
        else:
            weights = {col: raw_weights[col] / total for col in usable_factors}
        day_index = index_by_date.get(day, pd.Index([]))
        score = pd.Series(0.0, index=day_index)
        for col in usable_factors:
            score = score + out.loc[day_index, f"_pct_ic_{col}"].fillna(50.0) * float(weights[col])
        out.loc[day_index, "dynamic_ic_factor_score"] = score.clip(0, 100)
        row = {
            "trade_date": day,
            "method": "spearman_ic",
            "history_dates": len(history_dates),
            "history_start": str(min(history_dates)) if history_dates else None,
            "history_end": str(max(history_dates)) if history_dates else None,
        }
        row.update({f"weight_{col}": float(weights[col]) for col in usable_factors})
        row.update({f"raw_ic_{col}": float(raw_weights[col]) for col in usable_factors})
        weight_rows.append(row)
    drop_cols = [c for c in out.columns if c.startswith("_pct_ic_")]
    out = out.drop(columns=drop_cols)
    return out, pd.DataFrame(weight_rows)


def _select_candidates(day_scores: pd.DataFrame, spec: StrategySpec, top_n: int) -> pd.DataFrame:
    d = day_scores.copy()
    if spec.pool == "b_candidate":
        d = d[pd.to_numeric(d["is_bs_candidate"], errors="coerce").fillna(0).astype(int) == 1]
    elif spec.pool == "liquidity_tiered":
        is_b = pd.to_numeric(d["is_bs_candidate"], errors="coerce").fillna(0).astype(int) == 1
        gate_ok = ~d.get("bs_gate_label", pd.Series("", index=d.index)).fillna("").eq("过滤")
        high_liquidity = d["liquidity_rank_pct"] <= 0.10
        mid_liquidity = (d["liquidity_rank_pct"] > 0.10) & (d["liquidity_rank_pct"] <= 0.40)
        d = d[high_liquidity | (mid_liquidity & is_b & gate_ok)]
    if spec.liquidity_top_pct is not None:
        d = d[d["liquidity_rank_pct"] <= float(spec.liquidity_top_pct)]
    if spec.sort_col not in d.columns:
        return d.iloc[0:0].copy()
    d = d.dropna(subset=[spec.sort_col])
    if d.empty:
        return d
    d["_rank_score"] = pd.to_numeric(d[spec.sort_col], errors="coerce")
    if spec.b_bonus:
        is_b = pd.to_numeric(d["is_bs_candidate"], errors="coerce").fillna(0).astype(int) == 1
        d.loc[is_b, "_rank_score"] = d.loc[is_b, "_rank_score"] * (1.0 + float(spec.b_bonus))
    if spec.market_gate:
        market_ratio = pd.to_numeric(d.get("market_amount_ratio_20", pd.Series(np.nan, index=d.index)), errors="coerce")
        low_market = market_ratio < 0.80
        d = d[~low_market | (d["_rank_score"] >= 70.0)]
        if d.empty:
            return d
    ranked = d.sort_values(["_rank_score", "s_liquidity", "score"], ascending=[False, False, False])
    if spec.max_per_industry is None and not spec.industry_penalty_step:
        return ranked.head(int(top_n)).copy()
    selected_idx = []
    industry_counts: dict[str, int] = {}
    remaining = ranked.copy()
    while not remaining.empty and len(selected_idx) < int(top_n):
        if spec.industry_penalty_step:
            penalties = []
            for _, row in remaining.iterrows():
                industry = _industry_key(row.get("industry"), row.get("symbol"))
                penalties.append(float(spec.industry_penalty_step) * industry_counts.get(industry, 0))
            remaining["_industry_penalty"] = penalties
            remaining["_adjusted_rank_score"] = remaining["_rank_score"] - remaining["_industry_penalty"]
            ordered = remaining.sort_values(["_adjusted_rank_score", "_rank_score", "s_liquidity", "score"], ascending=[False, False, False, False])
        else:
            remaining["_industry_penalty"] = 0.0
            remaining["_adjusted_rank_score"] = remaining["_rank_score"]
            ordered = remaining
        picked_idx = None
        for idx, row in ordered.iterrows():
            industry = _industry_key(row.get("industry"), row.get("symbol"))
            if spec.max_per_industry is not None and industry_counts.get(industry, 0) >= int(spec.max_per_industry):
                continue
            picked_idx = idx
            break
        if picked_idx is None:
            break
        selected_idx.append(picked_idx)
        picked = remaining.loc[picked_idx]
        industry = _industry_key(picked.get("industry"), picked.get("symbol"))
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        remaining = remaining.drop(index=picked_idx)
    return ranked.loc[selected_idx].copy()


def _position_weight(row: pd.Series, spec: StrategySpec, selected_count: int, top_n: int) -> float:
    if selected_count <= 0:
        return 0.0
    if spec.position_mode == "equal":
        return 1.0 / float(selected_count)
    standard = 1.0 / float(max(1, top_n))
    if spec.position_mode == "hist_mdd_20":
        mdd = abs(_safe_float(row.get("hist_mdd_20"), 0.10))
        return min(standard, 0.005 / max(mdd, 0.01))
    if spec.position_mode == "vol_20":
        vol = _safe_float(row.get("vol_20"), 0.03)
        return min(standard, 0.005 / max(vol * np.sqrt(10), 0.01))
    if spec.position_mode == "expected_mdd":
        mdd = abs(_safe_float(row.get("bs_model_expected_mdd"), 0.10))
        return min(standard, 0.005 / max(mdd, 0.01))
    return 1.0 / float(selected_count)


def _market_exposure_scale(row: pd.Series, spec: StrategySpec) -> float:
    if not spec.market_gate:
        return 1.0
    ratio = _safe_float(row.get("market_amount_ratio_20"), np.nan)
    if np.isfinite(ratio) and ratio < 0.80:
        return 0.60
    return 1.0


def _next_trade_date(calendar: list[object], signal_date: object) -> object | None:
    ts = pd.Timestamp(signal_date).date()
    for day in calendar:
        if day > ts:
            return day
    return None


def _exit_trade_date(calendar: list[object], entry_date: object, hold_days: int) -> object | None:
    try:
        idx = calendar.index(entry_date)
    except ValueError:
        return None
    exit_idx = idx + int(hold_days) - 1
    if exit_idx >= len(calendar):
        return None
    return calendar[exit_idx]


def build_trades(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    specs: Iterable[StrategySpec],
    top_n: int,
    hold_days: int,
    rebalance_step: int,
) -> pd.DataFrame:
    if scores.empty or prices.empty:
        return pd.DataFrame()
    price_lookup = prices.set_index(["trade_date", "symbol"])[["adj_open", "adj_close"]].to_dict("index")
    calendar = sorted(prices["trade_date"].dropna().unique().tolist())
    rows: list[dict] = []
    score_groups = list(scores.groupby("trade_date", sort=True))
    step = max(1, int(rebalance_step))
    for idx, (signal_date, day_scores) in enumerate(score_groups):
        if idx % step != 0:
            continue
        entry_date = _next_trade_date(calendar, signal_date)
        if entry_date is None:
            continue
        exit_date = _exit_trade_date(calendar, entry_date, hold_days)
        if exit_date is None:
            continue
        for spec in specs:
            selected = _select_candidates(day_scores, spec, top_n=top_n)
            if selected.empty:
                continue
            selected_count = int(len(selected))
            for _, row in selected.iterrows():
                symbol = str(row["symbol"]).zfill(6)
                entry = price_lookup.get((entry_date, symbol))
                exit_ = price_lookup.get((exit_date, symbol))
                if not entry or not exit_:
                    continue
                entry_open = _safe_float(entry.get("adj_open"))
                exit_close = _safe_float(exit_.get("adj_close"))
                if not np.isfinite(entry_open) or not np.isfinite(exit_close) or entry_open <= 0:
                    continue
                gross_ret = exit_close / entry_open - 1.0
                position_weight = _position_weight(row, spec, selected_count=selected_count, top_n=top_n)
                exposure_scale = _market_exposure_scale(row, spec)
                effective_weight = position_weight * exposure_scale
                rows.append(
                    {
                        "strategy": spec.name,
                        "signal_date": signal_date,
                        "entry_date": entry_date,
                        "exit_date": exit_date,
                        "hold_days": int(hold_days),
                        "rebalance_step": int(rebalance_step),
                        "selected_count": selected_count,
                        "max_per_industry": spec.max_per_industry,
                        "industry_penalty_step": spec.industry_penalty_step,
                        "pit_status": spec.pit_status,
                        "risk_note": spec.risk_note,
                        "market_gate": int(spec.market_gate),
                        "position_mode": spec.position_mode,
                        "symbol": symbol,
                        "name": row.get("name"),
                        "industry": row.get("industry"),
                        "industry_key": _industry_key(row.get("industry"), symbol),
                        "is_bs_candidate": int(_safe_float(row.get("is_bs_candidate"), 0.0)),
                        "sort_col": spec.sort_col,
                        "rank_score": _safe_float(row.get("_rank_score")),
                        "score": _safe_float(row.get("score")),
                        "dynamic_factor_score": _safe_float(row.get("dynamic_factor_score")),
                        "score_liq_breakout_adj": _safe_float(row.get("score_liq_breakout_adj")),
                        "liquidity_detail_score": _safe_float(row.get("liquidity_detail_score")),
                        "s_breakout": _safe_float(row.get("s_breakout")),
                        "s_liquidity": _safe_float(row.get("s_liquidity")),
                        "s_relative_amount": _safe_float(row.get("s_relative_amount")),
                        "s_amount_ratio_5_20": _safe_float(row.get("s_amount_ratio_5_20")),
                        "s_low_impact_cost": _safe_float(row.get("s_low_impact_cost")),
                        "s_amount_stability": _safe_float(row.get("s_amount_stability")),
                        "vol_20": _safe_float(row.get("vol_20")),
                        "hist_mdd_20": _safe_float(row.get("hist_mdd_20")),
                        "bs_score_v2": _safe_float(row.get("bs_score_v2")),
                        "bs_consensus_score": _safe_float(row.get("bs_consensus_score")),
                        "bs_model_rank_score": _safe_float(row.get("bs_model_rank_score")),
                        "bs_model_prob": _safe_float(row.get("bs_model_prob")),
                        "bs_model_expected_mdd": _safe_float(row.get("bs_model_expected_mdd")),
                        "pool_type": row.get("pool_type"),
                        "bs_gate_label": row.get("bs_gate_label"),
                        "market_amount_ratio_20": _safe_float(row.get("market_amount_ratio_20")),
                        "market_exposure_scale": exposure_scale,
                        "position_weight": position_weight,
                        "effective_weight": effective_weight,
                        "entry_open": entry_open,
                        "exit_close": exit_close,
                        "gross_ret": gross_ret,
                    }
                )
    return pd.DataFrame(rows)


def build_portfolio_cycles(trades: pd.DataFrame, cost_rates: Iterable[float]) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    keys = ["strategy", "signal_date", "entry_date", "exit_date"]
    grouped = (
        trades.groupby(keys, as_index=False)
        .agg(
            selected=("symbol", "count"),
            gross_ret=("gross_ret", "mean"),
            weighted_gross_ret=("gross_ret", lambda s: np.nan),
            avg_score=("score", "mean"),
            avg_liquidity=("s_liquidity", "mean"),
            bs_count=("is_bs_candidate", "sum"),
            gross_exposure=("effective_weight", "sum"),
            pit_status=("pit_status", "first"),
            risk_note=("risk_note", "first"),
            market_gate=("market_gate", "first"),
            position_mode=("position_mode", "first"),
        )
        .sort_values(["strategy", "signal_date"])
    )
    weighted = (
        trades.assign(_weighted_ret=trades["gross_ret"] * trades["effective_weight"])
        .groupby(keys, as_index=False)["_weighted_ret"]
        .sum()
        .rename(columns={"_weighted_ret": "weighted_gross_ret_calc"})
    )
    grouped = grouped.drop(columns=["weighted_gross_ret"]).merge(weighted, on=keys, how="left")
    use_weighted = grouped["position_mode"].ne("equal") | grouped["market_gate"].astype(bool)
    grouped["gross_ret"] = grouped["gross_ret"].where(~use_weighted, grouped["weighted_gross_ret_calc"])
    industry = trades.copy()
    if "industry_key" not in industry.columns:
        industry["industry_key"] = [
            _industry_key(ind, sym) for ind, sym in zip(industry.get("industry", ""), industry.get("symbol", ""))
        ]
    max_industry_weight = (
        industry.groupby(keys + ["industry_key"])["symbol"]
        .count()
        .groupby(keys)
        .max()
        .reset_index(name="max_industry_count")
    )
    grouped = grouped.merge(max_industry_weight, on=keys, how="left")
    grouped["max_industry_weight"] = grouped["max_industry_count"] / grouped["selected"].replace(0, np.nan)
    rows: list[dict] = []
    for cost_rate in cost_rates:
        d = grouped.copy()
        d["cost_rate"] = float(cost_rate)
        d["net_ret"] = d["gross_ret"] - float(cost_rate) * d["gross_exposure"].fillna(1.0).clip(lower=0.0)
        rows.append(d)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _max_drawdown(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    nav = (1.0 + returns.fillna(0.0)).cumprod()
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min())


def _annualized_return(total_return: float, periods: int, hold_days: int) -> float | None:
    if periods <= 0:
        return None
    years = periods * int(hold_days) / 252.0
    if years <= 0:
        return None
    return float((1.0 + total_return) ** (1.0 / years) - 1.0)


def split_periods(signal_dates: list[object], train_ratio: float, validation_ratio: float) -> dict[object, str]:
    dates = sorted(signal_dates)
    n = len(dates)
    train_end = int(np.floor(n * float(train_ratio)))
    validation_end = int(np.floor(n * (float(train_ratio) + float(validation_ratio))))
    mapping: dict[object, str] = {}
    for i, day in enumerate(dates):
        if i < train_end:
            mapping[day] = "train"
        elif i < validation_end:
            mapping[day] = "validation"
        else:
            mapping[day] = "test"
    return mapping


def summarize_cycles(cycles: pd.DataFrame, hold_days: int, train_ratio: float, validation_ratio: float) -> pd.DataFrame:
    if cycles.empty:
        return pd.DataFrame()
    split_map = split_periods(cycles["signal_date"].dropna().unique().tolist(), train_ratio, validation_ratio)
    d = cycles.copy()
    d["sample"] = d["signal_date"].map(split_map).fillna("all")
    frames = [d.assign(sample="all"), d]
    all_d = pd.concat(frames, ignore_index=True)
    rows: list[dict] = []
    for (strategy, cost_rate, sample), g in all_d.groupby(["strategy", "cost_rate", "sample"], sort=False):
        g = g.sort_values("signal_date")
        ret = g["net_ret"].dropna()
        gross = g["gross_ret"].dropna()
        if ret.empty:
            continue
        total_return = float((1.0 + ret).prod() - 1.0)
        gross_total_return = float((1.0 + gross).prod() - 1.0) if not gross.empty else np.nan
        rows.append(
            {
                "strategy": strategy,
                "sample": sample,
                "cost_rate": float(cost_rate),
                "periods": int(len(ret)),
                "first_signal_date": str(g["signal_date"].min()),
                "last_signal_date": str(g["signal_date"].max()),
                "avg_selected": float(g["selected"].mean()),
                "avg_bs_count": float(g["bs_count"].mean()),
                "avg_gross_exposure": float(g["gross_exposure"].mean()) if "gross_exposure" in g.columns else 1.0,
                "pit_status": str(g["pit_status"].dropna().iloc[0]) if "pit_status" in g.columns and not g["pit_status"].dropna().empty else "",
                "risk_note": str(g["risk_note"].dropna().iloc[0]) if "risk_note" in g.columns and not g["risk_note"].dropna().empty else "",
                "market_gate": int(g["market_gate"].fillna(0).max()) if "market_gate" in g.columns else 0,
                "position_mode": str(g["position_mode"].dropna().iloc[0]) if "position_mode" in g.columns and not g["position_mode"].dropna().empty else "",
                "gross_total_return": gross_total_return,
                "total_return": total_return,
                "annualized_return": _annualized_return(total_return, len(ret), hold_days),
                "max_drawdown": _max_drawdown(ret),
                "win_rate": float((ret > 0).mean()),
                "avg_return": float(ret.mean()),
                "median_return": float(ret.median()),
                "best_period": float(ret.max()),
                "worst_period": float(ret.min()),
                "avg_score": float(g["avg_score"].mean()),
                "avg_liquidity": float(g["avg_liquidity"].mean()),
                "avg_max_industry_weight": float(g["max_industry_weight"].mean()) if "max_industry_weight" in g.columns else np.nan,
                "turnover_proxy": 1.0,
            }
        )
    return pd.DataFrame(rows).sort_values(["sample", "cost_rate", "total_return"], ascending=[True, True, False])


def build_monthly_returns(cycles: pd.DataFrame) -> pd.DataFrame:
    if cycles.empty:
        return pd.DataFrame()
    d = cycles.copy()
    d["month"] = pd.to_datetime(d["entry_date"]).dt.strftime("%Y-%m")
    return (
        d.groupby(["strategy", "cost_rate", "month"], as_index=False)["net_ret"]
        .apply(lambda s: float((1.0 + s).prod() - 1.0))
        .rename(columns={"net_ret": "monthly_return"})
    )


def build_market_environment(scores: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    if scores.empty:
        return pd.DataFrame()
    base = (
        scores.groupby("trade_date", as_index=False)
        .agg(
            market_avg_liquidity=("s_liquidity", "mean"),
            market_avg_score=("score", "mean"),
            market_bs_ratio=("market_bs_ratio", "median"),
            market_hs300_ret_20=("market_hs300_ret_20", "median"),
            market_hs300_pct_chg=("market_hs300_pct_chg", "median"),
        )
    )
    if not prices.empty and "amount" in prices.columns:
        daily_amount = prices.groupby("trade_date", as_index=False)["amount"].sum().sort_values("trade_date")
        daily_amount["market_amount_ma20"] = daily_amount["amount"].rolling(20, min_periods=10).mean()
        daily_amount["market_amount_ratio_20"] = daily_amount["amount"] / daily_amount["market_amount_ma20"].replace(0, np.nan)
        base = base.merge(daily_amount[["trade_date", "amount", "market_amount_ratio_20"]], on="trade_date", how="left")
    else:
        base["amount"] = np.nan
        base["market_amount_ratio_20"] = np.nan
    base["market_liquidity_bucket"] = np.select(
        [base["market_amount_ratio_20"] < 0.8, base["market_amount_ratio_20"] > 1.2],
        ["low_liquidity", "high_liquidity"],
        default="normal_liquidity",
    )
    base["index_bucket"] = np.select(
        [base["market_hs300_ret_20"] <= -0.04, base["market_hs300_ret_20"] >= 0.04],
        ["index_weak", "index_strong"],
        default="index_neutral",
    )
    return base


def attach_market_environment(scores: pd.DataFrame, market_env: pd.DataFrame) -> pd.DataFrame:
    if scores.empty or market_env.empty:
        out = scores.copy()
        if "market_amount_ratio_20" not in out.columns:
            out["market_amount_ratio_20"] = np.nan
        return out
    keep = [
        c
        for c in ["trade_date", "market_amount_ratio_20", "market_liquidity_bucket", "index_bucket"]
        if c in market_env.columns
    ]
    return scores.merge(market_env[keep], on="trade_date", how="left")


def build_environment_summary(cycles: pd.DataFrame, market_env: pd.DataFrame) -> pd.DataFrame:
    if cycles.empty or market_env.empty:
        return pd.DataFrame()
    d = cycles.merge(market_env, left_on="signal_date", right_on="trade_date", how="left")
    rows: list[dict] = []
    for bucket_col in ("market_liquidity_bucket", "index_bucket"):
        if bucket_col not in d.columns:
            continue
        for (strategy, cost_rate, bucket), g in d.groupby(["strategy", "cost_rate", bucket_col], dropna=False):
            ret = g["net_ret"].dropna()
            if ret.empty:
                continue
            rows.append(
                {
                    "bucket_type": bucket_col,
                    "bucket": bucket,
                    "strategy": strategy,
                    "cost_rate": float(cost_rate),
                    "periods": int(len(ret)),
                    "total_return": float((1.0 + ret).prod() - 1.0),
                    "avg_return": float(ret.mean()),
                    "win_rate": float((ret > 0).mean()),
                    "max_drawdown": _max_drawdown(ret),
                }
            )
    return pd.DataFrame(rows).sort_values(["bucket_type", "bucket", "cost_rate", "total_return"], ascending=[True, True, True, False])


def build_coverage_summary(scores: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    total_rows = int(len(scores))
    total_dates = int(scores["trade_date"].nunique()) if not scores.empty else 0
    for col in SCORE_COLUMNS:
        if col not in scores.columns:
            rows.append(
                {
                    "column": col,
                    "valid_rows": 0,
                    "valid_dates": 0,
                    "row_coverage": 0.0,
                    "date_coverage": 0.0,
                }
            )
            continue
        valid = scores[col].notna()
        rows.append(
            {
                "column": col,
                "valid_rows": int(valid.sum()),
                "valid_dates": int(scores.loc[valid, "trade_date"].nunique()),
                "row_coverage": float(valid.mean()) if total_rows else 0.0,
                "date_coverage": float(scores.loc[valid, "trade_date"].nunique() / total_dates) if total_dates else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _format_pct(x: object) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{float(x) * 100:.2f}%"


def write_outputs(
    out_dir: Path,
    summary: pd.DataFrame,
    cycles: pd.DataFrame,
    trades: pd.DataFrame,
    monthly: pd.DataFrame,
    coverage: pd.DataFrame,
    factor_weights: pd.DataFrame,
    market_env: pd.DataFrame,
    environment_summary: pd.DataFrame,
    params: dict,
    strategy_specs: list[StrategySpec] | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "full_pool_liquidity_strategy_summary.csv"
    cycles_path = out_dir / "full_pool_liquidity_strategy_cycles.csv"
    trades_path = out_dir / "full_pool_liquidity_strategy_trades.csv"
    monthly_path = out_dir / "full_pool_liquidity_strategy_monthly.csv"
    coverage_path = out_dir / "full_pool_liquidity_strategy_coverage.csv"
    factor_weights_path = out_dir / "full_pool_liquidity_strategy_dynamic_weights.csv"
    market_env_path = out_dir / "full_pool_liquidity_strategy_market_environment.csv"
    environment_summary_path = out_dir / "full_pool_liquidity_strategy_environment_summary.csv"
    json_path = out_dir / "full_pool_liquidity_strategy_report.json"
    md_path = out_dir / "full_pool_liquidity_strategy_report.md"

    summary.to_csv(summary_path, index=False)
    cycles.to_csv(cycles_path, index=False)
    trades.to_csv(trades_path, index=False)
    monthly.to_csv(monthly_path, index=False)
    coverage.to_csv(coverage_path, index=False)
    factor_weights.to_csv(factor_weights_path, index=False)
    market_env.to_csv(market_env_path, index=False)
    environment_summary.to_csv(environment_summary_path, index=False)

    evaluated_strategies = set(summary["strategy"].dropna().astype(str).unique()) if not summary.empty else set()
    expected_specs = strategy_specs or build_strategy_specs()
    missing_strategies = [spec.name for spec in expected_specs if spec.name not in evaluated_strategies]
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "coverage": coverage.to_dict("records"),
        "dynamic_weights": factor_weights.to_dict("records"),
        "environment_summary": environment_summary.to_dict("records"),
        "missing_strategies": missing_strategies,
        "summary": summary.to_dict("records"),
        "files": {
            "summary_csv": str(summary_path),
            "cycles_csv": str(cycles_path),
            "trades_csv": str(trades_path),
            "monthly_csv": str(monthly_path),
            "coverage_csv": str(coverage_path),
            "dynamic_weights_csv": str(factor_weights_path),
            "market_environment_csv": str(market_env_path),
            "environment_summary_csv": str(environment_summary_path),
            "markdown": str(md_path),
            "json": str(json_path),
        },
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    base_cost = min(params["cost_rates"])
    all_rows = summary[(summary["sample"] == "all") & (summary["cost_rate"] == base_cost)].copy()
    test_rows = summary[(summary["sample"] == "test") & (summary["cost_rate"] == base_cost)].copy()
    show_cols = [
        "strategy",
        "pit_status",
        "periods",
        "total_return",
        "annualized_return",
        "max_drawdown",
        "win_rate",
        "avg_return",
        "median_return",
        "avg_bs_count",
        "avg_gross_exposure",
        "avg_max_industry_weight",
    ]
    for df in (all_rows, test_rows):
        for col in [
            "total_return",
            "annualized_return",
            "max_drawdown",
            "win_rate",
            "avg_return",
            "median_return",
            "avg_max_industry_weight",
        ]:
            if col in df.columns:
                df[col] = df[col].map(_format_pct)
    lines = [
        "# 全量池流动性策略研究报告",
        "",
        "## 回测口径",
        "",
        f"- 信号来源：`score_rank_daily`，仅使用信号日当日已经存在的评分字段。",
        f"- 买入/卖出：信号日 T 生成，T+1 开盘买入，持有 {params['hold_days']} 个交易日后收盘卖出。",
        f"- 组合：Top {params['top_n']} 等权，`rebalance_step={params['rebalance_step']}`。为 1 时是每日滚动事件研究，为持仓天数时更接近非重叠调仓。",
        f"- 成本：`cost_rate` 作为单次买入到卖出的合计交易成本，从组合收益中扣除。",
        f"- 全量池有效门槛：单日评分股票数不少于 {params['min_pool_size']}。",
        "",
        "## 字段覆盖",
        "",
        coverage.to_markdown(index=False) if not coverage.empty else "_无字段覆盖数据_",
        "",
        "## 未形成有效回测的策略",
        "",
        ", ".join(f"`{name}`" for name in missing_strategies) if missing_strategies else "_全部策略均形成有效回测_",
        "",
        "## 全样本排名",
        "",
        all_rows[show_cols].head(20).to_markdown(index=False) if not all_rows.empty else "_无全样本结果_",
        "",
        "## 测试区间排名",
        "",
        test_rows[show_cols].head(20).to_markdown(index=False) if not test_rows.empty else "_无测试区间结果_",
        "",
        "## 市场环境归因",
        "",
        environment_summary.head(30).to_markdown(index=False) if not environment_summary.empty else "_无市场环境归因数据_",
        "",
        "## 输出文件",
        "",
        f"- Summary CSV: `{summary_path}`",
        f"- Cycles CSV: `{cycles_path}`",
        f"- Trades CSV: `{trades_path}`",
        f"- Monthly CSV: `{monthly_path}`",
        f"- Coverage CSV: `{coverage_path}`",
        f"- Dynamic Weights CSV: `{factor_weights_path}`",
        f"- Market Environment CSV: `{market_env_path}`",
        f"- Environment Summary CSV: `{environment_summary_path}`",
        f"- JSON: `{json_path}`",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report["files"]


def parse_cost_rates(raw: str) -> list[float]:
    rates = []
    for item in str(raw).split(","):
        item = item.strip()
        if not item:
            continue
        rates.append(float(item))
    return rates or [0.0, 0.0015, 0.0020]


def run_research(args: argparse.Namespace) -> dict:
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
    scores = add_liquidity_derived_features(scores, prices)
    scores = add_forward_returns(scores, prices, args.hold_days)
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
        factor_weights["method"] = factor_weights.get("method", "long_topn_return")
    factor_weights = pd.concat([factor_weights, ic_weights], ignore_index=True, sort=False)
    market_env = build_market_environment(scores, prices)
    scores = attach_market_environment(scores, market_env)
    specs = filter_strategy_specs(build_strategy_specs(), trusted_only=args.trusted_only)
    trades = build_trades(
        scores,
        prices,
        specs=specs,
        top_n=args.top_n,
        hold_days=args.hold_days,
        rebalance_step=args.rebalance_step,
    )
    if trades.empty:
        raise RuntimeError("No completed trades generated.")
    cost_rates = parse_cost_rates(args.cost_rates)
    cycles = build_portfolio_cycles(trades, cost_rates=cost_rates)
    summary = summarize_cycles(
        cycles,
        hold_days=args.hold_days,
        train_ratio=args.train_ratio,
        validation_ratio=args.validation_ratio,
    )
    monthly = build_monthly_returns(cycles)
    coverage = build_coverage_summary(scores)
    environment_summary = build_environment_summary(cycles, market_env)
    params = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "top_n": args.top_n,
        "hold_days": args.hold_days,
        "rebalance_step": args.rebalance_step,
        "min_pool_size": args.min_pool_size,
        "cost_rates": cost_rates,
        "train_ratio": args.train_ratio,
        "validation_ratio": args.validation_ratio,
        "dynamic_lookback_dates": args.dynamic_lookback_dates,
        "trusted_only": bool(args.trusted_only),
        "score_dates": int(scores["trade_date"].nunique()),
        "score_rows": int(len(scores)),
        "trade_rows": int(len(trades)),
        "cycle_rows": int(len(cycles)),
    }
    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_full_pool_liquidity")
    files = write_outputs(
        out_dir,
        summary,
        cycles,
        trades,
        monthly,
        coverage,
        factor_weights,
        market_env,
        environment_summary,
        params,
        strategy_specs=specs,
    )
    return {"params": params, "files": files, "top_summary": summary.head(20).to_dict("records")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Research full-pool liquidity prefilter and B-bonus strategies.")
    parser.add_argument("--start-date", default=None, help="Inclusive score date, YYYY-MM-DD.")
    parser.add_argument("--end-date", default=None, help="Inclusive score date, YYYY-MM-DD.")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument(
        "--rebalance-step",
        type=int,
        default=1,
        help="Use every Nth score date. 1 means rolling daily event study; set to hold-days for non-overlapping cycles.",
    )
    parser.add_argument("--min-pool-size", type=int, default=5000)
    parser.add_argument(
        "--cost-rates",
        default="0,0.0015,0.0020",
        help="Comma-separated round-trip cost rates. 0.0015 means 0.15%% total cost per cycle.",
    )
    parser.add_argument("--train-ratio", type=float, default=0.60)
    parser.add_argument("--validation-ratio", type=float, default=0.20)
    parser.add_argument("--dynamic-lookback-dates", type=int, default=20)
    parser.add_argument("--trusted-only", action="store_true", help="Exclude strategies with known model-version future-information risk.")
    args = parser.parse_args()
    report = run_research(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
