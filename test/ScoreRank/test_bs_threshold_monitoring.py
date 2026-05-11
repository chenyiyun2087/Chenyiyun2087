import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_monitoring import (  # noqa: E402
    compare_distributions,
    cost_scenario_topn_report,
    cost_sensitive_topn_report,
    portfolio_risk_report,
    shadow_pool_overlap,
    topn_rank_report,
)
from scoreRank.core.bs_threshold_policy import assign_shadow_pool, resolve_bs_thresholds  # noqa: E402
from scoreRank.core.external_features import attach_external_features  # noqa: E402
from scripts.export_signal_enhancement_dataset import _add_split_column, _field_contract_report  # noqa: E402
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

    def test_cost_scenario_topn_reports_three_cost_profiles(self):
        df = pd.DataFrame(
            [
                {"event_date": "2026-01-01", "score_a": 90, "hit_20_10pct": 1, "ret_20": 0.10},
                {"event_date": "2026-01-02", "score_a": 85, "hit_20_10pct": 1, "ret_20": 0.08},
            ]
        )
        report = cost_scenario_topn_report(df, ["score_a"], horizon=20, top_ns=(1,))

        self.assertEqual(set(report["scenario"]), {"optimistic", "base", "conservative"})

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
        self.assertIn("benchmark_avg_ret", report)

    def test_random_forest_pipeline_and_model_name(self):
        df = pd.DataFrame({"feature": [1, 2, 3, 4, 5, 6], "sector": ["a", "a", "b", "b", "c", "c"], "target": [0, 0, 0, 1, 1, 1]})
        pipe = _build_pipeline(df, ["feature", "sector"], model_kind="random_forest")
        pipe.fit(df[["feature", "sector"]], df["target"])

        self.assertEqual(_model_file_name("random_forest", "hit_20_10pct"), "random_forest_hit_20_10pct.joblib")
        self.assertEqual(pipe.predict_proba(df[["feature", "sector"]]).shape, (6, 2))

    def test_hist_gradient_boosting_pipeline_and_model_name(self):
        df = pd.DataFrame({"feature": [1, 2, 3, 4, 5, 6], "sector": ["a", "a", "b", "b", "c", "c"], "target": [0, 0, 0, 1, 1, 1]})
        pipe = _build_pipeline(df, ["feature", "sector"], model_kind="hist_gradient_boosting")
        pipe.fit(df[["feature", "sector"]], df["target"])

        self.assertEqual(_model_file_name("hist_gradient_boosting", "hit_20_10pct"), "hist_gradient_boosting_hit_20_10pct.joblib")
        self.assertEqual(pipe.predict_proba(df[["feature", "sector"]]).shape, (6, 2))

    def test_field_contract_report_marks_groups_complete(self):
        frame = pd.DataFrame(
            {
                "event_date": ["2026-01-01"],
                "event_uid": ["u1"],
                "symbol": ["000001"],
                "ts_code": ["000001.SZ"],
                "name": ["A"],
                "score": [70],
                "hit_20_10pct": [1],
            }
        )
        report = _field_contract_report(frame)

        self.assertTrue(report["labels"]["complete"])
        self.assertTrue(report["identity"]["complete"])

    def test_horizon_aware_split_creates_target_specific_columns(self):
        df = pd.DataFrame(
            {
                "event_date": pd.date_range("2026-01-01", periods=20, freq="D"),
                "hit_20_10pct": [1] * 18 + [None, None],
            }
        )
        out = _add_split_column(df, primary_horizon=20)

        self.assertIn("split_hit_20_10pct", out.columns)
        self.assertEqual(set(out.loc[out["hit_20_10pct"].isna(), "split_hit_20_10pct"]), {"unlabeled"})

    def test_portfolio_risk_report_applies_constraints(self):
        df = pd.DataFrame(
            [
                {"event_date": "2026-01-01", "symbol": "000001", "industry": "tech", "score_a": 90, "bs_model_prob": 0.6, "bs_model_expected_mdd": -0.05, "hit_20_10pct": 1, "ret_20": 0.12, "mdd_20": -0.04},
                {"event_date": "2026-01-01", "symbol": "000002", "industry": "tech", "score_a": 85, "bs_model_prob": 0.4, "bs_model_expected_mdd": -0.08, "hit_20_10pct": 0, "ret_20": -0.02, "mdd_20": -0.10},
                {"event_date": "2026-01-01", "symbol": "000003", "industry": "bank", "score_a": 80, "bs_model_prob": 0.3, "bs_model_expected_mdd": -0.03, "hit_20_10pct": 1, "ret_20": 0.04, "mdd_20": -0.02},
            ]
        )
        report = portfolio_risk_report(df, "score_a", horizon=20, top_n=3, max_position_weight=0.6, max_industry_weight=0.7)

        self.assertEqual(report["days"], 1)
        self.assertLessEqual(report["avg_max_position_weight"], 0.6)


if __name__ == "__main__":
    unittest.main()
