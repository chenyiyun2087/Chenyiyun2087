"""Tests for PR15: final promotion evaluation."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path
import tempfile

from scripts.research.final_promotion import (
    stitch_oos_nav, compute_stitched_metrics,
    generate_final_report, StitchedOOSResult, FinalPromotionReport,
)


def _make_fold_results(n_folds=3, n_days=30):
    folds = []
    for fi in range(n_folds):
        nav_rows = []
        nav = 1.0
        for d in range(n_days):
            nav *= (1 + np.random.RandomState(fi * 100 + d).normal(0.001, 0.01))
            nav_rows.append({"trade_date": f"2023-0{fi+1}-{d+1:02d}", "nav": nav})
        fr = type("FR", (), {
            "fold_index": fi, "nav_rows": nav_rows,
            "window_label": f"202{fi+3}H1", "metrics": None, "trade_rows": [],
        })()
        folds.append(fr)
    return folds


class TestStitchedOOS:
    def test_stitch_produces_continuous_nav(self):
        folds = _make_fold_results(3, 30)
        nav = stitch_oos_nav(folds)
        assert len(nav) > 0

    def test_compute_metrics(self):
        nav = pd.Series([1.0, 1.02, 1.05, 1.03, 1.08, 1.12])
        metrics = compute_stitched_metrics(nav)
        assert metrics.cumulative_return > 0
        assert metrics.sharpe_ratio > 0

    def test_empty_nav(self):
        metrics = compute_stitched_metrics(pd.Series([], dtype=float))
        assert metrics.cumulative_return == 0.0


class TestFinalReport:
    def test_generate_report_creates_files(self):
        stitched_a9 = StitchedOOSResult(
            cumulative_return=0.25, annualized_return=0.22, max_drawdown=-0.18,
            sharpe_ratio=1.30, calmar_ratio=1.22, cvar_95=-0.04, worst_day=-0.05,
            ann_volatility=0.20, trading_days=252,
        )
        stitched_c0 = StitchedOOSResult(
            cumulative_return=0.15, annualized_return=0.14, max_drawdown=-0.22,
            sharpe_ratio=0.80, calmar_ratio=0.64,
        )
        decision = type("D", (), {
            "recommend_promotion": True, "overall_score": 0.92,
            "conditions_passed": 12, "conditions_total": 14,
            "failure_reasons": ["cost_stress: 25bp fails"],
            "evidence": [
                type("E", (), {"gate_name": "comparison", "passed": True})(),
                type("E", (), {"gate_name": "promotion", "passed": True})(),
            ],
        })()
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_final_report(stitched_a9, stitched_c0, decision, Path(tmp))
            assert Path(result["json_path"]).exists()
            assert Path(result["md_path"]).exists()
            assert result["report"]["recommend_promotion"] is True

    def test_blocked_promotion(self):
        stitched_a9 = StitchedOOSResult(cumulative_return=-0.05, max_drawdown=-0.30)
        stitched_c0 = StitchedOOSResult(cumulative_return=0.10, max_drawdown=-0.22)
        decision = type("D", (), {
            "recommend_promotion": False, "overall_score": 0.25,
            "conditions_passed": 4, "conditions_total": 14,
            "failure_reasons": ["cumulative_return: A9 < A0", "drawdown: A9 < A0"],
            "evidence": [
                type("E", (), {"gate_name": "comparison", "passed": False})(),
                type("E", (), {"gate_name": "promotion", "passed": False})(),
            ],
        })()
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_final_report(stitched_a9, stitched_c0, decision, Path(tmp))
            assert result["report"]["recommend_promotion"] is False

    def test_excess_return_positive_when_a9_wins(self):
        stitched_a9 = StitchedOOSResult(annualized_return=0.25)
        stitched_c0 = StitchedOOSResult(annualized_return=0.15)
        excess = stitched_a9.annualized_return - stitched_c0.annualized_return
        assert excess == 0.10
