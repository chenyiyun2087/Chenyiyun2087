import sys
import unittest
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.cli.build_bs_consensus import (  # noqa: E402
    SCORE_UPDATE_COLUMNS,
    _parse_trade_date,
    enrich_score_rows,
)


class TestBuildBSConsensus(unittest.TestCase):
    def test_parse_trade_date_accepts_common_formats(self):
        self.assertEqual(_parse_trade_date("20260508"), date(2026, 5, 8))
        self.assertEqual(_parse_trade_date("2026-05-08"), date(2026, 5, 8))

    def test_enrich_score_rows_returns_update_payload(self):
        rows = [
            {
                "symbol": "2903",
                "score": 76,
                "base_score": 76,
                "penalty": 0,
                "s_trend": 100,
                "s_breakout": 94.53,
                "s_volume": 94.6,
                "s_rs": 98.55,
                "s_contraction": 37.83,
                "s_liquidity": 22.87,
                "opt_score": 7.75,
                "claude_score": 48.66,
                "price_change_ratio": 6,
                "is_limit_up": 0,
                "bs_model_prob": 0.72,
            }
        ]

        enriched = enrich_score_rows(rows)

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["symbol"], "002903")
        for col in SCORE_UPDATE_COLUMNS:
            self.assertIn(col, enriched[0])
        self.assertGreater(enriched[0]["bs_consensus_score"], 0)
        self.assertIn(enriched[0]["bs_consensus_label"], {"共振观察", "谨慎观察", "模型分歧", "回避"})


if __name__ == "__main__":
    unittest.main()
