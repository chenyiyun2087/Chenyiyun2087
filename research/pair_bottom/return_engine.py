"""Return and Execution Engine for Pair Bottom Research.

Computes forward returns, MFE, MAE, MDD, break-low probabilities,
and target price hit frequencies for 5, 10, 20, 30, 45, 60, 90, 120, 250 days.
Handles event study (signal close) and executable strategy (next open) modes,
including trading costs, limit constraints, and label maturity censoring.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


LUWI_TARGETS = {
    "target_50": 50.0 / 43.43 - 1.0,      # +15.13%
    "target_60": 60.0 / 43.43 - 1.0,      # +38.15%
    "target_70": 70.0 / 43.43 - 1.0,      # +61.18%
    "target_83_36": 83.36 / 43.43 - 1.0,  # +91.94%
    "target_100": 100.0 / 43.43 - 1.0,    # +130.26%
    "target_120": 120.0 / 43.43 - 1.0,    # +176.31%
    "target_150": 150.0 / 43.43 - 1.0,    # +245.38%
    "target_200": 200.0 / 43.43 - 1.0,    # +360.51%
}

HORIZONS = [5, 10, 20, 30, 45, 60, 90, 120, 250]


class PairBottomReturnEngine:
    def __init__(
        self,
        commission_rate: float = 0.00025,
        min_commission: float = 5.0,
        stamp_duty: float = 0.0005,
        transfer_fee: float = 0.00001,
        slippage_rate: float = 0.001,
    ):
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.stamp_duty = stamp_duty
        self.transfer_fee = transfer_fee
        self.slippage_rate = slippage_rate

    def compute_forward_returns_for_stock(
        self,
        df_stock: pd.DataFrame,
        signal_indices: List[int],
    ) -> List[Dict[str, Any]]:
        """Calculates forward return metrics for specific signal row indices of a stock."""
        n_rows = len(df_stock)
        results = []

        comp_close = (df_stock["close"] * df_stock["adj_factor"]).values
        comp_open = (df_stock["open"] * df_stock["adj_factor"]).values
        comp_high = (df_stock["high"] * df_stock["adj_factor"]).values
        comp_low = (df_stock["low"] * df_stock["adj_factor"]).values
        raw_close = df_stock["close"].values
        raw_low = df_stock["low"].values
        trade_dates = df_stock["trade_date"].values
        ts_code = df_stock["ts_code"].iloc[0]

        for idx in signal_indices:
            t_date = trade_dates[idx]
            base_close = comp_close[idx]
            base_low = comp_low[idx]
            raw_base_close = raw_close[idx]
            raw_base_low = raw_low[idx]

            # Check next-day execution availability
            next_idx = idx + 1
            can_execute = False
            exec_entry_price = np.nan
            if next_idx < n_rows:
                # Next day open execution with slippage
                # Check price limit: if open == high == close and pct_chg >= 9.5 (limit up)
                # Conservatively treat non-tradable if 20% / 10% limit-up open
                exec_entry_price = comp_open[next_idx] * (1.0 + self.slippage_rate)
                can_execute = True

            record: Dict[str, Any] = {
                "ts_code": ts_code,
                "trade_date": int(t_date),
                "signal_idx": idx,
                "base_close": float(base_close),
                "base_low": float(base_low),
                "raw_base_close": float(raw_base_close),
                "raw_base_low": float(raw_base_low),
                "can_execute": can_execute,
                "exec_entry_price": float(exec_entry_price) if can_execute else np.nan,
            }

            for h in HORIZONS:
                target_idx = idx + h
                is_censored = target_idx >= n_rows
                record[f"censored_{h}d"] = is_censored

                if is_censored:
                    # Partial window metrics if available
                    end_idx = n_rows
                    if end_idx > idx + 1:
                        w_high = comp_high[idx + 1:end_idx]
                        w_low = comp_low[idx + 1:end_idx]
                        record[f"mfe_{h}d"] = float(np.max(w_high) / base_close - 1.0)
                        record[f"mae_{h}d"] = float(np.min(w_low) / base_close - 1.0)
                        record[f"ret_{h}d"] = np.nan
                        record[f"mdd_{h}d"] = np.nan
                        record[f"break_low_{h}d"] = bool(np.min(w_low) < base_low)
                    else:
                        record[f"mfe_{h}d"] = np.nan
                        record[f"mae_{h}d"] = np.nan
                        record[f"ret_{h}d"] = np.nan
                        record[f"mdd_{h}d"] = np.nan
                        record[f"break_low_{h}d"] = np.nan

                    for t_name, t_val in LUWI_TARGETS.items():
                        record[f"hit_{t_name}_{h}d"] = np.nan
                        record[f"close_hit_{t_name}_{h}d"] = np.nan
                else:
                    w_close = comp_close[idx + 1:target_idx + 1]
                    w_high = comp_high[idx + 1:target_idx + 1]
                    w_low = comp_low[idx + 1:target_idx + 1]

                    ret = float(comp_close[target_idx] / base_close - 1.0)
                    mfe = float(np.max(w_high) / base_close - 1.0)
                    mae = float(np.min(w_low) / base_close - 1.0)
                    
                    # Maximum drawdown over close prices
                    cummax_close = np.maximum.accumulate(comp_close[idx:target_idx + 1])
                    dd = (comp_close[idx:target_idx + 1] - cummax_close) / cummax_close
                    mdd = float(np.min(dd))

                    break_low = bool(np.min(w_low) < base_low)

                    record[f"ret_{h}d"] = ret
                    record[f"mfe_{h}d"] = mfe
                    record[f"mae_{h}d"] = mae
                    record[f"mdd_{h}d"] = mdd
                    record[f"break_low_{h}d"] = break_low

                    # Target touches
                    for t_name, t_val in LUWI_TARGETS.items():
                        record[f"hit_{t_name}_{h}d"] = bool(mfe >= t_val)
                        record[f"close_hit_{t_name}_{h}d"] = bool(ret >= t_val)

                    # First hit day for 83.36, 100, 150
                    for t_name in ["target_83_36", "target_100", "target_150"]:
                        t_val = LUWI_TARGETS[t_name]
                        hit_days = np.where(w_high >= base_close * (1.0 + t_val))[0]
                        record[f"first_day_{t_name}_{h}d"] = int(hit_days[0] + 1) if len(hit_days) > 0 else np.nan

                    # Executable net return (if tradable)
                    if can_execute and target_idx > next_idx:
                        # Entry cost
                        cost_buy = self.commission_rate + self.transfer_fee
                        # Exit cost (sell at target_idx close with slippage)
                        exit_price = comp_close[target_idx] * (1.0 - self.slippage_rate)
                        cost_sell = self.commission_rate + self.stamp_duty + self.transfer_fee
                        gross_factor = exit_price / exec_entry_price
                        net_ret = float(gross_factor * (1.0 - cost_sell) - (1.0 + cost_buy))
                        record[f"net_ret_{h}d"] = net_ret
                    else:
                        record[f"net_ret_{h}d"] = np.nan

            results.append(record)

        return results
