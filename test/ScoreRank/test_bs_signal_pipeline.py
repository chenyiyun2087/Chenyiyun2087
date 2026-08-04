import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_model_infer import apply_bs_model_scores, latest_model_path, load_latest_bs_model  # noqa: E402
from scoreRank.core.ashare_data_center_features import attach_adc_features  # noqa: E402
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
        df = pd.DataFrame(columns=["bs_score_v2", "bs_gate_score", "bs_model_prob", "bs_consensus_score", "ret_20", "adc_hma_slope"])
        features = _feature_whitelist(df)

        self.assertIn("bs_score_v2", features)
        self.assertIn("bs_gate_score", features)
        self.assertIn("adc_hma_slope", features)
        self.assertNotIn("bs_model_prob", features)
        self.assertNotIn("bs_consensus_score", features)
        self.assertNotIn("ret_20", features)

    def test_attach_adc_features_uses_prefixed_point_in_time_fields(self):
        def fake_query(sql, params=None):
            sql_lower = sql.lower()
            if "information_schema.columns" in sql_lower:
                table = params[1]
                columns = {
                    "dws_tech_pattern": ["ts_code", "trade_date", "hma_slope", "rsi_14", "boll_width"],
                    "dws_capital_flow": ["ts_code", "trade_date", "main_net_ratio", "main_net_ma5"],
                }.get(table, [])
                return pd.DataFrame({"column_name": columns})
            if "dws_tech_pattern" in sql_lower:
                return pd.DataFrame(
                    [
                        {
                            "adc_ts_code": "000001.SZ",
                            "adc_event_date_key": "20260508",
                            "adc_hma_slope": 0.03,
                            "adc_rsi_14": 58,
                            "adc_boll_width": 0.12,
                        }
                    ]
                )
            if "dws_capital_flow" in sql_lower:
                return pd.DataFrame(
                    [
                        {
                            "adc_ts_code": "000001.SZ",
                            "adc_event_date_key": "20260508",
                            "adc_main_net_ratio": 0.08,
                            "adc_main_net_ma5": 0.03,
                        }
                    ]
                )
            return pd.DataFrame()

        df = pd.DataFrame([{"symbol": "1", "event_date_key": 20260508}])
        out = attach_adc_features(df, "event_date_key", fake_query)

        self.assertIn("adc_hma_slope", out.columns)
        self.assertIn("adc_main_net_accel", out.columns)
        self.assertAlmostEqual(float(out.loc[0, "adc_hma_slope"]), 0.03)
        self.assertAlmostEqual(float(out.loc[0, "adc_main_net_accel"]), 0.05)

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
        # v5.3 P0 freeze (2026-08-04): the ridge risk model's out-of-sample
        # R2 is negative — risk_model_in_chain=false.  The risk head columns
        # exist but stay None; ranking is probability + bs_score_v2 only.
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
        self.assertTrue(out["bs_model_risk_score"].isna().all(), "risk head frozen off in v5.3")
        self.assertGreater(float(out.loc[0, "bs_model_rank_score"]), float(out.loc[1, "bs_model_rank_score"]))

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

    def test_load_latest_bs_model_falls_back_when_joblib_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "20260511_210000"
            model_dir.mkdir()
            (model_dir / "logistic_calibrated_hit_20_10pct.joblib").write_bytes(b"placeholder")

            original_import = __import__

            def fake_import(name, *args, **kwargs):
                if name == "joblib":
                    raise ModuleNotFoundError("No module named 'joblib'", name="joblib")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import), self.assertWarns(RuntimeWarning):
                bundle = load_latest_bs_model(model_root=tmp, target="hit_20_10pct",
                                              research_mode=True)

            self.assertIsNone(bundle)

    def test_load_latest_bs_model_falls_back_when_bundle_load_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "20260511_210000"
            model_dir.mkdir()
            (model_dir / "logistic_calibrated_hit_20_10pct.joblib").write_bytes(b"placeholder")

            class FakeJoblib:
                @staticmethod
                def load(_path):
                    raise AttributeError("incompatible sklearn bundle")

            with patch.dict(sys.modules, {"joblib": FakeJoblib}), self.assertWarns(RuntimeWarning):
                bundle = load_latest_bs_model(model_root=tmp, target="hit_20_10pct",
                                              research_mode=True)

            self.assertIsNone(bundle)

    def test_latest_model_path_prefers_active_model_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older_dir = root / "20260511_210000"
            newer_dir = root / "20260511_220000"
            older_dir.mkdir()
            newer_dir.mkdir()
            active_model = older_dir / "random_forest_hit_20_10pct.joblib"
            fallback_model = newer_dir / "hist_gradient_boosting_hit_20_10pct.joblib"
            active_model.write_bytes(b"active")
            fallback_model.write_bytes(b"fallback")
            (root / "active_model.json").write_text(
                '{"target": "hit_20_10pct", "model_path": "' + str(active_model) + '"}',
                encoding="utf-8",
            )

            self.assertEqual(latest_model_path(root, "hit_20_10pct"), active_model)

    def test_db_config_builds_urls_without_embedded_secret_default(self):
        self.assertEqual(symbol_to_ts_code("600000"), "600000.SH")
        self.assertEqual(symbol_to_ts_code("300001"), "300001.SZ")
        self.assertEqual(symbols_to_ts_codes(["830001"]), ["830001.BJ"])
        self.assertIn("mysql+pymysql://root@localhost:3306/chenyiyun", build_sqlalchemy_url("MISSING_TEST_DB"))


if __name__ == "__main__":
    unittest.main()
