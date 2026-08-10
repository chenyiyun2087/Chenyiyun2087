from __future__ import annotations

import numpy as np
import pandas as pd

from backtest_engine.config import BacktestConfig
from backtest_engine.core.engine import BacktestEngine
from backtest_engine.core.strategy import Strategy
from backtest_engine.core.types import Bar, Order
from backtest_engine.datafeed.base import DataFeed
from scripts.research.advanced_statistical_validation import cscv_pbo, permutation_test
from scripts.research.build_capacity_stress_matrix import build_capacity_stress_matrix
from scripts.research.research_preregistration import validate_formal_evidence
from scripts.research.t2130_research_pipeline import run_t2130_pipeline


def test_forged_in_memory_e3_is_never_formal():
    result = validate_formal_evidence(
        pit_qualifier={"component": "pit_factor_panel", "status": "DATA_E3_QUALIFIED", "content_sha256": "a" * 64},
        forward_evidence={"epoch_id": "fake", "evidence_sha256": "b" * 64},
        formal_epoch={"epoch_id": "fake"},
    )
    assert result["status"] == "BLOCKED"
    assert "formal_evidence_paths_required" in result["reason"]


class _TwoSymbolFeed(DataFeed):
    def iter_bars(self, start, end, universe, fields, freq):
        for day in ("2026-08-10", "2026-08-11"):
            for symbol in universe:
                yield Bar(day, symbol, 10.0, 10.0, 10.0, 10.0, 1_000_000)


class _BuyOnFirstDay(Strategy):
    def on_bar(self, bar, context):
        return [Order(bar.ts, bar.symbol, "BUY", 100)] if bar.ts == "2026-08-10" else []


def test_two_symbol_pending_orders_cannot_disappear():
    result = BacktestEngine(
        _TwoSymbolFeed(), _BuyOnFirstDay(), BacktestConfig(initial_cash=10_000), trusted=True,
    ).run("2026-08-10", "2026-08-11", ["000001", "600000"])
    assert len(result.trades) + len(result.rejections) == 2
    assert {trade.symbol for trade in result.trades} == {"000001", "600000"}


def test_block_permutation_standard_rank_pbo_and_capacity_monotonicity():
    permutation = permutation_test(np.linspace(-0.01, 0.02, 80), n_permutations=19, seed=7)
    assert permutation["mode"] == "block_sign_flip" and permutation["block_size"] == 20
    pbo = cscv_pbo(np.vstack([np.linspace(0, .02, 80), np.linspace(.01, -.01, 80)]))
    assert pbo["status"] == "PASS"
    assert {"oos_rank", "omega", "rank_logit"}.issubset(pbo["splits"][0])
    matrix = build_capacity_stress_matrix(
        [{"portfolio_weight": 0.5, "adv20_cny": 1_000_000}],
        slippage_bps_grid=[10], capital_grid=[50_000, 500_000, 5_000_000],
    ).sort_values("capital_cny")
    assert matrix["missed_fill_rate"].is_monotonic_increasing


def _synthetic_panel() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2024-01-02", periods=420)
    rows = []
    for number in range(20):
        price = 10.0 + number * 0.1
        for index, date in enumerate(dates):
            price *= 1.0 + rng.normal(0.0002, 0.01)
            rows.append({
                "trade_date": date, "symbol": f"{number:06d}",
                "raw_open": price * (1.0 + rng.normal(0, 0.002)), "raw_close": price,
                "industry": f"I{number % 4}", "circ_mv": 1e9 * (1 + number / 10),
                "market_beta": 0.8 + number * 0.02, "adv20_cny": 1e8,
                "amount": 5e7, "benchmark_close": 1000 + index * 0.1,
                "eligible_universe": True,
            })
    return pd.DataFrame(rows)


def test_both_t2130_research_identities_run_the_same_full_chain():
    panel = _synthetic_panel()
    for strategy_id in ("smart_beta_v1_t2130", "pure_alpha_residual_v1_t2130"):
        result = run_t2130_pipeline(panel, strategy_id=strategy_id, n_permutations=19)
        assert result["status"] == "RESEARCH_COMPLETE"
        assert result["decision_contract_id"] == "ashare_t2130_t1_v1"
        assert result["selection"]["status"] == "PASS"
        assert result["formal"] is False and result["capital_cny"] == 0.0
        assert not result["orders"].empty and not result["nav"].empty
        assert set(result["fills"].get("canonical_kernel_id", [])) == {"ashare_canonical_economic_kernel"}
