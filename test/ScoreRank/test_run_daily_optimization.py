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


if __name__ == '__main__':
    unittest.main()
