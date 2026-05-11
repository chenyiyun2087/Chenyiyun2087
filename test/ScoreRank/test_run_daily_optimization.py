import unittest
from pathlib import Path


class TestRunDailyOptimization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path('scoreRank/cli/run_daily.py')
        cls.content = cls.path.read_text(encoding='utf-8')

    def test_vectorized_enrichment_is_used(self):
        self.assertIn('enrich_scored_with_market_metrics(scored, features)', self.content)
        self.assertNotIn("scored.apply(get_close, axis=1)", self.content)
        self.assertNotIn("scored.apply(get_is_limit_up, axis=1)", self.content)
        self.assertNotIn("scored['price_change_ratio'] = scored.apply(calc_ratio, axis=1)", self.content)

    def test_delete_query_is_parameterized(self):
        self.assertIn('DELETE FROM score_rank_daily WHERE trade_date = :trade_date', self.content)
        self.assertNotIn("DELETE FROM score_rank_daily WHERE trade_date = '{asof_date.date()}'", self.content)

    def test_model_scores_are_applied_before_save(self):
        self.assertIn('load_latest_bs_model(target=CONFIG.get("bs_model_target", "hit_20_10pct"))', self.content)
        self.assertIn('apply_bs_model_scores(scored, model_bundle=model_bundle, only_candidates=True)', self.content)
        self.assertIn("'bs_model_prob': 'bs_model_prob'", self.content)
        self.assertIn("'bs_model_rank_score': 'bs_model_rank_score'", self.content)

    def test_bs_detection_metadata_is_merged_for_model_features(self):
        self.assertIn('"event_seq_for_symbol"', self.content)
        self.assertIn('"total_b_points"', self.content)
        self.assertIn('"buy_points_count"', self.content)

    def test_dynamic_threshold_and_shadow_pool_are_applied(self):
        self.assertIn("resolve_bs_thresholds(market_context, CONFIG)", self.content)
        self.assertIn("assign_shadow_pool(scored, CONFIG)", self.content)
        self.assertIn("'pool_type_shadow': 'pool_type_shadow'", self.content)
        self.assertIn("'dynamic_trade_threshold': 'dynamic_trade_threshold'", self.content)


if __name__ == '__main__':
    unittest.main()
