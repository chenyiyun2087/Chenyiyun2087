"""Grid search for optimal nonlinear score transformation parameters.

Tests combinations of (center, half_width, strength) on historical data
to find parameters that maximize the Spearman rank correlation between
transformed score and forward 1-week return.

The transformation is a bounded cubic pull toward ``center``.  The cubic
correction is capped at ``strength * distance_from_center`` outside the
central interval, so it cannot reverse the ordering of raw scores.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


def apply_transform(raw_score: pd.Series, center: float, half_width: float, strength: float) -> pd.Series:
    """Apply a bounded, monotone cubic contraction transformation."""
    deviation = (raw_score - center) / half_width
    adjustment = (
        np.sign(deviation)
        * np.minimum(np.abs(deviation) ** 3, np.abs(deviation))
        * half_width
        * strength
    )
    return (raw_score - adjustment).clip(0, 100)


def load_data(engine, lookback_days: int = 35):
    """Load scores and compute forward returns for stocks in score_rank_daily."""
    start = int((date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d"))
    end = int(date.today().strftime("%Y%m%d"))

    # Calendar
    cal = pd.read_sql(
        text(
            "SELECT cal_date FROM chenyiyun.dim_trade_cal "
            "WHERE exchange='SSE' AND is_open=1 ORDER BY cal_date"
        ),
        engine,
    )
    calendar = sorted(cal["cal_date"].astype(int).tolist())
    cal_set = set(calendar)

    # Build next-trade-date map
    next_date_map = {}
    for i, d in enumerate(calendar[:-1]):
        next_date_map[d] = calendar[i + 1]
    next_date_map[calendar[-1]] = None

    # Load scores
    scores = pd.read_sql(
        text(
            """
        SELECT trade_date, symbol, score, opt_score, claude_score,
               bs_score, bs_consensus_score, bs_model_prob,
               s_trend, s_breakout, s_volume, s_rs, s_contraction, s_liquidity,
               industry
        FROM chenyiyun.score_rank_daily
        WHERE trade_date >= :start AND trade_date <= :end
          AND score IS NOT NULL
        ORDER BY trade_date, symbol
        """
        ),
        engine,
        params={"start": start, "end": end},
    )
    for col in ["trade_date"]:
        scores[col] = scores[col].apply(lambda x: int(str(x).replace("-", "")) if pd.notna(x) else 0)

    # Get all symbols for price loading
    symbols = scores["symbol"].dropna().unique().tolist()
    # Build ts_codes with suffix
    code_variants = set()
    for s in symbols:
        code_variants.add(s)
        for suffix in [".SZ", ".SH", ".BJ"]:
            code_variants.add(s + suffix)
    price_codes = list(code_variants)

    placeholders = ",".join([f":pc_{i}" for i in range(len(price_codes))])
    params = {f"pc_{i}": c for i, c in enumerate(price_codes)}
    params["start_date"] = int(scores["trade_date"].min())
    params["end_date"] = max(calendar)

    prices = pd.read_sql(
        text(
            f"""
        SELECT trade_date, ts_code, adj_open, adj_close
        FROM tushare_stock.dwd_stock_daily_standard
        WHERE ts_code IN ({placeholders})
          AND trade_date >= :start_date AND trade_date <= :end_date
        """
        ),
        engine,
        params=params,
    )

    # Normalize ts_code
    prices["symbol"] = prices["ts_code"].str.replace(r"\.[A-Z]+$", "", regex=True)

    # Compute forward returns
    print(f"  Loaded {len(scores)} scores, {len(prices)} price rows, {len(symbols)} symbols")

    price_idx = prices.set_index(["symbol", "trade_date"])
    rets = []
    for _, row in scores.iterrows():
        sd = int(row["trade_date"])
        sym = str(row["symbol"])
        if sd not in cal_set:
            continue
        exec_date = next_date_map.get(sd)
        if exec_date is None:
            continue
        # Get exit date (5th trading day)
        exit_dates = [exec_date]
        cursor = exec_date
        for _ in range(4):
            nxt = next_date_map.get(cursor)
            if nxt is None:
                break
            exit_dates.append(nxt)
            cursor = nxt
        if len(exit_dates) < 5:
            continue
        last_close_date = exit_dates[-1]
        try:
            entry_open = float(price_idx.loc[(sym, exec_date), "adj_open"])
            last_close = float(price_idx.loc[(sym, last_close_date), "adj_close"])
        except (KeyError, TypeError):
            continue
        if np.isfinite(entry_open) and np.isfinite(last_close) and entry_open > 0:
            rets.append(
                {
                    "trade_date": sd,
                    "symbol": sym,
                    "forward_ret_1w": last_close / entry_open - 1.0,
                }
            )

    rets_df = pd.DataFrame(rets)
    print(f"  Computed {len(rets_df)} forward returns")

    # Merge scores with returns
    merged = scores.merge(rets_df, on=["trade_date", "symbol"], how="inner")
    print(f"  Merged: {len(merged)} rows with both score and forward return")
    return merged


def run_grid_search(df: pd.DataFrame):
    """Grid search over transformation parameters."""
    raw = df["score"].copy()
    target = df["forward_ret_1w"].copy()

    centers = [45, 50, 55, 60]
    half_widths = [20, 25, 30, 35]
    strengths = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

    results = []
    for center, hw, strength in product(centers, half_widths, strengths):
        transformed = apply_transform(raw, center, hw, strength)
        # Spearman correlation
        spearman = transformed.corr(target, method="spearman")
        # Also check: mean score in top quintile vs bottom quintile
        valid_df = pd.DataFrame({"score_t": transformed, "ret": target}).dropna()
        if len(valid_df) < 20:
            continue
        valid_df["quintile"] = pd.qcut(valid_df["score_t"], 5, labels=False, duplicates="drop")
        q_max = valid_df[valid_df["quintile"] == valid_df["quintile"].max()]["ret"].mean()
        q_min = valid_df[valid_df["quintile"] == valid_df["quintile"].min()]["ret"].mean()
        q_spread = q_max - q_min
        # Also check monotonicity: does each higher quintile have higher return?
        quintile_means = valid_df.groupby("quintile")["ret"].mean()
        monotonic = int((quintile_means.diff().dropna() > 0).all())

        results.append(
            {
                "center": center,
                "half_width": hw,
                "strength": strength,
                "spearman_r": spearman,
                "q5_ret": q_max,
                "q1_ret": q_min,
                "q_spread": q_spread,
                "monotonic": monotonic,
                "mean_score": transformed.mean(),
                "std_score": transformed.std(),
            }
        )

    results_df = pd.DataFrame(results).sort_values("spearman_r", ascending=False)
    return results_df


def analyze_results(results_df: pd.DataFrame, df: pd.DataFrame):
    """Print analysis and recommend best parameters."""
    raw_corr = df["score"].corr(df["forward_ret_1w"], method="spearman")
    print(f"\n当前 score 与 forward_ret_1w 的 Spearman r = {raw_corr:.5f}")

    print("\n=== Top 15 参数组合（按 Spearman r 排序） ===")
    print(results_df.head(15).to_string(index=False))

    best = results_df.iloc[0]
    print(f"\n=== 最优参数 ===")
    print(f"  center={best['center']}, half_width={best['half_width']}, strength={best['strength']}")
    print(f"  Spearman r: {best['spearman_r']:.5f} (改善: {best['spearman_r'] - raw_corr:+.5f})")
    print(f"  Q1收益: {best['q1_ret']*100:.2f}% → Q5收益: {best['q5_ret']*100:.2f}% (spread: {best['q_spread']*100:.2f}%)")
    print(f"  monotonic: {'✅' if best['monotonic'] else '❌'}")
    print(f"  变换后评分: mean={best['mean_score']:.1f} std={best['std_score']:.1f}")

    # Find params that are monotonic
    mono = results_df[results_df["monotonic"] == 1]
    if not mono.empty:
        print(f"\n=== 单调性成立的参数组合 (共{len(mono)}组) ===")
        print(mono.head(10).to_string(index=False))

    # Stability: check if best params are stable across near-optimal values
    top5 = results_df.head(5)
    print(f"\n=== Top5 参数稳定性 ===")
    for col in ["center", "half_width", "strength"]:
        vals = top5[col].tolist()
        print(f"  {col}: {vals} (range: {min(vals)}-{max(vals)})")

    return best


def main():
    engine = create_engine(build_sqlalchemy_url())
    print("Loading data...")
    df = load_data(engine)

    if len(df) < 50:
        print(f"ERROR: Only {len(df)} valid rows, need >=50 for reliable grid search")
        return

    print(f"\nRunning grid search on {len(df)} rows...")
    results_df = run_grid_search(df)
    best = analyze_results(results_df, df)

    # Save results
    out_dir = PROJECT_ROOT / "exports" / "order_forward_reviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    results_df.to_csv(out_dir / "grid_search_score_transform.csv", index=False, encoding="utf-8-sig")

    # Print recommended config snippet
    print(f"\n=== 推荐配置（放入 scorer.py） ===")
    print(f"center = {best['center']}")
    print(f"half_width = {best['half_width']}")
    print(f"# deviation = (raw_score - center) / half_width")
    print(
        "# adjustment = sign(deviation) * min(abs(deviation)**3, abs(deviation)) "
        f"* half_width * {best['strength']}"
    )
    print(f"# score = (raw_score - adjustment).clip(0, 100)")


if __name__ == "__main__":
    main()
