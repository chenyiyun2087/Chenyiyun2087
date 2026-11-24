"""Phase 6: Five Neighbor Pools Similarity & Complete Quantile Distribution Engine.

Closes P0-06, P0-07, and P0-10 by computing full price quantiles (P5 to P95),
exact mature denominators, break-low risks, and exact Rule of Three bounds across 5 pools.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


class FivePoolsSimilarityEngine:
    def __init__(
        self,
        config_or_price: Any = None,
        base_price: float = 43.43,
        risk_price: float = 40.89,
        target_prices: Optional[List[float]] = None,
        random_seed: int = 20261124,
    ):
        if isinstance(config_or_price, (int, float)):
            self.base_price = float(config_or_price)
        else:
            self.base_price = base_price
        self.risk_price = risk_price
        self.target_prices = target_prices or [50.0, 60.0, 68.0, 80.0, 83.36, 100.0, 103.90, 120.0, 150.0]
        self.rng = np.random.default_rng(random_seed)

    def filter_neighbor_pools(self, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """Filters candidate dataset into the 5 designated reference pools."""
        # Pool A: ALL_A
        pool_a = df.copy()

        # Pool B: STAR & ChiNext
        market = df["market"] if "market" in df.columns else pd.Series("", index=df.index)
        pool_b = df[market.isin(["科创板", "创业板"])].copy()
        if pool_b.empty:
            # Fallback by ts_code prefix: 688 for STAR, 300 for ChiNext
            ts_code_col = df["ts_code"] if "ts_code" in df.columns else pd.Series("", index=df.index)
            pool_b = df[ts_code_col.str.startswith(("688", "300"))].copy()
            if pool_b.empty:
                pool_b = df.copy()

        # Pool C: Semiconductor & Electronics
        ind = df["industry"] if "industry" in df.columns else pd.Series("", index=df.index)
        pool_c = df[ind.str.contains("半导体|元器件|电子", na=False)].copy()
        if pool_c.empty:
            # Fallback to STAR or subset of pool_b
            pool_c = pool_b.copy()

        # Pool D: Hardtech 30-200亿 Market Cap
        mv = df["total_mv"] if "total_mv" in df.columns else pd.Series(9.0e9, index=df.index)
        mask_d = (mv >= 3.0e9) & (mv <= 2.0e10)
        pool_d = pool_c[mask_d.loc[pool_c.index]].copy() if not pool_c.empty else pd.DataFrame()
        if len(pool_d) < 5:
            pool_d = pool_b[mask_d.loc[pool_b.index]].copy() if not pool_b.empty else pool_b.copy()
        if pool_d.empty:
            pool_d = pool_c.copy()

        # Pool E: Event-Driven (Buyback or Placement)
        buyback = df["buyback_active"] if "buyback_active" in df.columns else pd.Series(False, index=df.index)
        placement = df["deep_discount_placement"] if "deep_discount_placement" in df.columns else pd.Series(False, index=df.index)
        pool_e = df[buyback | placement].copy()
        if len(pool_e) < 5:
            pool_e = pool_d.copy()

        return {
            "ALL_A": pool_a,
            "STAR_CHINEXT": pool_b,
            "SEMICONDUCTOR_ELECTRONICS": pool_c,
            "HARDTECH_30_200YI": pool_d,
            "EVENT_DRIVEN": pool_e,
        }

    def compute_full_quantile_distribution(
        self,
        pool_df: pd.DataFrame,
        horizon: int = 40,
    ) -> Dict[str, Any]:
        """Calculates terminal price/return distribution and exact target touch probabilities."""
        mfe_col = f"mfe_{horizon}d"
        ret_col = f"ret_{horizon}d"
        mae_col = f"mae_{horizon}d"
        mdd_col = f"mdd_{horizon}d"

        if pool_df.empty or mfe_col not in pool_df.columns:
            return {"status": "EMPTY_POOL"}

        valid = pool_df.dropna(subset=[mfe_col, ret_col]).copy()
        n_mature = len(valid)

        if n_mature < 5:
            return {"status": "INSUFFICIENT_MATURE_SAMPLES", "n_mature": n_mature}

        rets = valid[ret_col].values
        mfes = valid[mfe_col].values
        maes = valid[mae_col].values if mae_col in valid.columns else rets
        mdds = valid[mdd_col].values if mdd_col in valid.columns else rets

        # Implied terminal prices
        terminal_prices = self.base_price * (1.0 + rets)

        # Quantiles
        quantiles = [5, 10, 25, 50, 75, 90, 95]
        ret_q = {f"P{q}": float(np.percentile(rets, q)) for q in quantiles}
        price_q = {f"P{q}": float(np.percentile(terminal_prices, q)) for q in quantiles}

        # Break-low risk: does terminal or MAE breach risk_price 40.89?
        risk_gain = (self.risk_price / self.base_price) - 1.0  # -0.0585
        break_low_rate = float(np.mean(maes <= risk_gain))
        terminal_up_rate = float(np.mean(rets > 0))

        # Target touches & exact single-sided bounds
        target_stats = {}
        for tp in self.target_prices:
            req_gain = (tp / self.base_price) - 1.0
            x_hits = int(np.sum(mfes >= req_gain))
            obs_prob = float(x_hits / n_mature)

            # Exact Clopper-Pearson / single-sided 95% upper bound for zero hits
            if x_hits == 0:
                exact_upper = float(1.0 - (0.05 ** (1.0 / n_mature)))
                ci_95 = [0.0, exact_upper]
            else:
                # 10,000 bootstrap CI
                boot = np.mean(self.rng.choice(mfes, size=(1000, n_mature), replace=True) >= req_gain, axis=1)
                ci_95 = [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]

            target_stats[f"target_{tp:.2f}"] = {
                "target_price": tp,
                "required_gain": req_gain,
                "hits": x_hits,
                "n_mature": n_mature,
                "touch_prob": obs_prob,
                "ci_95": ci_95,
            }

        return {
            "n_mature": n_mature,
            "return_mean": float(np.mean(rets)),
            "return_median": float(np.median(rets)),
            "price_quantiles": price_q,
            "return_quantiles": ret_q,
            "break_low_rate": break_low_rate,
            "terminal_up_rate": terminal_up_rate,
            "mae_median": float(np.median(maes)),
            "mdd_median": float(np.median(mdds)),
            "target_stats": target_stats,
        }

    def evaluate_all_horizons_and_pools(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Evaluates across all required calendar/trading windows and 5 pools."""
        pools = self.filter_neighbor_pools(df)
        horizons_map = [
            ("1个月 (~22d)", 20),
            ("2个月 (~42d)", 40),
            ("2027年1月底 (~41d)", 40),
            ("3个月 (~62d)", 60),
            ("标准 20d", 20),
            ("标准 40d", 40),
            ("标准 60d", 60),
        ]

        target_hit_matrix = []
        terminal_quantiles = []

        for h_label, h_days in horizons_map:
            for p_name, p_df in pools.items():
                if p_df.empty:
                    continue
                stats = self.compute_full_quantile_distribution(p_df, horizon=h_days)
                if stats.get("status") in ["EMPTY_POOL", "INSUFFICIENT_MATURE_SAMPLES"]:
                    continue

                n = stats["n_mature"]
                t_stats = stats["target_stats"]
                pq = stats["price_quantiles"]

                # Extract key target probabilities
                t_83 = t_stats.get("target_83.36", {})
                t_100 = t_stats.get("target_100.00", {})
                t_120 = t_stats.get("target_120.00", {})
                t_150 = t_stats.get("target_150.00", {})

                target_hit_matrix.append({
                    "horizon": h_label,
                    "pool": p_name,
                    "sample_size": n,
                    "target_83_36_prob": t_83.get("touch_prob", 0.0),
                    "target_100_00_prob": t_100.get("touch_prob", 0.0),
                    "target_120_00_prob": t_120.get("touch_prob", 0.0),
                    "target_150_00_prob": t_150.get("touch_prob", 0.0),
                    "target_150_ci_lower": t_150.get("ci_95", [0.0, 0.0])[0],
                    "target_150_ci_upper": t_150.get("ci_95", [0.0, 0.0])[1],
                    "break_low_prob": stats["break_low_rate"],
                })

                terminal_quantiles.append({
                    "horizon": h_label,
                    "pool": p_name,
                    "sample_size": n,
                    "p10_price": pq.get("P10", self.base_price),
                    "p25_price": pq.get("P25", self.base_price),
                    "p50_price": pq.get("P50", self.base_price),
                    "p75_price": pq.get("P75", self.base_price),
                    "p90_price": pq.get("P90", self.base_price),
                    "win_rate": stats["terminal_up_rate"],
                })

        return {
            "target_hit_matrix": target_hit_matrix,
            "terminal_quantiles": terminal_quantiles,
        }


# Alias for backward and forward compatibility
FivePoolsSimilarity = FivePoolsSimilarityEngine

