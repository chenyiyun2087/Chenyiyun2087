import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_monitoring import compare_distributions, shadow_pool_overlap, topn_rank_report  # noqa: E402
from scoreRank.core.bs_threshold_policy import assign_shadow_pool, resolve_bs_thresholds  # noqa: E402
from scoreRank.core.external_features import attach_external_features  # noqa: E402


class TestBSThresholdMonitoring(unittest.TestCase):
    def test_dynamic_threshold_tightens_in_risk_off(self):
        cfg = {"bs_v2_trade_threshold": 72, "bs_v2_watch_threshold": 58}
        decision = resolve_bs_thresholds(
            {
                "market_regime": "risk_off",
                "market_hs300_ret_20": -0.05,
                "market_hs300_pct_chg": -2.5,
                "market_bs_ratio": 0.18,
                "market_limit_up_rate": 0.09,
            },
            cfg,
        )

        self.assertGreater(decision.trade_threshold, 72)
        self.assertGreater(decision.watch_threshold, 58)
        self.assertIn("risk_off_tighten", decision.reason)

    def test_assign_shadow_pool_uses_consensus_and_model(self):
        df = pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "is_bs_candidate": 1,
                    "bs_gate_label": "可买",
                    "bs_consensus_score": 70,
                    "bs_model_rank_score": 65,
                },
                {
                    "symbol": "000002",
                    "is_bs_candidate": 1,
                    "bs_gate_label": "过滤",
                    "bs_consensus_score": 90,
                    "bs_model_rank_score": 90,
                },
            ]
        )
        out = assign_shadow_pool(df, {})

        self.assertEqual(out.loc[0, "pool_type_shadow"], "TRADE")
        self.assertIsNone(out.loc[1, "pool_type_shadow"])

    def test_topn_rank_report_compares_score_columns(self):
        df = pd.DataFrame(
            [
                {"event_date": "2026-01-01", "score_a": 90, "score_b": 10, "hit_20_10pct": 1, "max_ret_20": 0.2, "mdd_20": -0.05},
                {"event_date": "2026-01-01", "score_a": 10, "score_b": 90, "hit_20_10pct": 0, "max_ret_20": 0.01, "mdd_20": -0.2},
                {"event_date": "2026-01-02", "score_a": 80, "score_b": 20, "hit_20_10pct": 1, "max_ret_20": 0.15, "mdd_20": -0.04},
                {"event_date": "2026-01-02", "score_a": 20, "score_b": 80, "hit_20_10pct": 0, "max_ret_20": 0.02, "mdd_20": -0.12},
            ]
        )
        report = topn_rank_report(df, ["score_a", "score_b"], horizon=20, top_ns=(1,))
        row = report[report["score_col"] == "score_a"].iloc[0]

        self.assertEqual(row["top_n"], 1)
        self.assertEqual(row["avg_hit_rate"], 1.0)

    def test_compare_distribution_warns_on_shift(self):
        ref = pd.DataFrame({"bs_model_prob": [0.1, 0.2, 0.3]})
        cur = pd.DataFrame({"bs_model_prob": [0.8, 0.9, 1.0]})
        report = compare_distributions(ref, cur, ["bs_model_prob"])

        self.assertIn("bs_model_prob:mean_shift_high", report["warnings"])

    def test_shadow_overlap_counts_actionable_rows(self):
        report = shadow_pool_overlap(
            pd.DataFrame(
                [
                    {"pool_type": "TRADE", "pool_type_shadow": "TRADE"},
                    {"pool_type": "WATCH", "pool_type_shadow": None},
                    {"pool_type": None, "pool_type_shadow": "WATCH"},
                ]
            )
        )

        self.assertEqual(report["current_actionable"], 2)
        self.assertEqual(report["shadow_actionable"], 2)

    def test_external_features_fallback_is_non_blocking(self):
        def failing_query(sql, params=None):
            raise RuntimeError("missing table")

        out = attach_external_features(pd.DataFrame({"symbol": ["1"]}), pd.Timestamp("2026-05-11"), failing_query)

        self.assertIn("industry", out.columns)
        self.assertIn("fund_pe_ttm", out.columns)


if __name__ == "__main__":
    unittest.main()
