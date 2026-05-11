import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_model_infer import apply_bs_model_scores  # noqa: E402
from scoreRank.core.db_config import build_sqlalchemy_url, symbol_to_ts_code, symbols_to_ts_codes  # noqa: E402
from scripts.export_signal_enhancement_dataset import _feature_whitelist, _time_split_for_mask  # noqa: E402


class FakeModel:
    def predict_proba(self, frame):
        base = pd.to_numeric(frame["bs_score_v2"], errors="coerce").fillna(0.0).to_numpy() / 100.0
        return np.column_stack([1.0 - base, base])


class FakeRiskModel:
    def predict(self, frame):
        return np.where(pd.to_numeric(frame["bs_score_v2"], errors="coerce").fillna(0.0) >= 70, -0.03, -0.20)


class TestBSSignalPipeline(unittest.TestCase):
    def test_horizon_split_keeps_embargo_gap(self):
        dates = pd.Series(pd.date_range("2025-01-01", periods=80, freq="D"))
        mask = pd.Series(True, index=dates.index)
        split = _time_split_for_mask(dates, mask, embargo_days=20)

        self.assertIn("embargo", set(split))
        train_max = dates[split == "train"].max()
        val_min = dates[split == "validation"].min()
        val_max = dates[split == "validation"].max()
        test_min = dates[split == "test"].min()
        self.assertGreater((val_min - train_max).days, 1)
        self.assertGreater((test_min - val_max).days, 1)

    def test_feature_whitelist_excludes_model_outputs(self):
        df = pd.DataFrame(columns=["bs_score_v2", "bs_gate_score", "bs_model_prob", "bs_consensus_score", "ret_20"])
        features = _feature_whitelist(df)

        self.assertIn("bs_score_v2", features)
        self.assertIn("bs_gate_score", features)
        self.assertNotIn("bs_model_prob", features)
        self.assertNotIn("bs_consensus_score", features)
        self.assertNotIn("ret_20", features)

    def test_apply_bs_model_scores_only_scores_candidates(self):
        df = pd.DataFrame(
            [
                {"symbol": "000001", "is_bs_candidate": 1, "bs_score_v2": 80, "bs_gate_label": "可买"},
                {"symbol": "000002", "is_bs_candidate": 0, "bs_score_v2": 80, "bs_gate_label": "可买"},
                {"symbol": "000003", "is_bs_candidate": 1, "bs_score_v2": 80, "bs_gate_label": "过滤"},
            ]
        )
        out = apply_bs_model_scores(
            df,
            {"model": FakeModel(), "feature_cols": ["bs_score_v2"], "version": "unit"},
            only_candidates=True,
        )

        self.assertAlmostEqual(float(out.loc[0, "bs_model_prob"]), 0.8)
        self.assertIsNone(out.loc[1, "bs_model_prob"])
        self.assertLess(float(out.loc[2, "bs_model_rank_score"]), float(out.loc[0, "bs_model_rank_score"]))
        self.assertEqual(out.loc[0, "bs_model_version"], "unit")

    def test_apply_bs_model_scores_adds_risk_head_outputs(self):
        df = pd.DataFrame(
            [
                {"symbol": "000001", "is_bs_candidate": 1, "bs_score_v2": 80, "bs_gate_label": "可买"},
                {"symbol": "000002", "is_bs_candidate": 1, "bs_score_v2": 40, "bs_gate_label": "可买"},
            ]
        )
        out = apply_bs_model_scores(
            df,
            {"model": FakeModel(), "risk_model": FakeRiskModel(), "feature_cols": ["bs_score_v2"], "version": "unit"},
            only_candidates=True,
        )

        self.assertIn("bs_model_expected_mdd", out.columns)
        self.assertIn("bs_model_risk_score", out.columns)
        self.assertGreater(float(out.loc[0, "bs_model_risk_score"]), float(out.loc[1, "bs_model_risk_score"]))

    def test_apply_bs_model_scores_records_missing_features(self):
        df = pd.DataFrame([{"symbol": "000001", "is_bs_candidate": 1, "bs_score_v2": 80}])
        with self.assertWarns(RuntimeWarning):
            out = apply_bs_model_scores(
                df,
                {"model": FakeModel(), "feature_cols": ["bs_score_v2", "total_b_points"], "version": "unit"},
                only_candidates=True,
            )

        self.assertIn("total_b_points", out.attrs["bs_model_missing_features"])
        self.assertAlmostEqual(float(out.loc[0, "bs_model_prob"]), 0.8)

    def test_db_config_builds_urls_without_embedded_secret_default(self):
        self.assertEqual(symbol_to_ts_code("600000"), "600000.SH")
        self.assertEqual(symbol_to_ts_code("300001"), "300001.SZ")
        self.assertEqual(symbols_to_ts_codes(["830001"]), ["830001.BJ"])
        self.assertIn("mysql+pymysql://root@localhost:3306/chenyiyun", build_sqlalchemy_url("MISSING_TEST_DB"))


if __name__ == "__main__":
    unittest.main()
