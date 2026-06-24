#!/usr/bin/env python3
"""
Build training dataset for B/S point detection model.

Reads OCR labels from chenyiyun.bs_detection_results and merges with
technical indicators from tushare_stock tables to produce a labeled
training dataset.

Usage:
    python scripts/research/build_bs_training_dataset.py \
        --output data/processed/bs_training_dataset.parquet \
        --lookback 60
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

# --- Config ---
CHENYIYUN_DB = "mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4"
TUSHARE_DB = "mysql+pymysql://root:19871019@localhost:3306/tushare_stock?charset=utf8mb4"

# QFQ (前复权) columns from ods_stk_factor to use as features
PRICE_COLS = [
    "open_qfq", "high_qfq", "low_qfq", "close_qfq", "pre_close_qfq",
]
VOLUME_COLS = [
    "vol", "amount", "turnover_rate", "volume_ratio",
]
VALUATION_COLS = [
    "pe", "pe_ttm", "pb", "total_mv", "circ_mv",
]
MACD_COLS = ["macd_qfq", "macd_dif_qfq", "macd_dea_qfq"]
KDJ_COLS = ["kdj_qfq", "kdj_k_qfq", "kdj_d_qfq"]
RSI_COLS = ["rsi_qfq_6", "rsi_qfq_12", "rsi_qfq_24"]
BOLL_COLS = ["boll_upper_qfq", "boll_mid_qfq", "boll_lower_qfq"]
MA_COLS = ["ma_qfq_5", "ma_qfq_10", "ma_qfq_20", "ma_qfq_60", "ma_qfq_250"]
MISC_COLS = [
    "cci_qfq", "atr_qfq",
    "bias1_qfq", "bias2_qfq", "bias3_qfq",
    "dmi_pdi_qfq", "dmi_mdi_qfq", "dmi_adx_qfq", "dmi_adxr_qfq",
    "mfi_qfq", "mtm_qfq", "mtmma_qfq",
    "roc_qfq", "maroc_qfq",
    "wr_qfq", "wr1_qfq",
    "vr_qfq", "psy_qfq", "psyma_qfq",
    "trix_qfq", "trma_qfq",
    "bbi_qfq",
    "updays", "downdays", "lowdays", "topdays",
]

ALL_FACTOR_COLS = (
    PRICE_COLS + VOLUME_COLS + VALUATION_COLS +
    MACD_COLS + KDJ_COLS + RSI_COLS + BOLL_COLS +
    MA_COLS + MISC_COLS
)

# Additional tables to join
DWS_TABLES = {
    "dws_tech_pattern": ["hma_slope", "rsi_14", "boll_width"],
    "dws_capital_flow": ["main_net_ratio", "main_net_ma5", "vol_price_corr"],
    "dws_chip_dynamics": ["profit_ratio", "profit_pressure", "support_strength"],
    "dws_liquidity_factor": ["turnover_vol_20", "amihud_20"],
}

# ADC feature tables (v5): pre-computed scores from AShareDataCenter
ADC_TABLES = {
    "ads_stock_bs_signal": [
        "signal_strength", "signal_quality_score",
        "trend_context_score", "volume_confirm_score",
        "entry_signal_confidence", "exit_signal_confidence",
    ],
    "ads_stock_score_daily": [
        "tech_score", "capital_score", "sentiment_score", "chip_score",
        "total_score", "score_rank_pct",
    ],
    "ads_selection_digest_history_di": [
        "main_score", "smart_money_score", "predicted_return_5d", "confidence",
    ],
}


def stock_code_to_ts_code(code: str) -> str:
    """Convert 6-digit stock code to ts_code format (e.g. '000001' -> '000001.SZ')."""
    code = str(code).zfill(6)
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    elif code.startswith(("6", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"  # default


def load_labels(engine) -> pd.DataFrame:
    """Load OCR labels from bs_detection_results and clean multi-day B/S runs."""
    query = """
    SELECT
        stock_code,
        batch_date,
        has_buy_signal,
        has_sell_signal
    FROM bs_detection_results
    WHERE batch_name NOT LIKE 'ml_detect%%'
    ORDER BY batch_date, stock_code
    """
    df = pd.read_sql(query, engine)
    df["ts_code"] = df["stock_code"].apply(stock_code_to_ts_code)

    # --- Label cleaning: keep only FIRST day of each B/S run ---
    # A "run" = consecutive trading days where the same stock has the signal
    df = df.sort_values(["stock_code", "batch_date"])

    for signal_col in ["has_buy_signal", "has_sell_signal"]:
        # Identify the previous day's signal for the same stock
        df[f"{signal_col}_prev"] = df.groupby("stock_code")[signal_col].shift(1)
        # A signal is "new" if: (a) it's 1 today, AND (b) it was NOT 1 yesterday
        df[f"{signal_col}_is_new"] = (
            (df[signal_col] == 1) & (df[f"{signal_col}_prev"] != 1)
        ).astype(int)
        # Count cleaned signals
        n_orig = int(df[signal_col].sum())
        n_cleaned = int(df[f"{signal_col}_is_new"].sum())
        n_removed = n_orig - n_cleaned
        print(f"[Labels] {signal_col}: {n_orig} original → {n_cleaned} after dedup "
              f"(removed {n_removed} continuation days, {n_removed/n_orig*100:.1f}%)")
        # Replace original signal with cleaned version
        df[signal_col] = df[f"{signal_col}_is_new"]

    # Drop helper columns
    df = df.drop(columns=[c for c in df.columns if c.endswith("_prev") or c.endswith("_is_new")])

    print(f"[Labels] Loaded {len(df)} records (cleaned), "
          f"{df['has_buy_signal'].sum()} buys, "
          f"{df['has_sell_signal'].sum()} sells")
    return df


def load_factor_data(engine_tushare, trade_dates: list, ts_codes: list) -> pd.DataFrame:
    """Batch-load factor data for given dates and stocks."""
    cols_str = ", ".join(ALL_FACTOR_COLS)
    dates_str = ",".join(str(d) for d in trade_dates)

    # Use chunked loading for large result sets
    dfs = []
    chunk_size = 500
    for i in range(0, len(ts_codes), chunk_size):
        chunk_codes = ts_codes[i:i + chunk_size]
        codes_str = ",".join(f"'{c}'" for c in chunk_codes)

        sql = f"""
        SELECT ts_code, trade_date, {cols_str}
        FROM ods_stk_factor
        WHERE trade_date IN ({dates_str})
          AND ts_code IN ({codes_str})
        ORDER BY ts_code, trade_date
        """
        chunk_df = pd.read_sql(sql, engine_tushare)
        dfs.append(chunk_df)

    result = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    print(f"[Factors] Loaded {len(result)} rows from ods_stk_factor "
          f"({len(trade_dates)} dates, {len(ts_codes)} stocks)")
    return result


def load_adc_tables(engine_tushare, trade_dates: list) -> dict:
    """Load ADC (AShareDataCenter) feature tables."""
    results = {}
    dates_str = ",".join(str(d) for d in trade_dates)
    for table_name, cols in ADC_TABLES.items():
        cols_str = ", ".join(["ts_code", "trade_date"] + cols)
        try:
            sql = f"SELECT {cols_str} FROM {table_name} WHERE trade_date IN ({dates_str})"
            df = pd.read_sql(sql, engine_tushare)
            results[table_name] = df
            print(f"[ADC] Loaded {len(df)} rows from {table_name}")
        except Exception as e:
            print(f"[ADC] Skipped {table_name}: {e}")
    return results


def load_dws_tables(engine_tushare, trade_dates: list) -> dict:
    """Load DWS layer tables for given dates."""
    results = {}
    dates_str = ",".join(str(d) for d in trade_dates)

    for table_name, cols in DWS_TABLES.items():
        cols_str = ", ".join(["ts_code", "trade_date"] + cols)
        sql = f"""
        SELECT {cols_str}
        FROM {table_name}
        WHERE trade_date IN ({dates_str})
        """
        df = pd.read_sql(sql, engine_tushare)
        results[table_name] = df
        print(f"[DWS] Loaded {len(df)} rows from {table_name}")

    return results


def engineer_features(factor_df: pd.DataFrame, dws_dfs: dict, adc_dfs: dict = None) -> pd.DataFrame:
    """
    Engineer derived features from raw factor data.
    Input: factor data sorted by ts_code, trade_date.
    Output: DataFrame with both raw and derived features at the signal date.
    """
    df = factor_df.copy()

    # --- Price-derived features ---
    df["ret_1d"] = df["close_qfq"] / df["pre_close_qfq"] - 1.0

    # Rolling returns (grouped by stock)
    df = df.sort_values(["ts_code", "trade_date"])
    for n in [2, 3, 5, 10, 20]:
        df[f"ret_{n}d"] = df.groupby("ts_code")["close_qfq"].transform(
            lambda x: x.pct_change(n)
        )

    # Short-term acceleration: how is ret_1d changing?
    df["ret_1d_prev"] = df.groupby("ts_code")["ret_1d"].shift(1)
    df["ret_1d_accel"] = df["ret_1d"] - df["ret_1d_prev"]
    # Volume acceleration
    df["vol_ratio_prev"] = df.groupby("ts_code")["volume_ratio"].shift(1)
    df["vol_ratio_accel"] = df["volume_ratio"] - df["vol_ratio_prev"]

    # --- Trend freshness features (v3): 5-day change in key indicators ---
    # These help the model distinguish "just started trending" from "trending for weeks"
    FRESHNESS_WINDOW = 5
    FRESHNESS_COLS = [
        "close_vs_ma20", "close_vs_ma60",
        "rsi_qfq_6", "kdj_k_qfq", "macd_qfq",
    ]
    for col in FRESHNESS_COLS:
        if col in df.columns:
            df[f"{col}_{FRESHNESS_WINDOW}d_ago"] = df.groupby("ts_code")[col].shift(FRESHNESS_WINDOW)
            df[f"{col}_chg_{FRESHNESS_WINDOW}d"] = df[col] - df[f"{col}_{FRESHNESS_WINDOW}d_ago"]

    # Boll position freshness
    if "boll_position" in df.columns:
        df["boll_position_5d_ago"] = df.groupby("ts_code")["boll_position"].shift(5)
        df["boll_position_chg_5d"] = df["boll_position"] - df["boll_position_5d_ago"]

    # ret_5d "freshness" — how much of the 5-day return happened in the last 1 day?
    if "ret_1d" in df.columns and "ret_5d" in df.columns:
        # If ret_5d is large but ret_1d is small → momentum is fading
        # If ret_5d is moderate but ret_1d is large → just breaking out
        df["ret_5d_minus_ret_1d"] = df["ret_5d"] - df["ret_1d"]  # earlier 4-day return
        df["ret_5d_ago"] = df.groupby("ts_code")["ret_5d"].shift(5)
        df["ret_5d_accel"] = df["ret_5d"] - df["ret_5d_ago"]  # is 5d return accelerating?

    # Days since first crossing above MA20 (approximate)
    if "ma_qfq_20" in df.columns:
        prev_close = df.groupby("ts_code")["close_qfq"].shift(1)
        prev_ma20 = df.groupby("ts_code")["ma_qfq_20"].shift(1)
        was_below = prev_close <= prev_ma20
        is_above = df["close_qfq"] > df["ma_qfq_20"]
        df["just_crossed_above_ma20"] = (was_below & is_above).astype(int)
        # How many days above MA20? (running count, reset when cross below)
        df["above_ma20"] = (df["close_qfq"] > df["ma_qfq_20"]).astype(int)
        # Count consecutive: for each stock, cumsum resets when above_ma20=0
        df["days_above_ma20"] = df.groupby("ts_code")["above_ma20"].transform(
            lambda x: x.groupby((x == 0).cumsum()).cumsum()
        )
        # Cap at 60 (long trends are all "mature")
        df["days_above_ma20"] = df["days_above_ma20"].clip(0, 60)

    # Close position relative to MAs
    for n in [5, 10, 20, 60, 250]:
        ma_col = f"ma_qfq_{n}"
        if ma_col in df.columns:
            df[f"close_vs_ma{n}"] = df["close_qfq"] / df[ma_col] - 1.0

    # MA alignment flags
    if all(f"ma_qfq_{n}" in df.columns for n in [5, 10, 20]):
        df["ma_bull_align"] = (
            (df["ma_qfq_5"] > df["ma_qfq_10"]) &
            (df["ma_qfq_10"] > df["ma_qfq_20"])
        ).astype(int)
        df["ma_bear_align"] = (
            (df["ma_qfq_5"] < df["ma_qfq_10"]) &
            (df["ma_qfq_10"] < df["ma_qfq_20"])
        ).astype(int)

    # MACD derived
    if all(c in df.columns for c in ["macd_dif_qfq", "macd_dea_qfq"]):
        df["macd_diff"] = df["macd_dif_qfq"] - df["macd_dea_qfq"]
    if "macd_qfq" in df.columns:
        df["macd_hist_sign"] = np.sign(df["macd_qfq"])
        # Histogram change direction (shift within group)
        df["macd_hist_prev"] = df.groupby("ts_code")["macd_qfq"].shift(1)
        df["macd_hist_chg"] = df["macd_qfq"] - df["macd_hist_prev"]
        # Zero-crossing: was negative, now positive (golden cross signal)
        df["macd_hist_zero_cross_up"] = (
            (df["macd_hist_prev"] <= 0) & (df["macd_qfq"] > 0)
        ).astype(int)
        df["macd_hist_zero_cross_down"] = (
            (df["macd_hist_prev"] >= 0) & (df["macd_qfq"] < 0)
        ).astype(int)
        # DIF-DEA golden cross
        df["macd_dif_prev"] = df.groupby("ts_code")["macd_dif_qfq"].shift(1)
        df["macd_dea_prev"] = df.groupby("ts_code")["macd_dea_qfq"].shift(1)
        df["macd_golden_cross"] = (
            (df["macd_dif_prev"] <= df["macd_dea_prev"]) &
            (df["macd_dif_qfq"] > df["macd_dea_qfq"])
        ).astype(int)
        df["macd_death_cross"] = (
            (df["macd_dif_prev"] >= df["macd_dea_prev"]) &
            (df["macd_dif_qfq"] < df["macd_dea_qfq"])
        ).astype(int)

    # KDJ derived
    if all(c in df.columns for c in ["kdj_k_qfq", "kdj_d_qfq"]):
        df["kdj_k_prev"] = df.groupby("ts_code")["kdj_k_qfq"].shift(1)
        df["kdj_d_prev"] = df.groupby("ts_code")["kdj_d_qfq"].shift(1)
        df["kdj_golden_cross"] = (
            (df["kdj_k_prev"] <= df["kdj_d_prev"]) &
            (df["kdj_k_qfq"] > df["kdj_d_qfq"])
        ).astype(int)
        df["kdj_death_cross"] = (
            (df["kdj_k_prev"] >= df["kdj_d_prev"]) &
            (df["kdj_k_qfq"] < df["kdj_d_qfq"])
        ).astype(int)
        df["kdj_k_oversold"] = (df["kdj_k_qfq"] < 22).astype(int)
        df["kdj_k_overbought"] = (df["kdj_k_qfq"] > 80).astype(int)

    # RSI derived
    if "rsi_qfq_6" in df.columns:
        df["rsi6_oversold"] = (df["rsi_qfq_6"] < 25).astype(int)
        df["rsi6_overbought"] = (df["rsi_qfq_6"] > 75).astype(int)
    if "rsi_qfq_12" in df.columns:
        df["rsi12_oversold"] = (df["rsi_qfq_12"] < 30).astype(int)

    # BOLL position
    if all(c in df.columns for c in ["boll_upper_qfq", "boll_lower_qfq", "boll_mid_qfq"]):
        boll_range = df["boll_upper_qfq"] - df["boll_lower_qfq"]
        df["boll_position"] = np.where(
            boll_range > 0,
            (df["close_qfq"] - df["boll_lower_qfq"]) / boll_range,
            0.5
        )
        df["boll_lower_touch"] = (df["low_qfq"] <= df["boll_lower_qfq"]).astype(int)
        df["boll_upper_touch"] = (df["high_qfq"] >= df["boll_upper_qfq"]).astype(int)

    # Volume derived
    if "vol" in df.columns:
        df["vol_ma5"] = df.groupby("ts_code")["vol"].transform(
            lambda x: x.rolling(5, min_periods=1).mean()
        )
        df["vol_ratio_5"] = df["vol"] / (df["vol_ma5"] + 1e-10)
        df["vol_spike"] = (df["vol_ratio_5"] > 2.0).astype(int)

    # Breakout: close is N-day high
    for n in [10, 20, 60]:
        df[f"is_{n}d_high"] = (
            df["close_qfq"] == df.groupby("ts_code")["high_qfq"].transform(
                lambda x: x.rolling(n, min_periods=1).max()
            )
        ).astype(int)

    # 20-day drawdown from rolling max
    df["high_20d_max"] = df.groupby("ts_code")["high_qfq"].transform(
        lambda x: x.rolling(20, min_periods=1).max()
    )
    df["mdd_20d"] = df["close_qfq"] / (df["high_20d_max"] + 1e-10) - 1.0

    # CCI status
    if "cci_qfq" in df.columns:
        df["cci_oversold"] = (df["cci_qfq"] < -150).astype(int)
        df["cci_overbought"] = (df["cci_qfq"] > 150).astype(int)

    # --- v4: High-discrimination features for B-point patterns ---
    # Gap from N-day high (stocks near highs often get B points)
    for n in [5, 10, 20]:
        roll_max = df.groupby("ts_code")["high_qfq"].transform(lambda x: x.rolling(n, min_periods=1).max())
        df[f"gap_from_{n}d_high"] = df["close_qfq"] / (roll_max + 1e-10) - 1.0

    # Consecutive up/down days (handle None in pre_close_qfq)
    df["pre_close_qfq"] = df["pre_close_qfq"].fillna(df["close_qfq"])
    df["is_up_day"] = (df["close_qfq"] > df["pre_close_qfq"]).astype(int)
    df["up_streak"] = df.groupby("ts_code")["is_up_day"].transform(
        lambda x: x.groupby((x == 0).cumsum()).cumsum()
    )
    df["down_streak"] = df.groupby("ts_code")["is_up_day"].transform(
        lambda x: (1-x).groupby((x == 1).cumsum()).cumsum()
    )

    # Volume relative to 20-day average volume
    df["vol_ma20"] = df.groupby("ts_code")["vol"].transform(lambda x: x.rolling(20, min_periods=5).mean())
    df["vol_vs_ma20"] = df["vol"] / (df["vol_ma20"] + 1e-10)

    # Price position within day (close vs high-low range)
    day_range = df["high_qfq"] - df["low_qfq"]
    df["close_position_in_day"] = np.where(day_range > 0,
        (df["close_qfq"] - df["low_qfq"]) / day_range, 0.5)

    # RSI divergence signal: price near 10d low but RSI not at 10d low
    rsi10_low = df.groupby("ts_code")["rsi_qfq_6"].transform(lambda x: x.rolling(10, min_periods=3).min())
    price10_low = df.groupby("ts_code")["low_qfq"].transform(lambda x: x.rolling(10, min_periods=3).min())
    near_price_low = (df["low_qfq"] <= price10_low * 1.02)
    rsi_not_at_low = (df["rsi_qfq_6"] > rsi10_low * 1.1)
    df["rsi_bull_divergence"] = (near_price_low & rsi_not_at_low).astype(int)

    # Bollinger squeeze: bandwidth narrowing (volatility contraction)
    if all(c in df.columns for c in ["boll_upper_qfq", "boll_lower_qfq"]):
        df["boll_bandwidth"] = (df["boll_upper_qfq"] - df["boll_lower_qfq"]) / (df["boll_mid_qfq"] + 1e-10)
        bw_prev = df.groupby("ts_code")["boll_bandwidth"].shift(1)
        df["boll_squeeze"] = ((df["boll_bandwidth"] < bw_prev) & (df["boll_bandwidth"] < 0.15)).astype(int)

    # Merge DWS tables
    for table_name, dws_df in dws_dfs.items():
        if dws_df.empty:
            continue
        merge_cols = ["ts_code", "trade_date"]
        overlap = [c for c in dws_df.columns if c not in merge_cols]
        df = df.merge(
            dws_df[merge_cols + overlap],
            on=merge_cols, how="left"
        )

    # Fill NaN for DWS columns (some stocks may not have DWS data)
    dws_all_cols = []
    for cols in DWS_TABLES.values():
        dws_all_cols.extend(cols)
    for c in dws_all_cols:
        if c in df.columns:
            df[c] = df[c].fillna(0)

    # Merge ADC tables (v5)
    if adc_dfs:
        for table_name, adc_df in adc_dfs.items():
            if adc_df is None or adc_df.empty:
                continue
            merge_cols = ["ts_code", "trade_date"]
            overlap = [c for c in adc_df.columns if c not in merge_cols]
            df = df.merge(adc_df[merge_cols + overlap], on=merge_cols, how="left")
        for cols in ADC_TABLES.values():
            for c in cols:
                if c in df.columns:
                    df[c] = df[c].fillna(0)

    return df


def build_dataset(
    output_path: str,
    lookback: int = 60,
    train_end: str = "20260430",
    val_end: str = "20260531",
):
    """Main pipeline: load labels + factors, engineer features, split, save."""
    engine_cy = create_engine(CHENYIYUN_DB)
    engine_ts = create_engine(TUSHARE_DB)

    # 1. Load labels
    labels = load_labels(engine_cy)
    if labels.empty:
        print("ERROR: No OCR labels found in bs_detection_results")
        sys.exit(1)

    unique_dates = sorted(labels["batch_date"].unique())
    unique_codes = sorted(labels["ts_code"].unique())
    print(f"[Labels] {len(unique_dates)} unique dates, {len(unique_codes)} unique stocks")

    # 2. Load factor data (include lookback window before first label date)
    all_dates_needed = set()
    for d in unique_dates:
        date_int = int(d)
        # We need data for that date + some lookback for feature engineering
        all_dates_needed.add(date_int)

    # Also get nearby dates for rolling feature computation
    all_dates_sorted = sorted(all_dates_needed)

    factor_raw = load_factor_data(engine_ts, all_dates_sorted, unique_codes)

    # 3. Load DWS tables
    dws_dfs = load_dws_tables(engine_ts, all_dates_sorted)
    adc_dfs = load_adc_tables(engine_ts, all_dates_sorted)

    # 4. Engineer features
    print("[Features] Engineering derived features...")
    featured = engineer_features(factor_raw, dws_dfs, adc_dfs)

    # 5. Merge labels with features on (ts_code, trade_date=batch_date)
    labels["trade_date"] = labels["batch_date"].astype(int)
    featured["trade_date"] = featured["trade_date"].astype(int)

    dataset = labels.merge(
        featured,
        on=["ts_code", "trade_date"],
        how="left",
        suffixes=("", "_factor")
    )

    # Check merge coverage
    matched = dataset["close_qfq"].notna().sum()
    print(f"[Merge] {matched}/{len(dataset)} records matched with factor data "
          f"({matched/len(dataset)*100:.1f}%)")

    # Drop rows with missing critical features
    critical_cols = ["close_qfq", "vol"]
    dataset = dataset.dropna(subset=critical_cols)

    # 6. Create train/val/test split
    dataset = dataset.sort_values(["batch_date", "stock_code"])

    train_mask = dataset["batch_date"].astype(str) <= train_end
    val_mask = (dataset["batch_date"].astype(str) > train_end) & \
               (dataset["batch_date"].astype(str) <= val_end)
    test_mask = dataset["batch_date"].astype(str) > val_end

    dataset["split"] = "train"
    dataset.loc[val_mask.values, "split"] = "val"
    dataset.loc[test_mask.values, "split"] = "test"

    for split_name in ["train", "val", "test"]:
        split_df = dataset[dataset["split"] == split_name]
        print(f"[Split] {split_name}: {len(split_df)} rows, "
              f"{split_df['has_buy_signal'].sum()} buys, "
              f"{split_df['has_sell_signal'].sum()} sells "
              f"(buy_rate={split_df['has_buy_signal'].mean()*100:.1f}%)")

    # 7. Identify and drop non-feature columns before saving
    meta_cols = [
        "stock_code", "batch_date", "ts_code", "trade_date",
        "has_buy_signal", "has_sell_signal", "split",
    ]
    # Also drop intermediate derived columns that shouldn't be features
    drop_patterns = ["macd_hist_prev", "macd_dif_prev", "macd_dea_prev",
                     "kdj_k_prev", "kdj_d_prev",
                     "macd_hist_sign", "vol_ma5", "vol_ma20", "high_20d_max",
                     "is_up_day", "above_ma20"]
    extra_drop = [c for c in dataset.columns
                  if any(p in c for p in drop_patterns)]
    # But keep some engineered features
    keep_extra = [
        "macd_golden_cross", "macd_death_cross",
        "macd_hist_zero_cross_up", "macd_hist_zero_cross_down",
        "kdj_golden_cross", "kdj_death_cross",
    ]

    feature_cols = [c for c in dataset.columns
                    if c not in meta_cols
                    and c not in extra_drop
                    or c in keep_extra]

    print(f"[Features] Total feature columns: {len(feature_cols)}")
    obj_cols = [c for c in feature_cols if dataset[c].dtype == 'object']
    if obj_cols:
        print(f"[Features] Converting object columns to numeric: {obj_cols}")
        for c in obj_cols:
            dataset[c] = pd.to_numeric(dataset[c], errors='coerce')

    # 8. Save
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    dataset.to_parquet(output_path, index=False, engine='pyarrow')
    print(f"[Save] Dataset saved to {output_path} ({os.path.getsize(output_path)/1024/1024:.1f} MB)")

    # Save feature list for later use
    feature_list_path = output_path.replace(".parquet", "_features.txt")
    with open(feature_list_path, "w") as f:
        for col in feature_cols:
            f.write(f"{col}\n")
    print(f"[Save] Feature list saved to {feature_list_path}")

    # Summary stats
    for label_col in ["has_buy_signal", "has_sell_signal"]:
        print(f"\n[{label_col} distribution by split]:")
        for split_name in ["train", "val", "test"]:
            split_df = dataset[dataset["split"] == split_name]
            pos = split_df[label_col].sum()
            total = len(split_df)
            print(f"  {split_name}: {pos}/{total} ({pos/total*100:.2f}%)")

    engine_cy.dispose()
    engine_ts.dispose()


def main():
    parser = argparse.ArgumentParser(description="Build B/S training dataset")
    parser.add_argument("--output", default="data/processed/bs_training_dataset.parquet",
                        help="Output parquet file path")
    parser.add_argument("--lookback", type=int, default=60,
                        help="Lookback days for feature engineering")
    parser.add_argument("--train-end", default="20260430",
                        help="End date for training set (YYYYMMDD)")
    parser.add_argument("--val-end", default="20260531",
                        help="End date for validation set (YYYYMMDD)")
    args = parser.parse_args()

    build_dataset(
        output_path=args.output,
        lookback=args.lookback,
        train_end=args.train_end,
        val_end=args.val_end,
    )


if __name__ == "__main__":
    main()
