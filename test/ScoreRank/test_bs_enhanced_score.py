import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_enhanced_score import (  # noqa: E402
    add_bs_enhanced_scores,
    calculate_bs_enhanced_score,
    calculate_bs_research_signal,
    calculate_bs_score_v2,
    entry_timing_score,
    normalize_opt_score,
)


class TestBSEnhancedScore(unittest.TestCase):
    def test_normalize_opt_score_supports_zero_to_ten_scale(self):
        self.assertEqual(normalize_opt_score(7.2), 72.0)
        self.assertEqual(normalize_opt_score(81), 81.0)

    def test_entry_timing_prefers_early_positive_confirmation(self):
        self.assertGreater(entry_timing_score(6), entry_timing_score(-15))
        self.assertGreater(entry_timing_score(6), entry_timing_score(45))
        self.assertGreater(entry_timing_score(-1), entry_timing_score(-10))

    def test_calculate_bs_enhanced_score_is_bounded_and_labeled(self):
        result = calculate_bs_enhanced_score(
            {
                "score": 72,
                "opt_score": 8.5,
                "claude_score": 68,
                "s_rs": 85,
                "s_breakout": 80,
                "price_change_ratio": 5,
                "is_limit_up": 0,
            }
        )
        self.assertGreaterEqual(result["bs_score"], 0)
        self.assertLessEqual(result["bs_score"], 100)
        self.assertIn(result["bs_score_label"], {"强确认", "可交易", "观察", "等待"})

    def test_calculate_bs_score_v2_uses_risk_adjusted_labels(self):
        strong = calculate_bs_score_v2(
            {
                "score": 78,
                "opt_score": 9,
                "claude_score": 70,
                "s_rs": 90,
                "s_liquidity": 85,
                "s_breakout": 88,
                "s_volume": 80,
                "s_contraction": 60,
                "price_change_ratio": 4,
                "is_limit_up": 0,
            }
        )
        weak = calculate_bs_score_v2(
            {
                "score": 40,
                "opt_score": 3,
                "claude_score": 35,
                "s_rs": 25,
                "s_liquidity": 10,
                "s_breakout": 30,
                "s_volume": 20,
                "price_change_ratio": 45,
                "is_limit_up": 1,
            }
        )
        self.assertGreater(strong["bs_score_v2"], weak["bs_score_v2"])
        self.assertEqual(strong["bs_score_v2_label"], "强买")
        self.assertEqual(weak["bs_score_v2_label"], "剔除")

    def test_research_signal_rewards_v2_rs_liquidity_confluence(self):
        strong = calculate_bs_research_signal(
            {
                "bs_score_v2": 56,
                "s_rs": 82,
                "s_liquidity": 72,
                "s_breakout": 65,
                "price_change_ratio": 2,
                "is_limit_up": 0,
            }
        )
        weak = calculate_bs_research_signal(
            {
                "bs_score_v2": 45,
                "s_rs": 35,
                "s_liquidity": 15,
                "s_breakout": 45,
                "price_change_ratio": 16,
                "is_limit_up": 1,
            }
        )
        self.assertGreater(strong["bs_research_score"], weak["bs_research_score"])
        self.assertEqual(strong["bs_research_label"], "强观察")
        self.assertEqual(weak["bs_research_label"], "回避")
        self.assertIn("共振", strong["bs_research_reason"])

    def test_add_bs_enhanced_scores_preserves_rows(self):
        df = pd.DataFrame(
            [
                {"symbol": "000001", "score": 70, "opt_score": 8, "claude_score": 65},
                {"symbol": "000002", "score": 35, "opt_score": 3, "claude_score": 20},
            ]
        )
        out = add_bs_enhanced_scores(df)
        self.assertEqual(len(out), 2)
        self.assertIn("bs_score", out.columns)
        self.assertIn("bs_score_v2", out.columns)
        self.assertIn("bs_research_label", out.columns)
        self.assertGreater(out.iloc[0]["bs_score"], out.iloc[1]["bs_score"])


if __name__ == "__main__":
    unittest.main()
