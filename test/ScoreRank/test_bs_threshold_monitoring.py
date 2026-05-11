import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_monitoring import compare_distributions, cost_sensitive_topn_report, shadow_pool_overlap, topn_rank_report  # noqa: E402
from scoreRank.core.bs_threshold_policy import assign_shadow_pool, resolve_bs_thresholds  # noqa: E402
from scoreRank.core.external_features import attach_external_features  # noqa: E402
from scripts.evaluate_bs_holding_policy import assign_holding_actions, evaluate_holding_policy  # noqa: E402
from scripts.optimize_bs_thresholds import evaluate_threshold_candidate  # noqa: E402
from scripts.train_bs_signal_model import _build_pipeline, _model_file_name  # noqa: E402


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

    def test_cost_sensitive_topn_subtracts_round_trip_cost(self):
        df = pd.DataFrame(
            [
                {"event_date": "2026-01-01", "score_a": 90, "hit_20_10pct": 1, "ret_20": 0.10, "max_ret_20": 0.2, "mdd_20": -0.02},
                {"event_date": "2026-01-01", "score_a": 80, "hit_20_10pct": 0, "ret_20": 0.02, "max_ret_20": 0.05, "mdd_20": -0.08},
            ]
        )
        report = cost_sensitive_topn_report(df, ["score_a"], horizon=20, top_ns=(1,), cost_bps=10, slippage_bps=5)

        self.assertAlmostEqual(float(report.iloc[0]["avg_net_ret"]), 0.097)

    def test_threshold_candidate_scores_trade_pool(self):
        df = pd.DataFrame(
            [
                {"is_bs_candidate": 1, "bs_gate_label": "可买", "bs_consensus_score": 70, "bs_model_rank_score": 64, "bs_score_v2": 60, "hit_20_10pct": 1, "ret_20": 0.08, "max_ret_20": 0.15, "mdd_20": -0.03},
                {"is_bs_candidate": 1, "bs_gate_label": "过滤", "bs_consensus_score": 90, "bs_model_rank_score": 90, "bs_score_v2": 90, "hit_20_10pct": 0, "ret_20": -0.05, "max_ret_20": 0.01, "mdd_20": -0.12},
            ]
        )
        result = evaluate_threshold_candidate(
            df,
            {
                "consensus_trade": 66,
                "consensus_watch": 54,
                "model_trade": 62,
                "model_watch": 52,
                "v2_trade": 72,
                "v2_watch": 58,
            },
            horizon=20,
            min_trade_rows=1,
        )

        self.assertEqual(result["trade_rows"], 1)
        self.assertEqual(result["trade_hit_rate"], 1.0)

    def test_holding_policy_assigns_actions(self):
        df = pd.DataFrame(
            [
                {"bs_gate_label": "可买", "bs_consensus_score": 66, "bs_model_rank_score": 60, "ret_3": 0.01, "ret_5": 0.02, "mdd_10": -0.02, "hit_5_10pct": 0, "hit_20_10pct": 1, "ret_20": 0.12, "mdd_20": -0.04},
                {"bs_gate_label": "过滤", "bs_consensus_score": 80, "bs_model_rank_score": 80, "ret_3": 0.01, "ret_5": 0.01, "mdd_10": -0.01, "hit_5_10pct": 0, "hit_20_10pct": 0, "ret_20": -0.06, "mdd_20": -0.10},
            ]
        )
        actions = assign_holding_actions(df)
        report = evaluate_holding_policy(df, horizon=20)

        self.assertEqual(actions.iloc[0], "ADD")
        self.assertEqual(actions.iloc[1], "EXIT")
        self.assertEqual(report["action_counts"]["ADD"], 1)

    def test_random_forest_pipeline_and_model_name(self):
        df = pd.DataFrame({"feature": [1, 2, 3, 4, 5, 6], "sector": ["a", "a", "b", "b", "c", "c"], "target": [0, 0, 0, 1, 1, 1]})
        pipe = _build_pipeline(df, ["feature", "sector"], model_kind="random_forest")
        pipe.fit(df[["feature", "sector"]], df["target"])

        self.assertEqual(_model_file_name("random_forest", "hit_20_10pct"), "random_forest_hit_20_10pct.joblib")
        self.assertEqual(pipe.predict_proba(df[["feature", "sector"]]).shape, (6, 2))


if __name__ == "__main__":
    unittest.main()
