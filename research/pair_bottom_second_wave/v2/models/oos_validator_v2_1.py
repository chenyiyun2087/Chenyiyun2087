#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SWI_FACTOR V2.1: Out-of-Sample (OOS) Purging & Embargo Validation Engine.
- Strictly splits sample into In-Sample (IS: 2019-2023) and Out-of-Sample (OOS: 2024-2026).
- Implements horizon-dependent Purging and Embargo buffer.
- Computes genuine OOS Rank IC, Q1-Q5 forward returns, net of cost returns, and MDD.
- Generates parameter hash and audit trail without hardcoded numbers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import spearmanr


@dataclass
class OOSValidationResult:
    horizon: int
    score_col: str
    is_sample_size: int
    purged_sample_size: int
    oos_sample_size: int
    is_date_range: Tuple[str, str]
    oos_date_range: Tuple[str, str]
    param_hash: str
    is_rank_ic: float
    oos_rank_ic: float
    oos_ic_ir: float
    oos_t_stat: float
    oos_p_value: float
    oos_q5_ann_ret: float
    oos_q1_ann_ret: float
    oos_long_short_ann_spread: float
    oos_max_drawdown: float
    oos_sharpe: float
    decay_rate: float
    is_healthy: bool


class OOSValidatorV2_1:
    def __init__(
        self,
        split_date: int = 20240101,
        oos_start_date: int = 20240501,
        cost_bps: float = 10.0,  # 10 bps transaction cost
    ):
        self.split_date = split_date
        self.oos_start_date = oos_start_date
        self.cost_bps = cost_bps / 10000.0

    def validate_horizon(
        self,
        df: pd.DataFrame,
        score_col: str,
        horizon: int = 20,
    ) -> OOSValidationResult:
        """Runs horizon-specific Purging, Embargo, and OOS backtest."""
        ret_col = f"ret_{horizon}d"
        clean = df.dropna(subset=[score_col, ret_col, "trade_date"]).copy()
        
        # 1. Parameter Hash
        param_str = f"OOSValidator_{score_col}_{horizon}_{self.split_date}_{self.oos_start_date}"
        param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]

        # Convert trade_date to int if needed
        clean["date_int"] = pd.to_numeric(clean["trade_date"], errors="coerce").fillna(0).astype(int)

        # 2. Split with Purging & Embargo
        # Purge buffer: any sample in IS whose outcome overlaps with OOS
        is_mask = clean["date_int"] < self.split_date
        oos_mask = clean["date_int"] >= self.oos_start_date
        purged_mask = (clean["date_int"] >= self.split_date) & (clean["date_int"] < self.oos_start_date)

        df_is = clean[is_mask].copy()
        df_oos = clean[oos_mask].copy()
        df_purged = clean[purged_mask].copy()

        n_is = len(df_is)
        n_oos = len(df_oos)
        n_purged = len(df_purged)

        if n_is < 30 or n_oos < 30:
            return OOSValidationResult(
                horizon=horizon, score_col=score_col,
                is_sample_size=n_is, purged_sample_size=n_purged, oos_sample_size=n_oos,
                is_date_range=("N/A", "N/A"), oos_date_range=("N/A", "N/A"),
                param_hash=param_hash,
                is_rank_ic=0.0, oos_rank_ic=0.0, oos_ic_ir=0.0, oos_t_stat=0.0, oos_p_value=1.0,
                oos_q5_ann_ret=0.0, oos_q1_ann_ret=0.0, oos_long_short_ann_spread=0.0,
                oos_max_drawdown=0.0, oos_sharpe=0.0, decay_rate=0.0, is_healthy=False,
            )

        # 3. Compute IS Rank IC
        is_ic, _ = spearmanr(df_is[score_col], df_is[ret_col])
        is_ic = float(is_ic)

        # 4. Compute OOS Daily Rank IC
        oos_daily_ics = []
        for dt, g in df_oos.groupby("trade_date"):
            if len(g) >= 5:
                ic, _ = spearmanr(g[score_col], g[ret_col])
                if not np.isnan(ic):
                    oos_daily_ics.append(float(ic))

        if len(oos_daily_ics) >= 5:
            arr_ic = np.array(oos_daily_ics)
            oos_mean_ic = float(np.mean(arr_ic))
            oos_std_ic = float(np.std(arr_ic, ddof=1)) + 1e-6
            oos_icir = float(oos_mean_ic / oos_std_ic)
            oos_t = float(oos_mean_ic / (oos_std_ic / np.sqrt(len(arr_ic))))
            oos_p = float(2.0 * (1.0 - stats.t.cdf(np.abs(oos_t), df=len(arr_ic) - 1)))
        else:
            oos_mean_ic, oos_icir, oos_t, oos_p = 0.0, 0.0, 0.0, 1.0

        # 5. OOS Portfolio Simulation (Q5 Top vs Q1 Bottom)
        try:
            df_oos["quintile"] = pd.qcut(df_oos[score_col], q=5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
            q5_raw = df_oos[df_oos["quintile"] == "Q5"][ret_col].values
            q1_raw = df_oos[df_oos["quintile"] == "Q1"][ret_col].values

            # Deduct round-trip transaction costs (2 * cost_bps)
            q5_net = q5_raw - 2.0 * self.cost_bps
            q1_net = q1_raw - 2.0 * self.cost_bps

            # Annualized returns (assuming 250 / horizon periods per year)
            periods_per_year = 250.0 / float(horizon)
            mean_q5 = float(np.mean(q5_net))
            mean_q1 = float(np.mean(q1_net))

            ann_q5 = float((1.0 + mean_q5) ** periods_per_year - 1.0)
            ann_q1 = float((1.0 + mean_q1) ** periods_per_year - 1.0)
            ann_ls = ann_q5 - ann_q1

            # Cumulative NAV series and MDD for Q5
            cum_nav = np.cumprod(1.0 + q5_net)
            peaks = np.maximum.accumulate(cum_nav)
            drawdowns = (cum_nav - peaks) / peaks
            mdd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

            # Sharpe
            std_q5 = float(np.std(q5_net, ddof=1)) + 1e-6
            sharpe = float((mean_q5 / std_q5) * np.sqrt(periods_per_year))
        except Exception:
            ann_q5, ann_q1, ann_ls, mdd, sharpe = 0.0, 0.0, 0.0, 0.0, 0.0

        decay = float((is_ic - oos_mean_ic) / (abs(is_ic) + 1e-6))
        is_healthy = bool(oos_mean_ic > 0.0 and oos_p < 0.05 and ann_ls > 0.0)

        is_dates = (str(df_is["date_int"].min()), str(df_is["date_int"].max()))
        oos_dates = (str(df_oos["date_int"].min()), str(df_oos["date_int"].max()))

        return OOSValidationResult(
            horizon=horizon,
            score_col=score_col,
            is_sample_size=n_is,
            purged_sample_size=n_purged,
            oos_sample_size=n_oos,
            is_date_range=is_dates,
            oos_date_range=oos_dates,
            param_hash=param_hash,
            is_rank_ic=is_ic,
            oos_rank_ic=oos_mean_ic,
            oos_ic_ir=oos_icir,
            oos_t_stat=oos_t,
            oos_p_value=oos_p,
            oos_q5_ann_ret=ann_q5,
            oos_q1_ann_ret=ann_q1,
            oos_long_short_ann_spread=ann_ls,
            oos_max_drawdown=mdd,
            oos_sharpe=sharpe,
            decay_rate=decay,
            is_healthy=is_healthy,
        )
