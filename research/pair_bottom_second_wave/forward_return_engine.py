"""Forward Return and Multi-Horizon Path Metrics Engine.

Computes forward returns, MFE, MAE, MDD, break-low rates, target hit indicators,
and censoring flags under realistic execution constraints (T+1 open entry, limits, fees).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


class ForwardReturnEngine:
    def __init__(
        self,
        horizons: List[int] = [20, 40, 60, 90, 120, 250],
        fees: float = 0.0010,
        slippage: float = 0.0010,
    ):
        self.horizons = sorted(horizons)
        self.fees = fees
        self.slippage = slippage

    def compute_forward_returns_for_stock(
        self,
        df: pd.DataFrame,
        signal_indices: List[int],
        target_prices: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Computes multi-horizon forward path metrics for each signal in df."""
        if not signal_indices:
            return []

        target_prices = target_prices or [50.0, 60.0, 68.0, 80.0, 83.36, 100.0, 120.0, 150.0]

        n = len(df)
        closes = df["close"].values
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        dates = df["trade_date"].values
        ts_code = df["ts_code"].iloc[0] if "ts_code" in df.columns else ""
        adj_factors = df["adj_factor"].values if "adj_factor" in df.columns else np.ones(n)

        # Market limit bounds (20% for STAR/ChiNext, 10% Main)
        is_star_chinext = ts_code.startswith("688") or ts_code.startswith("300") or ts_code.startswith("301")
        limit_ratio = 0.20 if is_star_chinext else 0.10

        records = []
        for idx in signal_indices:
            sig_date = int(dates[idx])
            raw_base_close = float(closes[idx])
            raw_base_low = float(lows[idx])

            # Next trading day entry check (T+1 open)
            if idx + 1 >= n:
                # Signal on very last day: fully censored
                rec = {
                    "ts_code": ts_code,
                    "trade_date": sig_date,
                    "signal_idx": idx,
                    "base_close": raw_base_close,
                    "base_low": raw_base_low,
                    "can_execute": False,
                    "exec_entry_price": np.nan,
                }
                for h in self.horizons:
                    rec[f"censored_{h}d"] = True
                    rec[f"ret_{h}d"] = np.nan
                    rec[f"mfe_{h}d"] = np.nan
                    rec[f"mae_{h}d"] = np.nan
                    rec[f"mdd_{h}d"] = np.nan
                    rec[f"break_low_{h}d"] = np.nan
                records.append(rec)
                continue

            entry_open = float(opens[idx + 1])
            pre_close = float(closes[idx])
            up_limit = pre_close * (1.0 + limit_ratio)

            # Check if one-word limit-up blocked entry
            can_execute = True
            if entry_open >= up_limit * 0.995 and highs[idx + 1] == lows[idx + 1]:
                can_execute = False

            exec_entry_price = entry_open * (1.0 + self.slippage) if can_execute else np.nan
            adj_entry = exec_entry_price * (adj_factors[idx + 1] / adj_factors[idx + 1])

            rec = {
                "ts_code": ts_code,
                "trade_date": sig_date,
                "signal_idx": idx,
                "base_close": raw_base_close,
                "base_low": raw_base_low,
                "can_execute": can_execute,
                "exec_entry_price": exec_entry_price,
            }

            for h in self.horizons:
                end_idx = min(idx + 1 + h, n)
                actual_days = end_idx - (idx + 1)

                if actual_days < 5:
                    # Too few days observed
                    rec[f"censored_{h}d"] = True
                    rec[f"ret_{h}d"] = np.nan
                    rec[f"mfe_{h}d"] = np.nan
                    rec[f"mae_{h}d"] = np.nan
                    rec[f"mdd_{h}d"] = np.nan
                    rec[f"break_low_{h}d"] = np.nan
                    for tp in target_prices:
                        rec[f"hit_{int(tp)}_{h}d"] = np.nan
                        rec[f"close_reached_{int(tp)}_{h}d"] = np.nan
                    continue

                is_censored = (actual_days < h)
                rec[f"censored_{h}d"] = is_censored

                # Forward window prices
                win_closes = closes[idx + 1 : end_idx]
                win_highs = highs[idx + 1 : end_idx]
                win_lows = lows[idx + 1 : end_idx]
                win_adjs = adj_factors[idx + 1 : end_idx]

                # Adjusted close return
                adj_exit_close = win_closes[-1] * (win_adjs[-1] / adj_factors[idx + 1])
                gross_ret = (adj_exit_close / exec_entry_price) - 1.0 if can_execute else np.nan
                net_ret = gross_ret - (self.fees * 2) if can_execute else np.nan

                # MFE: Maximum Favorable Excursion from entry
                adj_win_highs = win_highs * (win_adjs / adj_factors[idx + 1])
                max_high_adj = np.max(adj_win_highs)
                mfe = (max_high_adj / exec_entry_price) - 1.0 if can_execute else np.nan

                # MAE: Maximum Adverse Excursion from entry
                adj_win_lows = win_lows * (win_adjs / adj_factors[idx + 1])
                min_low_adj = np.min(adj_win_lows)
                mae = (min_low_adj / exec_entry_price) - 1.0 if can_execute else np.nan

                # MDD: Maximum Drawdown inside holding window
                cum_max = np.maximum.accumulate(adj_win_highs)
                drawdowns = (adj_win_lows - cum_max) / cum_max
                mdd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

                # Break low: did raw low breach raw base low?
                break_low = bool(np.any(win_lows < raw_base_low))

                rec[f"ret_{h}d"] = net_ret
                rec[f"mfe_{h}d"] = mfe
                rec[f"mae_{h}d"] = mae
                rec[f"mdd_{h}d"] = mdd
                rec[f"break_low_{h}d"] = break_low

                # Target price hits (unadjusted raw price touch and close reach)
                for tp in target_prices:
                    tp_key = f"{int(tp)}" if tp == int(tp) else f"{tp:.2f}".replace(".", "_")
                    hit_touch = bool(np.any(win_highs >= tp))
                    hit_close = bool(win_closes[-1] >= tp)
                    rec[f"hit_{tp_key}_{h}d"] = hit_touch
                    rec[f"close_reached_{tp_key}_{h}d"] = hit_close

            records.append(rec)

        return records
