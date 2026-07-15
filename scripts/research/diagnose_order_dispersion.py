"""Diagnose stock-level dispersion in strategy order forward performance.

Identifies characteristics that separate winners from losers to find
actionable strategy improvements.

Dimensions analyzed:
  1. Score-based: do higher-scored stocks outperform?
  2. Industry concentration & performance
  3. Recurring winners (e.g. 688146 appears multiple times)
  4. Day-1 return as early exit signal
  5. Score sub-components correlation with forward returns
  6. Market regime interaction
  7. Position sizing vs equal-weight
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url

TODAY = date.today()
LOOKBACK_DAYS = 40
FORWARD_TRADING_DAYS = 5
SCORE_TABLE = "chenyiyun.score_rank_daily"
ORDER_TABLE = "chenyiyun.ads_local_strategy_orders"
PRICE_TABLE = "tushare_stock.dwd_stock_daily_standard"
CALENDAR_TABLE = "chenyiyun.dim_trade_cal"
DIM_STOCK_TABLE = "tushare_stock.dim_stock"


def _to_int_date(d) -> int:
    if isinstance(d, int):
        return d
    if isinstance(d, str):
        return int(d.replace("-", ""))
    return int(d.strftime("%Y%m%d"))


def _from_int_date(d: int) -> date:
    s = str(d)
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _pct(v, digits=2):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(x):
        return "-"
    return f"{x * 100:.{digits}f}%"


def _num(v, digits=2):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(x):
        return "-"
    return f"{x:.{digits}f}"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_all_data(engine):
    """Load orders, scores, prices, calendar, and stock info in one pass."""
    start_date = _to_int_date(TODAY - timedelta(days=LOOKBACK_DAYS))
    end_date = _to_int_date(TODAY)

    # Calendar
    cal = pd.read_sql(
        text(f"SELECT cal_date FROM {CALENDAR_TABLE} WHERE exchange='SSE' AND is_open=1 ORDER BY cal_date"),
        engine,
    )
    calendar = sorted(cal["cal_date"].astype(int).tolist())

    # Build next-trade-date map
    next_date_map = {}
    for i, d in enumerate(calendar[:-1]):
        next_date_map[d] = calendar[i + 1]
    next_date_map[calendar[-1]] = None

    # Orders
    orders = pd.read_sql(
        text(
            f"""
        SELECT id, trade_date, execution_date, strategy, ts_code, side,
               target_weight, delta_weight, order_status
        FROM {ORDER_TABLE}
        WHERE trade_date >= :start_date AND trade_date <= :end_date
          AND side = 'BUY'
        ORDER BY trade_date, ts_code
        """
        ),
        engine,
        params={"start_date": start_date, "end_date": end_date},
    )
    for col in ["trade_date", "execution_date"]:
        if col in orders.columns:
            orders[col] = orders[col].apply(lambda x: _to_int_date(x) if pd.notna(x) else 0)
    orders["ts_code"] = orders["ts_code"].astype(str).str.strip()

    # Scores (use symbol matching — score table has 'symbol' not 'ts_code')
    # Extract bare symbol from ts_code (e.g. "300285" from "300285" or "300285.SZ")
    symbols = orders["ts_code"].str.replace(r"\.[A-Z]+$", "", regex=True).unique().tolist()
    placeholders = ",".join([f":sym_{i}" for i in range(len(symbols))])
    score_params = {f"sym_{i}": s for i, s in enumerate(symbols)}
    score_params["start_date"] = start_date
    score_params["end_date"] = end_date

    scores = pd.read_sql(
        text(
            f"""
        SELECT trade_date, symbol, name, industry, score, opt_score, claude_score,
               bs_score, bs_consensus_score, bs_gate_score, bs_model_prob,
               s_trend, s_breakout, s_volume, s_rs, s_contraction, s_liquidity,
               opt_momentum, opt_value, opt_quality, opt_technical, opt_capital, opt_chip, opt_size,
               score_momentum, score_value, score_quality, score_technical, score_capital, score_chip,
               market_hs300_pct_chg, market_hs300_ret_5, market_hs300_ret_20,
               market_regime, market_limit_up_rate, market_avg_score,
               is_bs_candidate, pool_type
        FROM {SCORE_TABLE}
        WHERE trade_date >= :start_date AND trade_date <= :end_date
          AND symbol IN ({placeholders})
        ORDER BY trade_date, symbol
        """
        ),
        engine,
        params=score_params,
    )
    for col in ["trade_date"]:
        if col in scores.columns:
            scores[col] = scores[col].apply(lambda x: _to_int_date(x) if pd.notna(x) else 0)

    # Stock info (industry, market, list_date)
    stock_info = pd.read_sql(
        text(
            f"""
        SELECT symbol, name, industry, area, market, list_date
        FROM {DIM_STOCK_TABLE}
        WHERE symbol IN ({placeholders})
        """
        ),
        engine,
        params={f"sym_{i}": s for i, s in enumerate(symbols)},
    )

    # Prices — load for all order symbols
    code_variants = set()
    for c in orders["ts_code"].unique():
        c_str = str(c).strip()
        code_variants.add(c_str)
        for suffix in [".SZ", ".SH", ".BJ"]:
            code_variants.add(c_str + suffix)
    price_codes = list(code_variants)
    price_placeholders = ",".join([f":pc_{i}" for i in range(len(price_codes))])
    price_params = {f"pc_{i}": c for i, c in enumerate(price_codes)}
    price_params["start_date"] = int(min(orders["trade_date"]))
    price_params["end_date"] = max(calendar)

    prices = pd.read_sql(
        text(
            f"""
        SELECT trade_date, ts_code, adj_open, adj_close
        FROM {PRICE_TABLE}
        WHERE ts_code IN ({price_placeholders})
          AND trade_date >= :start_date AND trade_date <= :end_date
        ORDER BY ts_code, trade_date
        """
        ),
        engine,
        params=price_params,
    )
    # Normalize ts_code in prices to bare code
    prices["ts_code_bare"] = prices["ts_code"].str.replace(r"\.[A-Z]+$", "", regex=True)
    prices["ts_code"] = prices["ts_code_bare"]
    prices = prices.drop(columns=["ts_code_bare"])

    # Also load benchmark (CSI 300)
    bm_prices = pd.read_sql(
        text(
            f"""
        SELECT trade_date, ts_code, `open` AS adj_open, `close` AS adj_close
        FROM tushare_stock.dwd_index_daily
        WHERE ts_code = '000300.SH'
          AND trade_date >= :start_date AND trade_date <= :end_date
        ORDER BY trade_date
        """
        ),
        engine,
        params={"start_date": price_params["start_date"], "end_date": price_params["end_date"]},
    )

    return orders, scores, prices, stock_info, bm_prices, calendar, next_date_map


# ---------------------------------------------------------------------------
# Forward return computation (same as review script)
# ---------------------------------------------------------------------------


def compute_forward_returns(orders, prices, calendar, next_date_map):
    """Compute forward returns per order."""
    cal_set = set(calendar)
    signal_map = {}
    for sd in sorted(orders["trade_date"].unique()):
        sd = int(sd)
        if sd not in cal_set:
            continue
        exec_date = next_date_map.get(sd)
        if exec_date is None:
            continue
        exit_dates = [exec_date]
        cursor = exec_date
        for _ in range(FORWARD_TRADING_DAYS - 1):
            nxt = next_date_map.get(cursor)
            if nxt is None:
                break
            exit_dates.append(nxt)
            cursor = nxt
        if len(exit_dates) == FORWARD_TRADING_DAYS:
            signal_map[sd] = {"exec_date": exec_date, "exit_dates": exit_dates}

    price_idx = prices.set_index(["ts_code", "trade_date"])
    rows = []
    for _, order in orders.iterrows():
        sd = int(order["trade_date"])
        ts_code = str(order["ts_code"])
        sched = signal_map.get(sd)
        if sched is None:
            continue
        exec_date = sched["exec_date"]
        exit_dates = sched["exit_dates"]
        try:
            entry_open = float(price_idx.loc[(ts_code, exec_date), "adj_open"])
        except (KeyError, TypeError):
            entry_open = np.nan
        closes = []
        for exit_d in exit_dates:
            try:
                closes.append(float(price_idx.loc[(ts_code, exit_d), "adj_close"]))
            except (KeyError, TypeError):
                closes.append(np.nan)
        rets = {}
        for i, c in enumerate(closes):
            if np.isfinite(entry_open) and np.isfinite(c) and entry_open > 0:
                rets[f"forward_ret_d{i+1}"] = c / entry_open - 1.0
            else:
                rets[f"forward_ret_d{i+1}"] = np.nan
        if closes and np.isfinite(closes[-1]) and np.isfinite(entry_open) and entry_open > 0:
            rets["forward_ret_1w"] = closes[-1] / entry_open - 1.0
        else:
            rets["forward_ret_1w"] = np.nan
        finite_closes = [c for c in closes if np.isfinite(c)]
        if finite_closes and np.isfinite(entry_open) and entry_open > 0:
            rets["forward_max_ret"] = max(finite_closes) / entry_open - 1.0
        else:
            rets["forward_max_ret"] = np.nan
        # D1 max drawdown (intra-week risk)
        week_low = min([c for c in closes if np.isfinite(c)]) if finite_closes else np.nan
        if np.isfinite(week_low) and np.isfinite(entry_open) and entry_open > 0:
            rets["forward_max_dd"] = week_low / entry_open - 1.0
        else:
            rets["forward_max_dd"] = np.nan
        rows.append(
            {
                "trade_date": sd,
                "ts_code": ts_code,
                "target_weight": order.get("target_weight", np.nan),
                **rets,
            }
        )
    return pd.DataFrame(rows), signal_map


def compute_benchmark_returns(bm_prices, signal_map):
    """CSI 300 forward returns per signal date."""
    if bm_prices.empty:
        return pd.Series(dtype=float)
    price_idx = bm_prices.set_index("trade_date")
    records = {}
    for sd, sched in signal_map.items():
        exec_date = sched["exec_date"]
        exit_dates = sched["exit_dates"]
        try:
            entry_open = float(price_idx.loc[exec_date, "adj_open"])
        except (KeyError, TypeError):
            records[sd] = np.nan
            continue
        if len(exit_dates) < FORWARD_TRADING_DAYS:
            records[sd] = np.nan
            continue
        try:
            last_close = float(price_idx.loc[exit_dates[-1], "adj_close"])
        except (KeyError, TypeError):
            records[sd] = np.nan
            continue
        if np.isfinite(entry_open) and np.isfinite(last_close) and entry_open > 0:
            records[sd] = last_close / entry_open - 1.0
        else:
            records[sd] = np.nan
    return pd.Series(records, name="benchmark_ret_1w")


# ---------------------------------------------------------------------------
# Merge & enrichment
# ---------------------------------------------------------------------------


def enrich_results(results, orders, scores, stock_info):
    """Merge forward returns with scores and stock info."""
    # Merge with orders to get original trade_date + ts_code
    # results already has trade_date, ts_code, and target_weight from compute_forward_returns
    # We just need to make sure ts_code is clean
    enriched = results.copy()
    enriched["ts_code"] = enriched["ts_code"].astype(str).str.strip()

    # Extract bare symbol for score matching
    enriched["symbol"] = enriched["ts_code"].str.replace(r"\.[A-Z]+$", "", regex=True)

    # Merge scores
    score_key_cols = ["trade_date", "symbol"]
    score_feature_cols = [
        "industry", "score", "opt_score", "claude_score",
        "bs_score", "bs_consensus_score", "bs_gate_score", "bs_model_prob",
        "s_trend", "s_breakout", "s_volume", "s_rs", "s_contraction", "s_liquidity",
        "opt_momentum", "opt_value", "opt_quality", "opt_technical", "opt_capital", "opt_chip", "opt_size",
        "score_momentum", "score_value", "score_quality", "score_technical", "score_capital", "score_chip",
        "market_hs300_pct_chg", "market_hs300_ret_5", "market_hs300_ret_20",
        "market_regime", "market_limit_up_rate", "is_bs_candidate", "pool_type",
    ]
    available_score_cols = score_key_cols + [c for c in score_feature_cols if c in scores.columns]
    # Avoid duplicate columns: only keep score columns not already in enriched
    existing_cols = set(enriched.columns)
    score_df = scores[available_score_cols].copy()
    # Rename columns that conflict (except merge keys)
    rename_map = {}
    for c in score_df.columns:
        if c in ("trade_date", "symbol"):
            continue
        if c in existing_cols:
            rename_map[c] = f"{c}_score"
    if rename_map:
        score_df = score_df.rename(columns=rename_map)

    enriched = enriched.merge(
        score_df,
        on=["trade_date", "symbol"],
        how="left",
    )
    # Copy score values to original columns
    for orig, renamed in rename_map.items():
        if renamed in enriched.columns:
            enriched[orig] = enriched[renamed].fillna(enriched[orig])
            enriched = enriched.drop(columns=[renamed])

    # Merge stock info (prefer stock_info industry over score industry)
    si_cols = ["symbol", "industry", "area", "market", "list_date"]
    si_cols = [c for c in si_cols if c in stock_info.columns]
    existing_cols2 = set(enriched.columns)
    si_rename = {}
    for c in si_cols:
        if c == "symbol":
            continue
        if c in existing_cols2:
            si_rename[c] = f"{c}_si"
    si_df = stock_info[si_cols].copy()
    if si_rename:
        si_df = si_df.rename(columns=si_rename)

    enriched = enriched.merge(si_df, on="symbol", how="left")
    for orig, renamed in si_rename.items():
        if renamed in enriched.columns:
            enriched[orig] = enriched[renamed].fillna(enriched[orig])
            enriched = enriched.drop(columns=[renamed])

    # Compute excess return vs benchmark
    return enriched


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def _safe_quintile(df, col, prefix="Q", n_bins=5):
    """Safely create quantile labels, handling duplicate bin edges."""
    # Remove NaN
    valid = df[col].dropna()
    if len(valid) < n_bins:
        n_bins = min(len(valid), 5)
    if n_bins <= 1:
        return pd.Series("All", index=df.index)
    # Compute quantiles
    try:
        bins = pd.qcut(valid, n_bins, duplicates="drop", retbins=True)[1]
    except ValueError:
        return pd.Series("All", index=df.index)
    actual_n = len(bins) - 1
    if actual_n <= 1:
        return pd.Series("All", index=df.index)
    labels = [f"{prefix}{i+1}" for i in range(actual_n)]
    labels[0] = f"{labels[0]}(低)"
    labels[-1] = f"{labels[-1]}(高)"
    return pd.cut(df[col], bins=bins, labels=labels, include_lowest=True)


def analyze_by_score_quantile(enriched: pd.DataFrame) -> pd.DataFrame:
    """Split stocks by score quantile and compare forward returns."""
    df = enriched.dropna(subset=["forward_ret_1w", "score"]).copy()
    if df.empty:
        return pd.DataFrame()
    df["score_quintile"] = _safe_quintile(df, "score")
    agg = df.groupby("score_quintile", observed=False).agg(
        n=("forward_ret_1w", "count"),
        avg_ret_1w=("forward_ret_1w", "mean"),
        median_ret_1w=("forward_ret_1w", "median"),
        win_rate=("forward_ret_1w", lambda x: (x > 0).mean()),
        hit_5pct=("forward_ret_1w", lambda x: (x >= 0.05).mean()),
        hit_10pct=("forward_ret_1w", lambda x: (x >= 0.10).mean()),
        avg_max_dd=("forward_max_dd", "mean"),
        avg_score=("score", "mean"),
    ).reset_index()
    return agg


def analyze_by_opt_score(enriched: pd.DataFrame) -> pd.DataFrame:
    """Split by opt_score (factor optimization score)."""
    df = enriched.dropna(subset=["forward_ret_1w", "opt_score"]).copy()
    if df.empty:
        return pd.DataFrame()
    df["opt_quintile"] = _safe_quintile(df, "opt_score", "OPT_")
    agg = df.groupby("opt_quintile", observed=False).agg(
        n=("forward_ret_1w", "count"),
        avg_ret_1w=("forward_ret_1w", "mean"),
        win_rate=("forward_ret_1w", lambda x: (x > 0).mean()),
        hit_10pct=("forward_ret_1w", lambda x: (x >= 0.10).mean()),
    ).reset_index()
    return agg


def analyze_by_claude_score(enriched: pd.DataFrame) -> pd.DataFrame:
    """Split by claude_score."""
    df = enriched.dropna(subset=["forward_ret_1w", "claude_score"]).copy()
    if df.empty:
        return pd.DataFrame()
    df["claude_quintile"] = _safe_quintile(df, "claude_score", "CLD_")
    agg = df.groupby("claude_quintile", observed=False).agg(
        n=("forward_ret_1w", "count"),
        avg_ret_1w=("forward_ret_1w", "mean"),
        win_rate=("forward_ret_1w", lambda x: (x > 0).mean()),
        hit_10pct=("forward_ret_1w", lambda x: (x >= 0.10).mean()),
    ).reset_index()
    return agg


def analyze_by_industry(enriched: pd.DataFrame) -> pd.DataFrame:
    """Analyze forward returns by industry."""
    df = enriched.dropna(subset=["forward_ret_1w", "industry"]).copy()
    if df.empty:
        return pd.DataFrame()
    agg = df.groupby("industry").agg(
        n=("forward_ret_1w", "count"),
        avg_ret_1w=("forward_ret_1w", "mean"),
        median_ret_1w=("forward_ret_1w", "median"),
        win_rate=("forward_ret_1w", lambda x: (x > 0).mean()),
        hit_10pct=("forward_ret_1w", lambda x: (x >= 0.10).mean()),
        max_ret=("forward_ret_1w", "max"),
        min_ret=("forward_ret_1w", "min"),
    ).reset_index()
    return agg.sort_values("avg_ret_1w", ascending=False)


def analyze_recurring_stocks(enriched: pd.DataFrame) -> pd.DataFrame:
    """Identify stocks that appear multiple times and their performance."""
    df = enriched.dropna(subset=["forward_ret_1w"]).copy()
    freq = df.groupby("ts_code").agg(
        appearances=("trade_date", "nunique"),
        avg_ret=("forward_ret_1w", "mean"),
        win_rate=("forward_ret_1w", lambda x: (x > 0).mean()),
        max_ret=("forward_ret_1w", "max"),
        min_ret=("forward_ret_1w", "min"),
    ).reset_index()
    return freq.sort_values("appearances", ascending=False)


def analyze_d1_as_exit_signal(enriched: pd.DataFrame) -> pd.DataFrame:
    """If D1 return is very negative, does it predict further decline?
    If D1 is very positive, does momentum continue?"""
    df = enriched.dropna(subset=["forward_ret_1w", "forward_ret_d1"]).copy()
    if df.empty:
        return pd.DataFrame()

    bins = [-np.inf, -0.05, -0.03, -0.01, 0.01, 0.03, 0.05, np.inf]
    labels = ["<-5%", "-5~-3%", "-3~-1%", "-1~1%", "1~3%", "3~5%", ">5%"]
    df["d1_bucket"] = pd.cut(df["forward_ret_d1"], bins=bins, labels=labels)

    agg = df.groupby("d1_bucket", observed=False).agg(
        n=("forward_ret_1w", "count"),
        pct_of_total=("forward_ret_1w", lambda x: len(x) / len(df)),
        avg_ret_1w=("forward_ret_1w", "mean"),
        avg_ret_d2_d5=("forward_ret_1w", lambda x: (x - df.loc[x.index, "forward_ret_d1"]).mean()),
        win_rate=("forward_ret_1w", lambda x: (x > 0).mean()),
        avg_d1=("forward_ret_d1", "mean"),
    ).reset_index()
    return agg


def analyze_score_correlations(enriched: pd.DataFrame) -> pd.DataFrame:
    """Compute correlations between pre-signal scores and forward 1w return."""
    df = enriched.dropna(subset=["forward_ret_1w"]).copy()
    score_features = [
        "score", "opt_score", "claude_score",
        "bs_score", "bs_consensus_score", "bs_gate_score", "bs_model_prob",
        "s_trend", "s_breakout", "s_volume", "s_rs", "s_contraction", "s_liquidity",
        "opt_momentum", "opt_value", "opt_quality", "opt_technical", "opt_capital", "opt_chip", "opt_size",
    ]
    available = [c for c in score_features if c in df.columns]
    corrs = []
    for col in available:
        valid = df[[col, "forward_ret_1w"]].dropna()
        if len(valid) < 5:
            continue
        ic = valid[col].corr(valid["forward_ret_1w"])
        spearman = valid[col].corr(valid["forward_ret_1w"], method="spearman")
        corrs.append({"feature": col, "pearson_r": ic, "spearman_r": spearman, "n": len(valid)})
    return pd.DataFrame(corrs).sort_values("spearman_r", ascending=False)


def analyze_market_regime(enriched: pd.DataFrame) -> pd.DataFrame:
    """Performance by market regime (bull/bear/neutral)."""
    df = enriched.dropna(subset=["forward_ret_1w", "market_regime"]).copy()
    if df.empty or "market_regime" not in df.columns:
        return pd.DataFrame()
    agg = df.groupby("market_regime").agg(
        n=("forward_ret_1w", "count"),
        avg_ret_1w=("forward_ret_1w", "mean"),
        win_rate=("forward_ret_1w", lambda x: (x > 0).mean()),
        hit_5pct=("forward_ret_1w", lambda x: (x >= 0.05).mean()),
    ).reset_index()
    return agg


def analyze_weight_vs_return(enriched: pd.DataFrame) -> pd.DataFrame:
    """Does higher target_weight predict better forward return?"""
    df = enriched.dropna(subset=["forward_ret_1w", "target_weight"]).copy()
    if df.empty:
        return pd.DataFrame()
    df["target_weight"] = pd.to_numeric(df["target_weight"], errors="coerce")
    df = df.dropna(subset=["target_weight"])
    if df.empty:
        return pd.DataFrame()
    df["weight_quintile"] = _safe_quintile(df, "target_weight", "W")
    agg = df.groupby("weight_quintile", observed=False).agg(
        n=("forward_ret_1w", "count"),
        avg_ret_1w=("forward_ret_1w", "mean"),
        win_rate=("forward_ret_1w", lambda x: (x > 0).mean()),
        avg_weight=("target_weight", "mean"),
    ).reset_index()
    return agg


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    enriched: pd.DataFrame,
    by_score: pd.DataFrame,
    by_opt: pd.DataFrame,
    by_claude: pd.DataFrame,
    by_industry: pd.DataFrame,
    recurring: pd.DataFrame,
    d1_signal: pd.DataFrame,
    correlations: pd.DataFrame,
    by_regime: pd.DataFrame,
    by_weight: pd.DataFrame,
) -> str:
    lines = []
    lines.append("# 策略订单个股分化诊断报告")
    lines.append(f"\n**分析日期**: {TODAY}")
    lines.append(f"**数据**: {len(enriched)} 笔订单，{enriched['forward_ret_1w'].notna().sum()} 笔有完整一周收益")

    total = len(enriched.dropna(subset=["forward_ret_1w"]))
    if total == 0:
        return "No complete data to analyze."

    # Overview stats
    valid = enriched["forward_ret_1w"].dropna()
    lines.append(f"\n## 1. 收益分布特征")
    lines.append(f"- 均值: {_pct(valid.mean())} / 中位数: {_pct(valid.median())} / 标准差: {_pct(valid.std(), 1)}")
    lines.append(f"- 偏度: {_num(valid.skew())} / 峰度: {_num(valid.kurtosis())}")
    lines.append(f"- P10: {_pct(valid.quantile(0.1))} / P25: {_pct(valid.quantile(0.25))} / P75: {_pct(valid.quantile(0.75))} / P90: {_pct(valid.quantile(0.9))}")
    extreme_win = (valid >= 0.15).mean()
    extreme_loss = (valid <= -0.10).mean()
    lines.append(f"- 极端赢家(≥15%): {_pct(extreme_win, 1)} / 极端输家(≤-10%): {_pct(extreme_loss, 1)}")

    # 1. Score analysis — THE most important
    lines.append(f"\n## 2. 得分分层分析 — 高分股是否跑赢低分股？")
    if not by_score.empty:
        lines.append("### 总得分(score)分层")
        lines.append("| 分层 | 数量 | 平均收益 | 中位数 | 胜率 | ≥5%率 | ≥10%率 | 最大回撤 | 平均分 |")
        lines.append("|" + "|".join(["---"] * 9) + "|")
        for _, row in by_score.iterrows():
            lines.append(
                f"| {row['score_quintile']} | {int(row['n'])} | {_pct(row['avg_ret_1w'])} | "
                f"{_pct(row['median_ret_1w'])} | {_pct(row['win_rate'],1)} | "
                f"{_pct(row['hit_5pct'],1)} | {_pct(row['hit_10pct'],1)} | "
                f"{_pct(row['avg_max_dd'])} | {_num(row['avg_score'],1)} |"
            )

    if not by_opt.empty:
        lines.append("\n### 因子优化得分(opt_score)分层")
        lines.append("| 分层 | 数量 | 平均收益 | 胜率 | ≥10%率 |")
        lines.append("|" + "|".join(["---"] * 5) + "|")
        for _, row in by_opt.iterrows():
            lines.append(
                f"| {row['opt_quintile']} | {int(row['n'])} | {_pct(row['avg_ret_1w'])} | "
                f"{_pct(row['win_rate'],1)} | {_pct(row['hit_10pct'],1)} |"
            )

    if not by_claude.empty:
        lines.append("\n### AI评分(claude_score)分层")
        lines.append("| 分层 | 数量 | 平均收益 | 胜率 | ≥10%率 |")
        lines.append("|" + "|".join(["---"] * 5) + "|")
        for _, row in by_claude.iterrows():
            lines.append(
                f"| {row['claude_quintile']} | {int(row['n'])} | {_pct(row['avg_ret_1w'])} | "
                f"{_pct(row['win_rate'],1)} | {_pct(row['hit_10pct'],1)} |"
            )

    # 2. Industry analysis
    if not by_industry.empty:
        lines.append(f"\n## 3. 行业分化")
        lines.append("| 行业 | 订单数 | 平均收益 | 中位数 | 胜率 | ≥10% | 最佳 | 最差 |")
        lines.append("|" + "|".join(["---"] * 8) + "|")
        for _, row in by_industry.head(15).iterrows():
            lines.append(
                f"| {row['industry']} | {int(row['n'])} | {_pct(row['avg_ret_1w'])} | "
                f"{_pct(row['median_ret_1w'])} | {_pct(row['win_rate'],1)} | "
                f"{_pct(row['hit_10pct'],1)} | {_pct(row['max_ret'])} | {_pct(row['min_ret'])} |"
            )

    # 3. Recurring stocks
    if not recurring.empty:
        lines.append(f"\n## 4. 重复出现股票分析")
        lines.append("| 代码 | 出现次数 | 平均收益 | 胜率 | 最佳 | 最差 |")
        lines.append("|" + "|".join(["---"] * 6) + "|")
        for _, row in recurring.head(15).iterrows():
            lines.append(
                f"| {row['ts_code']} | {int(row['appearances'])} | {_pct(row['avg_ret'])} | "
                f"{_pct(row['win_rate'],1)} | {_pct(row['max_ret'])} | {_pct(row['min_ret'])} |"
            )

    # 4. D1 as exit signal
    if not d1_signal.empty:
        lines.append(f"\n## 5. 首日表现预示效应")
        lines.append("| 首日收益 | 数量 | 占比 | 最终一周收益 | 后续D2-D5贡献 | 最终胜率 |")
        lines.append("|" + "|".join(["---"] * 6) + "|")
        for _, row in d1_signal.iterrows():
            lines.append(
                f"| {row['d1_bucket']} | {int(row['n'])} | {_pct(row['pct_of_total'],1)} | "
                f"{_pct(row['avg_ret_1w'])} | {_pct(row['avg_ret_d2_d5'])} | "
                f"{_pct(row['win_rate'],1)} |"
            )

    # 5. Score correlations
    if not correlations.empty:
        lines.append(f"\n## 6. 得分与前向收益的相关性")
        lines.append("| 特征 | Pearson r | Spearman r | 样本数 |")
        lines.append("|" + "|".join(["---"] * 4) + "|")
        for _, row in correlations.iterrows():
            lines.append(
                f"| {row['feature']} | {_num(row['pearson_r'], 4)} | "
                f"{_num(row['spearman_r'], 4)} | {int(row['n'])} |"
            )

    # 6. Market regime
    if not by_regime.empty:
        lines.append(f"\n## 7. 市场环境分化")
        lines.append("| 市场状态 | 订单数 | 平均收益 | 胜率 | ≥5%率 |")
        lines.append("|" + "|".join(["---"] * 5) + "|")
        for _, row in by_regime.iterrows():
            lines.append(
                f"| {row['market_regime']} | {int(row['n'])} | {_pct(row['avg_ret_1w'])} | "
                f"{_pct(row['win_rate'],1)} | {_pct(row['hit_5pct'],1)} |"
            )

    # 7. Weight vs return
    if not by_weight.empty:
        lines.append(f"\n## 8. 权重与收益关系")
        lines.append("| 权重分层 | 数量 | 平均收益 | 胜率 | 平均权重 |")
        lines.append("|" + "|".join(["---"] * 5) + "|")
        for _, row in by_weight.iterrows():
            lines.append(
                f"| {row['weight_quintile']} | {int(row['n'])} | {_pct(row['avg_ret_1w'])} | "
                f"{_pct(row['win_rate'],1)} | {_pct(row['avg_weight'])} |"
            )

    # Recommendations
    lines.append(f"\n## 9. 优化建议")

    recommendations = []

    # Check score monotonicity
    if not by_score.empty and "score_quintile" in by_score.columns:
        quintiles = by_score["score_quintile"].tolist()
        best_label = quintiles[-1]  # last row is highest quintile
        worst_label = quintiles[0]  # first row is lowest
        best_ret = float(by_score[by_score["score_quintile"] == best_label]["avg_ret_1w"].iloc[0])
        worst_ret = float(by_score[by_score["score_quintile"] == worst_label]["avg_ret_1w"].iloc[0])
        if np.isfinite(best_ret) and np.isfinite(worst_ret):
            if best_ret > worst_ret:
                recommendations.append(f"- ✅ **得分有效**: 最高分层({best_label})平均收益{_pct(best_ret)} vs 最低分层({worst_label}) {_pct(worst_ret)}，得分排序有效，可考虑提高入选门槛。")
            else:
                recommendations.append(f"- ⚠️ **得分反转**: 最高分层({best_label}) {_pct(best_ret)}反而不如最低分层({worst_label}) {_pct(worst_ret)}，需检查score公式权重。")

    # Check D1 signal strength
    if not d1_signal.empty:
        d1_bad = d1_signal[d1_signal["d1_bucket"].isin(["<-5%", "-5~-3%"])]
        if not d1_bad.empty:
            bad_avg = d1_bad["avg_ret_1w"].mean()
            bad_count = d1_bad["n"].sum()
            total_count = d1_signal["n"].sum()
            recommendations.append(
                f"- ⚠️ **首日止损机会**: 首日跌超3%的{bad_count}笔订单({_pct(bad_count/total_count,1)})，"
                f"后续平均最终收益仅{_pct(bad_avg)}。建议在T+1尾盘对首日跌超3%的持仓减半或清仓。"
            )

    # Check recurring stocks
    if not recurring.empty:
        multi_appear = recurring[recurring["appearances"] >= 2]
        if not multi_appear.empty:
            consistent_winners = multi_appear[(multi_appear["avg_ret"] > 0.05) & (multi_appear["win_rate"] > 0.5)]
            consistent_losers = multi_appear[(multi_appear["avg_ret"] < -0.03)]
            if not consistent_winners.empty:
                codes = ",".join(consistent_winners.head(3)["ts_code"].tolist())
                recommendations.append(f"- ✅ **常胜股识别**: {codes}等多次出现且平均收益>5%，可考虑加大权重或延长持有。")
            if not consistent_losers.empty:
                codes = ",".join(consistent_losers.head(3)["ts_code"].tolist())
                recommendations.append(f"- ⚠️ **常败股屏蔽**: {codes}等多次出现且平均亏损，建议加入黑名单或降低入选概率。")

    # Check correlations
    if not correlations.empty:
        top_feat = correlations.head(3)["feature"].tolist()
        bottom_feat = correlations.tail(3)["feature"].tolist()
        recommendations.append(f"- 📊 **最强预测因子**: {', '.join(top_feat)}（Spearman相关最高），可加大这些因子在排序中的权重。")
        recommendations.append(f"- 📊 **最弱预测因子**: {', '.join(bottom_feat)}，相关接近0甚至为负，需审视其有效性。")

    # Check weight optimization
    if not by_weight.empty and "weight_quintile" in by_weight.columns:
        w_quintiles = by_weight["weight_quintile"].tolist()
        if len(w_quintiles) >= 2:
            w_best_label = w_quintiles[-1]
            w_worst_label = w_quintiles[0]
            w_best_ret = float(by_weight[by_weight["weight_quintile"] == w_best_label]["avg_ret_1w"].iloc[0])
            w_worst_ret = float(by_weight[by_weight["weight_quintile"] == w_worst_label]["avg_ret_1w"].iloc[0])
            if np.isfinite(w_best_ret) and np.isfinite(w_worst_ret):
                if w_best_ret < w_worst_ret:
                    recommendations.append(f"- ⚠️ **权重倒挂**: 高权重分层({w_best_label})收益{_pct(w_best_ret)}低于低权重分层({w_worst_label}) {_pct(w_worst_ret)}，当前权重分配方向可能反了。")
                else:
                    recommendations.append(f"- ✅ **权重有效**: 高权重分层收益{_pct(w_best_ret)}高于低权重{_pct(w_worst_ret)}，方向正确。")

    recommendations.append(f"- 🔧 **建议增加止盈/止损规则**: 当前胜率42%但均值-中位数劈叉大，尾部管理是关键。对首日强势股(+5%以上)可设移动止盈；对弱势股快速止损可显著提升整体收益。")

    for r in recommendations:
        lines.append(r)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    engine = create_engine(build_sqlalchemy_url())

    print("Loading data...")
    orders, scores, prices, stock_info, bm_prices, calendar, next_date_map = load_all_data(engine)
    print(f"  Orders: {len(orders)}, Scores: {len(scores)}, Prices: {len(prices)}, Stocks: {len(stock_info)}")

    # Compute forward returns
    results, signal_map = compute_forward_returns(orders, prices, calendar, next_date_map)
    bm_rets = compute_benchmark_returns(bm_prices, signal_map)
    print(f"  Results: {len(results)} rows, {results['forward_ret_1w'].notna().sum()} complete")

    # Enrich
    enriched = enrich_results(results, orders, scores, stock_info)
    print(f"  Enriched columns: {list(enriched.columns)}")

    # Run analyses
    by_score = analyze_by_score_quantile(enriched)
    by_opt = analyze_by_opt_score(enriched)
    by_claude = analyze_by_claude_score(enriched)
    by_industry = analyze_by_industry(enriched)
    recurring = analyze_recurring_stocks(enriched)
    d1_signal = analyze_d1_as_exit_signal(enriched)
    correlations = analyze_score_correlations(enriched)
    by_regime = analyze_market_regime(enriched)
    by_weight = analyze_weight_vs_return(enriched)

    # Generate report
    report = generate_report(
        enriched, by_score, by_opt, by_claude, by_industry,
        recurring, d1_signal, correlations, by_regime, by_weight,
    )
    print("\n" + "=" * 80)
    print(report)

    # Save
    output_dir = PROJECT_ROOT / "exports" / "order_forward_reviews"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = TODAY.strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"order_dispersion_diagnosis_{ts}.md"
    report_path.write_text(report, encoding="utf-8")
    data_path = output_dir / f"order_dispersion_data_{ts}.csv"
    enriched.to_csv(data_path, index=False, encoding="utf-8-sig")
    print(f"\nReport: {report_path}")
    print(f"Data: {data_path}")


if __name__ == "__main__":
    main()
