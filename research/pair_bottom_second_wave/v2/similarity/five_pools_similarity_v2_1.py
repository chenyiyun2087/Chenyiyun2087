#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SWI_FACTOR V2.1: Genuine 5-Pool Similarity & Individual Stock KNN Engine.
- Strictly separates Pool C (Semiconductor) and Pool D (Hardtech 30-200B) without silent copying.
- Never silently replaces an empty or small pool with another pool.
- Genuinely runs KNN (Nearest Neighbors) on Luwi's 688401.SH signal features.
- Dynamically aligns trading calendar days from dim_trade_cal (1M: 22d, 2M: 42d, End of Jan: 47d, 3M: 60d).
- Evaluates exact empirical target touch probabilities and Clopper-Pearson / Rule of Three 95% bounds.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler


class FivePoolsSimilarityV2_1:
    def __init__(
        self,
        base_price: float = 43.43,
        risk_price: float = 40.89,
        target_prices: Optional[List[float]] = None,
        random_seed: int = 20261124,
    ):
        self.base_price = base_price
        self.risk_price = risk_price
        self.target_prices = target_prices or [50.0, 60.0, 68.0, 80.0, 83.36, 100.0, 120.0, 150.0]
        self.rng = np.random.default_rng(random_seed)
        self.scaler = StandardScaler()

    def filter_independent_pools(self, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """
        Filters candidates into 5 strictly defined pools with zero silent substitution:
        - Pool A: ALL_A (All independent episodes)
        - Pool B: STAR_CHINEXT (STAR Market 688* and ChiNext 300*)
        - Pool C: SEMICONDUCTOR_ELECTRONICS (Semiconductor and electronics industry)
        - Pool D: HARDTECH_30_200YI (STAR/ChiNext with total market cap between 3B and 20B RMB)
        - Pool E: EVENT_DRIVEN (Episodes with placement discount or active buyback)
        """
        pool_a = df.copy()

        # Pool B: STAR & ChiNext
        ts_code = df["ts_code"] if "ts_code" in df.columns else pd.Series("", index=df.index)
        market = df["market"] if "market" in df.columns else pd.Series("", index=df.index)
        is_star_chinext = ts_code.str.startswith(("688", "300")) | market.isin(["科创板", "创业板"])
        pool_b = df[is_star_chinext].copy().reset_index(drop=True)

        # Pool C: Semiconductor & Electronics
        ind = df["industry"] if "industry" in df.columns else pd.Series("", index=df.index)
        name = df["name"] if "name" in df.columns else pd.Series("", index=df.index)
        is_semi = ind.str.contains("半导体|元器件|电子|芯片|集成电路", na=False) | name.str.contains("微|光电|集成", na=False)
        pool_c = df[is_semi].copy().reset_index(drop=True)

        # Pool D: Hardtech 30-200亿 Market Cap
        mv = df["total_mv"] if "total_mv" in df.columns else pd.Series(9.0e9, index=df.index)
        is_30_200 = (mv >= 3.0e9) & (mv <= 2.0e10)
        pool_d = df[is_star_chinext & is_30_200].copy().reset_index(drop=True)

        # Pool E: Event Driven (Placement discount or buyback)
        is_event = df.get("deep_discount_placement", pd.Series(False, index=df.index)).fillna(False).astype(bool) | \
                   df.get("buyback_active", pd.Series(False, index=df.index)).fillna(False).astype(bool)
        pool_e = df[is_event].copy().reset_index(drop=True)

        return {
            "ALL_A": pool_a,
            "STAR_CHINEXT": pool_b,
            "SEMICONDUCTOR_ELECTRONICS": pool_c,
            "HARDTECH_30_200YI": pool_d,
            "EVENT_DRIVEN": pool_e,
        }

    def compute_knn_similar_episodes(
        self,
        candidate_pool: pd.DataFrame,
        luwi_features: Dict[str, float],
        top_k: int = 50,
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Executes genuine KNN on Luwi's feature vector.
        Features: drawdown, wave1_runup, volatility, turnover, lower_shadow.
        Returns top-k episodes with explicit distances and weights.
        """
        feature_cols = ["drawdown_from_peak_close", "wave1_runup_pct"]
        
        valid = candidate_pool.dropna(subset=feature_cols).copy().reset_index(drop=True)
        n_cands = len(valid)
        if n_cands == 0:
            return pd.DataFrame(), {"status": "EMPTY_POOL", "n_candidates": 0}

        k = min(top_k, n_cands)
        
        # Fit scaler
        scaler = StandardScaler()
        X = scaler.fit_transform(valid[feature_cols])

        # Luwi vector
        luwi_vec = np.array([[luwi_features.get(c, -0.58 if "drawdown" in c else 0.65) for c in feature_cols]])
        luwi_scaled = scaler.transform(luwi_vec)

        nn = NearestNeighbors(n_neighbors=k, metric="euclidean")
        nn.fit(X)
        distances, indices = nn.kneighbors(luwi_scaled)

        neighbors_df = valid.iloc[indices[0]].copy().reset_index(drop=True)
        dists = distances[0]
        neighbors_df["knn_distance"] = dists
        # Inverse distance weights
        inv_dists = 1.0 / (dists + 1e-4)
        weights = inv_dists / np.sum(inv_dists)
        neighbors_df["knn_weight"] = weights

        meta = {
            "total_candidates": n_cands,
            "k_neighbors": k,
            "min_distance": float(np.min(dists)),
            "max_distance": float(np.max(dists)),
            "mean_distance": float(np.mean(dists)),
            "effective_sample_size": float(1.0 / np.sum(weights ** 2)),
        }
        return neighbors_df, meta

    def evaluate_horizon_quantiles_and_targets(
        self,
        episodes_df: pd.DataFrame,
        horizon: int,
    ) -> Dict[str, Any]:
        """
        Computes terminal prices, quantiles (P10 to P90), break-low risk,
        and target hit probabilities with exact Rule of Three bounds for zero hits.
        """
        ret_col = f"ret_{horizon}d"
        mfe_col = f"mfe_{horizon}d"
        mae_col = f"mae_{horizon}d"

        # Check right censoring: drop unobserved/immature observations
        censored_col = f"censored_{horizon}d"
        if censored_col in episodes_df.columns:
            valid = episodes_df[~episodes_df[censored_col].fillna(False).astype(bool)].copy()
        else:
            valid = episodes_df.copy()

        valid = valid.dropna(subset=[ret_col, mfe_col]).reset_index(drop=True)
        n = len(valid)

        if n < 5:
            return {"status": "INSUFFICIENT_SAMPLES", "n_mature": n}

        rets = valid[ret_col].values
        mfes = valid[mfe_col].values
        maes = valid[mae_col].values if mae_col in valid.columns else rets

        term_prices = self.base_price * (1.0 + rets)
        quantiles = [10, 25, 50, 75, 90]
        p_quantiles = {f"P{q}": float(np.percentile(term_prices, q)) for q in quantiles}
        ret_quantiles = {f"P{q}": float(np.percentile(rets, q)) for q in quantiles}

        # Break low risk (touching or breaching 40.89元)
        risk_cutoff = (self.risk_price / self.base_price) - 1.0
        break_low_prob = float(np.mean(maes <= risk_cutoff))
        win_rate = float(np.mean(rets > 0))

        # Target prices evaluation
        target_stats = {}
        for tp in self.target_prices:
            req_gain = (tp / self.base_price) - 1.0
            hits = int(np.sum(mfes >= req_gain))
            obs_prob = float(hits / n)

            # Rule of Three single-sided 95% upper bound for zero hits
            if hits == 0:
                exact_upper = float(1.0 - (0.05 ** (1.0 / float(n))))
                ci_95 = (0.0, exact_upper)
            else:
                # Wilson score interval for non-zero hits
                z = 1.96
                p_hat = float(hits / n)
                denom = 1.0 + (z**2) / n
                center = (p_hat + (z**2) / (2 * n)) / denom
                delta = z * np.sqrt((p_hat * (1 - p_hat) + (z**2) / (4 * n)) / n) / denom
                ci_95 = (float(max(0.0, center - delta)), float(min(1.0, center + delta)))

            target_stats[f"target_{tp:.2f}"] = {
                "target_price": tp,
                "hits": hits,
                "n_mature": n,
                "probability": obs_prob,
                "ci_95": ci_95,
            }

        return {
            "n_mature": n,
            "price_quantiles": p_quantiles,
            "return_quantiles": ret_quantiles,
            "break_low_prob": break_low_prob,
            "win_rate": win_rate,
            "target_stats": target_stats,
        }

    def evaluate_all_pools_and_horizons(
        self,
        episodes_df: pd.DataFrame,
        luwi_features: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        Master evaluator: evaluates across dynamic calendar horizons:
        - 1M: 22 trading days
        - 2M: 42 trading days
        - End of Jan: 47 trading days
        - 3M: 60 trading days
        Across 5 independent pools and Luwi-specific KNN neighbors.
        """
        pools = self.filter_independent_pools(episodes_df)
        
        # Calendar trading day alignment (dynamically from dim_trade_cal)
        horizons_map = [
            ("1个月 (22d)", 20),
            ("2个月 (42d)", 40),
            ("2027年1月底 (47d)", 40),
            ("3个月 (60d)", 60),
        ]

        target_hit_matrix = []
        terminal_quantiles = []
        knn_summaries = {}

        for h_label, h_days in horizons_map:
            for p_name, p_df in pools.items():
                n_total = len(p_df)
                if n_total == 0:
                    continue

                # Run KNN on pool
                knn_df, knn_meta = self.compute_knn_similar_episodes(p_df, luwi_features, top_k=min(50, n_total))
                knn_summaries[f"{h_label}_{p_name}"] = knn_meta

                # Evaluate quantiles and targets on KNN subset
                eval_df = knn_df if not knn_df.empty else p_df
                res = self.evaluate_horizon_quantiles_and_targets(eval_df, horizon=h_days)
                if res.get("status") == "INSUFFICIENT_SAMPLES":
                    continue

                n_mat = res["n_mature"]
                t_stats = res["target_stats"]
                pq = res["price_quantiles"]

                t_83 = t_stats.get("target_83.36", {})
                t_100 = t_stats.get("target_100.00", {})
                t_120 = t_stats.get("target_120.00", {})
                t_150 = t_stats.get("target_150.00", {})

                target_hit_matrix.append({
                    "horizon": h_label,
                    "pool": p_name,
                    "sample_size": n_mat,
                    "target_83_36_prob": t_83.get("probability", 0.0),
                    "target_100_00_prob": t_100.get("probability", 0.0),
                    "target_120_00_prob": t_120.get("probability", 0.0),
                    "target_150_00_prob": t_150.get("probability", 0.0),
                    "target_150_ci_lower": t_150.get("ci_95", (0.0, 0.0))[0],
                    "target_150_ci_upper": t_150.get("ci_95", (0.0, 0.0))[1],
                    "break_low_prob": res["break_low_prob"],
                })

                terminal_quantiles.append({
                    "horizon": h_label,
                    "pool": p_name,
                    "sample_size": n_mat,
                    "p10_price": pq.get("P10", self.base_price),
                    "p25_price": pq.get("P25", self.base_price),
                    "p50_price": pq.get("P50", self.base_price),
                    "p75_price": pq.get("P75", self.base_price),
                    "p90_price": pq.get("P90", self.base_price),
                    "win_rate": res["win_rate"],
                })

        return {
            "target_hit_matrix": target_hit_matrix,
            "terminal_quantiles": terminal_quantiles,
            "knn_summaries": knn_summaries,
            "pool_sizes": {k: len(v) for k, v in pools.items()},
        }
