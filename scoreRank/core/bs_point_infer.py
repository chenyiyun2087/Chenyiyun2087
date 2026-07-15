#!/usr/bin/env python3
"""
B/S Point Inference Engine.

Loads trained models and generates B/S signal predictions from database features.
Produces output compatible with bs_detection_results table.

Usage:
    from scoreRank.core.bs_point_infer import BSPointInferrer
    inferrer = BSPointInferrer("exports/bs_point_models/latest")
    predictions = inferrer.predict("20260624")
"""

import json
import os

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from scoreRank.core.db_config import require_sqlalchemy_url

# QFQ (前复权) factor columns needed for inference
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

DWS_TABLES = {
    "dws_tech_pattern": ["hma_slope", "rsi_14", "boll_width"],
    "dws_capital_flow": ["main_net_ratio", "main_net_ma5", "vol_price_corr"],
    "dws_chip_dynamics": ["profit_ratio", "profit_pressure", "support_strength"],
    "dws_liquidity_factor": ["turnover_vol_20", "amihud_20"],
}

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


class BSPointInferrer:
    """Loads B/S prediction models and runs inference on database features."""

    def __init__(self, model_dir: str):
        """
        Args:
            model_dir: Path to model directory containing:
                - buy_model.joblib
                - sell_model.joblib
                - buy_threshold.txt
                - sell_threshold.txt
                - scaler.joblib
                - feature_names.json
        """
        self.model_dir = model_dir
        self.engine = None

        # Load models
        buy_path = os.path.join(model_dir, "buy_model.joblib")
        sell_path = os.path.join(model_dir, "sell_model.joblib")
        scaler_path = os.path.join(model_dir, "scaler.joblib")
        feature_path = os.path.join(model_dir, "feature_names.json")

        if not os.path.exists(buy_path):
            raise FileNotFoundError(f"Buy model not found: {buy_path}")
        if not os.path.exists(sell_path):
            raise FileNotFoundError(f"Sell model not found: {sell_path}")
        if not os.path.exists(feature_path):
            raise FileNotFoundError(f"Feature names not found: {feature_path}")

        self.buy_model = joblib.load(buy_path)
        self.sell_model = joblib.load(sell_path)

        with open(feature_path) as f:
            self.feature_names = json.load(f)

        # Load scaler (required for LR models, optional for tree-based)
        if os.path.exists(scaler_path):
            self.scaler = joblib.load(scaler_path)
            self.use_scaler = True
        else:
            self.scaler = None
            self.use_scaler = False

        # Load thresholds
        self.buy_threshold = self._load_threshold("buy_threshold.txt", 0.5)
        self.sell_threshold = self._load_threshold("sell_threshold.txt", 0.5)

        print(f"[BSPointInferrer] Loaded from {model_dir}")
        print(f"  Buy model: {self.buy_model.__class__.__name__}, threshold={self.buy_threshold:.4f}")
        print(f"  Sell model: {self.sell_model.__class__.__name__}, threshold={self.sell_threshold:.4f}")
        print(f"  Features: {len(self.feature_names)}")

    def _load_threshold(self, filename: str, default: float = 0.5) -> float:
        path = os.path.join(self.model_dir, filename)
        if os.path.exists(path):
            with open(path) as f:
                return float(f.read().strip())
        return default

    def _get_engine(self):
        if self.engine is None:
            self.engine = create_engine(
                require_sqlalchemy_url(database="tushare_stock")
            )
        return self.engine

    def _stock_code_to_ts_code(self, code: str) -> str:
        """Convert 6-digit code to ts_code."""
        code = str(code).zfill(6)
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        elif code.startswith(("6", "9")):
            return f"{code}.SH"
        elif code.startswith(("4", "8")):
            return f"{code}.BJ"
        return f"{code}.SZ"

    def load_features(self, trade_date: str, stock_codes: list = None,
                       lookback_days: int = 30) -> pd.DataFrame:
        """
        Load and engineer features for given trade_date.

        Args:
            trade_date: YYYYMMDD format
            stock_codes: Optional list of 6-digit stock codes. If None, loads all A-shares.
            lookback_days: Days of history to load for computing rolling features

        Returns:
            DataFrame with ts_code, stock_code, and all feature columns
        """
        engine = self._get_engine()
        date_int = int(trade_date)

        # Get stock universe
        if stock_codes:
            ts_codes = [self._stock_code_to_ts_code(c) for c in stock_codes]
        else:
            # All listed A-shares
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT DISTINCT ts_code FROM ods_stk_factor
                    WHERE trade_date = :date
                """), {"date": date_int})
                ts_codes = [row[0] for row in result.fetchall()]

        print(f"[Features] Loading data for {len(ts_codes)} stocks on {trade_date}")

        # Get date range for lookback
        with engine.connect() as conn:
            dates_result = conn.execute(text("""
                SELECT DISTINCT trade_date FROM ods_stk_factor
                WHERE trade_date <= :date AND ts_code IN (SELECT DISTINCT ts_code FROM ods_stk_factor WHERE trade_date = :date2)
                ORDER BY trade_date DESC
                LIMIT :lookback
            """), {"date": date_int, "date2": date_int, "lookback": lookback_days + 5})
            lookback_dates = sorted([row[0] for row in dates_result.fetchall()])
        print(f"[Features] Lookback window: {len(lookback_dates)} dates, {lookback_dates[0]} ~ {lookback_dates[-1]}")

        # Load ods_stk_factor for target date + lookback
        cols_str = ", ".join(["ts_code", "trade_date"] + list(ALL_FACTOR_COLS))
        ts_codes_str = ",".join(f"'{c}'" for c in ts_codes)
        dates_str = ",".join(str(d) for d in lookback_dates)

        sql = f"""
        SELECT {cols_str}
        FROM ods_stk_factor
        WHERE trade_date IN ({dates_str}) AND ts_code IN ({ts_codes_str})
        ORDER BY ts_code, trade_date
        """
        df = pd.read_sql(text(sql), engine)

        if df.empty:
            print("[Features] WARNING: No factor data loaded!")
            return pd.DataFrame()

        print(f"[Features] Loaded {len(df)} rows from ods_stk_factor")

        # Load DWS tables (for target date only)
        dws_dfs = {}
        for table_name, dws_cols in DWS_TABLES.items():
            dws_cols_str = ", ".join(["ts_code", "trade_date"] + dws_cols)
            try:
                dws_df = pd.read_sql(text(f"""
                    SELECT {dws_cols_str}
                    FROM {table_name}
                    WHERE trade_date = :date AND ts_code IN ({ts_codes_str})
                """), engine, params={"date": date_int})
                dws_dfs[table_name] = dws_df
            except Exception as e:
                print(f"[Features] Skipped {table_name}: {e}")

        # Load ADC tables (v5)
        adc_dfs = {}
        for table_name, adc_cols in ADC_TABLES.items():
            adc_cols_str = ", ".join(["ts_code", "trade_date"] + adc_cols)
            try:
                adc_df = pd.read_sql(text(f"""
                    SELECT {adc_cols_str}
                    FROM {table_name}
                    WHERE trade_date = :date AND ts_code IN ({ts_codes_str})
                """), engine, params={"date": date_int})
                adc_dfs[table_name] = adc_df
            except Exception as e:
                pass  # ADC tables may not exist on all dates

        # Engineer features (uses multi-day data for rolling features)
        featured = self._engineer_features(df, dws_dfs, adc_dfs, target_date=date_int)

        # Add stock_code column (6-digit)
        def ts_to_stock_code(ts):
            return ts.split(".")[0]

        featured["stock_code"] = featured["ts_code"].apply(ts_to_stock_code)

        return featured

    def _engineer_features(self, factor_df: pd.DataFrame, dws_dfs: dict,
                            adc_dfs: dict = None, target_date: int = None) -> pd.DataFrame:
        """Engineer derived features (same logic as training pipeline).
        When multi-day data is provided, computes proper rolling/cross features.
        """
        df = factor_df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # Price-derived
        df["ret_1d"] = df["close_qfq"] / df["pre_close_qfq"] - 1.0

        # Rolling returns (grouped by stock) - proper computation with lookback
        for n in [2, 3, 5, 10, 20]:
            df[f"ret_{n}d"] = df.groupby("ts_code")["close_qfq"].transform(
                lambda x: x.pct_change(n)
            )

        # Short-term acceleration features (v2 — temporal dynamics)
        df["ret_1d_prev"] = df.groupby("ts_code")["ret_1d"].shift(1)
        df["ret_1d_accel"] = df["ret_1d"] - df["ret_1d_prev"]
        df["vol_ratio_prev"] = df.groupby("ts_code")["volume_ratio"].shift(1)
        df["vol_ratio_accel"] = df["volume_ratio"] - df["vol_ratio_prev"]

        # --- Trend freshness features (v3): 5-day change in key indicators ---
        FW = 5
        for col in ["close_vs_ma20", "close_vs_ma60", "rsi_qfq_6", "kdj_k_qfq", "macd_qfq"]:
            if col in df.columns:
                df[f"{col}_{FW}d_ago"] = df.groupby("ts_code")[col].shift(FW)
                df[f"{col}_chg_{FW}d"] = df[col] - df[f"{col}_{FW}d_ago"]
        if "boll_position" in df.columns:
            df["boll_position_5d_ago"] = df.groupby("ts_code")["boll_position"].shift(5)
            df["boll_position_chg_5d"] = df["boll_position"] - df["boll_position_5d_ago"]
        if "ret_1d" in df.columns and "ret_5d" in df.columns:
            df["ret_5d_minus_ret_1d"] = df["ret_5d"] - df["ret_1d"]
            df["ret_5d_ago"] = df.groupby("ts_code")["ret_5d"].shift(5)
            df["ret_5d_accel"] = df["ret_5d"] - df["ret_5d_ago"]

        # Days since crossing above MA20
        if "ma_qfq_20" in df.columns:
            df["above_ma20"] = (df["close_qfq"] > df["ma_qfq_20"]).astype(int)
            df["days_above_ma20"] = df.groupby("ts_code")["above_ma20"].transform(
                lambda x: x.groupby((x == 0).cumsum()).cumsum()
            ).clip(0, 60)
            prev_close = df.groupby("ts_code")["close_qfq"].shift(1)
            prev_ma20 = df.groupby("ts_code")["ma_qfq_20"].shift(1)
            was_below = prev_close <= prev_ma20
            is_above = df["close_qfq"] > df["ma_qfq_20"]
            df["just_crossed_above_ma20"] = (was_below & is_above).astype(int)

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
            df["macd_hist_prev"] = df.groupby("ts_code")["macd_qfq"].shift(1)
            df["macd_hist_chg"] = df["macd_qfq"] - df["macd_hist_prev"]
            df["macd_hist_zero_cross_up"] = (
                (df["macd_hist_prev"] <= 0) & (df["macd_qfq"] > 0)
            ).astype(int)
            df["macd_hist_zero_cross_down"] = (
                (df["macd_hist_prev"] >= 0) & (df["macd_qfq"] < 0)
            ).astype(int)
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

        # RSI
        if "rsi_qfq_6" in df.columns:
            df["rsi6_oversold"] = (df["rsi_qfq_6"] < 25).astype(int)
            df["rsi6_overbought"] = (df["rsi_qfq_6"] > 75).astype(int)
        if "rsi_qfq_12" in df.columns:
            df["rsi12_oversold"] = (df["rsi_qfq_12"] < 30).astype(int)

        # BOLL
        if all(c in df.columns for c in ["boll_upper_qfq", "boll_lower_qfq", "boll_mid_qfq"]):
            boll_range = df["boll_upper_qfq"] - df["boll_lower_qfq"]
            df["boll_position"] = np.where(
                boll_range > 0,
                (df["close_qfq"] - df["boll_lower_qfq"]) / boll_range,
                0.5
            )
            df["boll_lower_touch"] = (df["low_qfq"] <= df["boll_lower_qfq"]).astype(int)
            df["boll_upper_touch"] = (df["high_qfq"] >= df["boll_upper_qfq"]).astype(int)

        # Volume
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

        # 20-day drawdown
        df["high_20d_max"] = df.groupby("ts_code")["high_qfq"].transform(
            lambda x: x.rolling(20, min_periods=1).max()
        )
        df["mdd_20d"] = df["close_qfq"] / (df["high_20d_max"] + 1e-10) - 1.0

        # CCI
        if "cci_qfq" in df.columns:
            df["cci_oversold"] = (df["cci_qfq"] < -150).astype(int)
            df["cci_overbought"] = (df["cci_qfq"] > 150).astype(int)

        # --- v4: High-discrimination features ---
        for n in [5, 10, 20]:
            roll_max = df.groupby("ts_code")["high_qfq"].transform(lambda x: x.rolling(n, min_periods=1).max())
            df[f"gap_from_{n}d_high"] = df["close_qfq"] / (roll_max + 1e-10) - 1.0
        df["pre_close_qfq"] = df["pre_close_qfq"].fillna(df["close_qfq"])
        df["is_up_day"] = (df["close_qfq"] > df["pre_close_qfq"]).astype(int)
        df["up_streak"] = df.groupby("ts_code")["is_up_day"].transform(lambda x: x.groupby((x == 0).cumsum()).cumsum())
        df["down_streak"] = df.groupby("ts_code")["is_up_day"].transform(lambda x: (1-x).groupby((x == 1).cumsum()).cumsum())
        df["vol_ma20"] = df.groupby("ts_code")["vol"].transform(lambda x: x.rolling(20, min_periods=5).mean())
        df["vol_vs_ma20"] = df["vol"] / (df["vol_ma20"] + 1e-10)
        day_range = df["high_qfq"] - df["low_qfq"]
        df["close_position_in_day"] = np.where(day_range > 0, (df["close_qfq"] - df["low_qfq"]) / day_range, 0.5)
        rsi10_low = df.groupby("ts_code")["rsi_qfq_6"].transform(lambda x: x.rolling(10, min_periods=3).min())
        price10_low = df.groupby("ts_code")["low_qfq"].transform(lambda x: x.rolling(10, min_periods=3).min())
        df["rsi_bull_divergence"] = ((df["low_qfq"] <= price10_low * 1.02) & (df["rsi_qfq_6"] > rsi10_low * 1.1)).astype(int)
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
            df = df.merge(dws_df[merge_cols + overlap], on=merge_cols, how="left")

        # Fill NaN for DWS columns
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

        # Filter to target date if specified
        if target_date is not None:
            df = df[df["trade_date"] == target_date]

        return df

    def predict(self, trade_date: str, stock_codes: list = None) -> pd.DataFrame:
        """
        Run inference for given trade_date.

        Returns DataFrame with columns:
            stock_code, ts_code, buy_prob, sell_prob,
            has_buy_signal, has_sell_signal,
            buy_signal_description, sell_signal_description,
            total_b_points, total_s_points, buy_points_count, sell_points_count
        """
        df = self.load_features(trade_date, stock_codes)
        if df.empty:
            return pd.DataFrame()

        # Models can reference optional DWS/ADC columns which are absent when
        # an upstream table is unavailable. Add them before selecting the
        # trained feature set; selecting first raises KeyError and prevents the
        # intended zero-fill fallback from ever running.
        for col in self.feature_names:
            if col not in df.columns:
                df[col] = 0.0

        # Ensure column order matches training
        X = df[self.feature_names].copy()

        # Fill NaN
        X = X.fillna(0).replace([np.inf, -np.inf], 0)

        # Convert to numeric
        for col in X.columns:
            if X[col].dtype == 'object':
                X[col] = pd.to_numeric(X[col], errors='coerce').fillna(0)

        X_arr = X.values.astype(np.float64)

        # Scale if needed
        if self.use_scaler:
            X_arr = self.scaler.transform(X_arr)

        # Predict
        buy_probs = self.buy_model.predict_proba(X_arr)[:, 1]
        sell_probs = self.sell_model.predict_proba(X_arr)[:, 1]

        # Apply thresholds
        # Strategy depends on pool size:
        #   Full market (>1000 stocks): top ~2% (p98)
        #   Self-selected pool (<1000 stocks): match OCR rate ~3% (p97), min 10 signals
        if len(buy_probs) > 0:
            n_stocks = len(buy_probs)
            if n_stocks <= 1000:
                # Self-selected pool: match OCR signal rate (~3%), at least 10 signals
                target_n = max(10, int(n_stocks * 0.035))
                buy_threshold_actual = float(np.sort(buy_probs)[-target_n]) if target_n < n_stocks else 0.0
                sell_target_n = max(8, int(n_stocks * 0.03))
                sell_threshold_actual = float(np.sort(sell_probs)[-sell_target_n]) if sell_target_n < n_stocks else 0.0
                print(f"[Predict] Self-selected mode: buy_thresh={buy_threshold_actual:.4f} (top {target_n}), "
                      f"sell_thresh={sell_threshold_actual:.4f} (top {sell_target_n})")
            else:
                # Full market: top 2-3%
                buy_threshold_actual = max(np.percentile(buy_probs, 98), self.buy_threshold * 0.3)
                sell_threshold_actual = max(np.percentile(sell_probs, 97), self.sell_threshold * 0.3)
        else:
            buy_threshold_actual = self.buy_threshold
            sell_threshold_actual = self.sell_threshold

        has_buy = (buy_probs >= buy_threshold_actual).astype(int)
        has_sell = (sell_probs >= sell_threshold_actual).astype(int)

        # Build result
        result = df[["stock_code", "ts_code"]].copy()
        result["buy_prob"] = buy_probs
        result["sell_prob"] = sell_probs
        result["has_buy_signal"] = has_buy
        result["has_sell_signal"] = has_sell

        # Descriptions
        result["buy_signal_description"] = result.apply(
            lambda r: f"ML模型预测买点(prob={r['buy_prob']:.2f})" if r["has_buy_signal"] == 1
            else "当天无买点", axis=1
        )
        result["sell_signal_description"] = result.apply(
            lambda r: f"ML模型预测卖点(prob={r['sell_prob']:.2f})" if r["has_sell_signal"] == 1
            else "当天无卖点", axis=1
        )

        # Point counts: store raw probability * 100 for precision (0-100 scale)
        result["total_b_points"] = (buy_probs * 100).round(1).clip(0, 100)
        result["total_s_points"] = (sell_probs * 100).round(1).clip(0, 100)
        result["buy_points_count"] = (buy_probs * 100).round(1).clip(0, 100)
        result["sell_points_count"] = (sell_probs * 100).round(1).clip(0, 100)

        # Stats
        n_buy = result["has_buy_signal"].sum()
        n_sell = result["has_sell_signal"].sum()
        print(f"[Predict] {len(result)} stocks → {n_buy} buy signals, {n_sell} sell signals "
              f"(buy_rate={n_buy/len(result)*100:.1f}%)")

        return result

    def save_to_db(self, predictions: pd.DataFrame, batch_name: str, batch_date: str,
                   chenyiyun_url: str = None):
        """Write predictions to chenyiyun.bs_detection_results."""
        if chenyiyun_url is None:
            chenyiyun_url = require_sqlalchemy_url(database="chenyiyun")

        engine = create_engine(chenyiyun_url)

        now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for _, r in predictions.iterrows():
            rows.append({
                "batch_name": batch_name,
                "batch_date": batch_date,
                "stock_code": r["stock_code"],
                "has_buy_signal": int(r["has_buy_signal"]),
                "has_sell_signal": int(r["has_sell_signal"]),
                "buy_signal_description": str(r["buy_signal_description"]),
                "sell_signal_description": str(r["sell_signal_description"]),
                "total_b_points": int(r["total_b_points"]),
                "total_s_points": int(r["total_s_points"]),
                "buy_points_count": int(r["buy_points_count"]),
                "sell_points_count": int(r["sell_points_count"]),
                "process_time": now,
                "image_path": None,
                "created_at": now,
            })

        if not rows:
            print("[Save] No predictions to save.")
            return

        insert_sql = """
        INSERT INTO bs_detection_results
            (batch_name, batch_date, stock_code, has_buy_signal, has_sell_signal,
             buy_signal_description, sell_signal_description,
             total_b_points, total_s_points, buy_points_count, sell_points_count,
             process_time, image_path, created_at)
        VALUES
            (:batch_name, :batch_date, :stock_code, :has_buy_signal, :has_sell_signal,
             :buy_signal_description, :sell_signal_description,
             :total_b_points, :total_s_points, :buy_points_count, :sell_points_count,
             :process_time, :image_path, :created_at)
        ON DUPLICATE KEY UPDATE
            has_buy_signal = VALUES(has_buy_signal),
            has_sell_signal = VALUES(has_sell_signal),
            buy_signal_description = VALUES(buy_signal_description),
            sell_signal_description = VALUES(sell_signal_description),
            total_b_points = VALUES(total_b_points),
            total_s_points = VALUES(total_s_points),
            buy_points_count = VALUES(buy_points_count),
            sell_points_count = VALUES(sell_points_count),
            process_time = VALUES(process_time),
            image_path = VALUES(image_path),
            created_at = VALUES(created_at)
        """

        with engine.begin() as conn:
            conn.execute(text(insert_sql), rows)

        print(f"[Save] Wrote {len(rows)} records to bs_detection_results "
              f"(batch={batch_name}, date={batch_date})")
        engine.dispose()
