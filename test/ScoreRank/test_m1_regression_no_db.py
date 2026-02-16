import unittest
from pathlib import Path


class TestM1RegressionNoDB(unittest.TestCase):
    """M1 regression checks that do not require external DB connections."""

    @classmethod
    def setUpClass(cls):
        cls.kpi_cli = Path('scoreRank/cli/build_b_event_kpi.py').read_text(encoding='utf-8')
        cls.web_app = Path('web/app.py').read_text(encoding='utf-8')
        cls.web_schema = Path('web_schema.sql').read_text(encoding='utf-8')

    def test_kpi_cli_contains_required_tables_and_metrics(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS b_event_fact', self.kpi_cli)
        self.assertIn('CREATE TABLE IF NOT EXISTS b_event_kpi', self.kpi_cli)
        self.assertIn('hit_3_10pct', self.kpi_cli)
        self.assertIn('hit_5_10pct', self.kpi_cli)
        self.assertIn('hit_10_10pct', self.kpi_cli)
        self.assertIn('mdd_3', self.kpi_cli)
        self.assertIn('mdd_5', self.kpi_cli)
        self.assertIn('mdd_10', self.kpi_cli)

    def test_web_schema_contains_m1_tables(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS b_event_fact', self.web_schema)
        self.assertIn('CREATE TABLE IF NOT EXISTS b_event_kpi', self.web_schema)
        self.assertIn('UNIQUE KEY uniq_event_symbol (event_date, symbol)', self.web_schema)

    def test_strategy_routes_have_no_db_fallback(self):
        self.assertIn('def _safe_fetch_strategy_context(conn):', self.web_app)
        self.assertIn('if conn is None:', self.web_app)
        self.assertIn("@app.route('/sina/strategy/pyramid')", self.web_app)
        self.assertIn("@app.route('/sina/strategy/weighted')", self.web_app)
        self.assertIn("@app.route('/sina/strategy/quadrant')", self.web_app)
        self.assertIn('m1_summary=m1_summary', self.web_app)


if __name__ == '__main__':
    unittest.main()
