"""Forward Shadow v5.5 end-to-end chain test.

seal (T 17:00) -> precommit (T+1 09:25) -> reconcile (T+1 09:35)

The full morning chain consumes ONLY the SEALED package: precommit
materializes lot-adjusted BUY orders; reconcile fills them at T+1 open
with directional gates (limit-UP blocks buys).  Everything runs in a
temp zone — no DB, no repo paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path("/Volumes/extension/projects/Chenyiyun2087")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.build_daily_alpha_signal_package import seal_signal_package  # noqa: E402
from scripts.ops.run_daily_shadow import (  # noqa: E402
    precommit,
    reconcile_from_package,
)

SIGNAL_DATE, EXEC_DATE = "2026-08-05", "2026-08-06"


def _seal_package(tmp: Path, portfolios: dict) -> Path:
    universe = pd.DataFrame({
        "trade_date": [SIGNAL_DATE] * 4,
        "symbol": ["600001", "600002", "600003", "600004"],
        "is_listed": [1, 1, 1, 1], "is_st": [0, 0, 0, 0],
        "is_suspended": [0, 0, 0, 0], "limit_status": ["NORMAL"] * 4,
        "security_status_transition": ["NORMAL"] * 4,
        "tradeable": [True] * 4,
    })
    factors = pd.DataFrame({
        "trade_date": [SIGNAL_DATE] * 4,
        "symbol": ["600001", "600002", "600003", "600004"],
        "score": [0.4, 0.3, 0.2, 0.1],
    })
    pkg = tmp / "packages" / SIGNAL_DATE
    seal_signal_package(
        pkg, signal_date=SIGNAL_DATE, execution_date=EXEC_DATE,
        universe=universe, factor_values=factors, scores=factors,
        target_portfolios=portfolios,
        data_quality={"signal_date": SIGNAL_DATE, "bar_dates": 30},
        input_manifest={"signal_date": SIGNAL_DATE,
                        "source_snapshot_shas": {}, "pit_contract_sha": None},
        git_info={"git_commit_sha": "test", "worktree_clean": True})
    return pkg


def _prices(tmp: Path) -> Path:
    # T-day close 10.00; T+1 opens: 600001 +10% (limit-up -> blocked),
    # others normal.
    prices = pd.DataFrame({
        "trade_date": [SIGNAL_DATE, SIGNAL_DATE, SIGNAL_DATE, SIGNAL_DATE,
                       EXEC_DATE, EXEC_DATE, EXEC_DATE, EXEC_DATE],
        "symbol": ["600001", "600002", "600003", "600004"] * 2,
        "open": [10.00, 10.00, 10.00, 10.00, 11.00, 10.10, 10.05, 10.02],
        "raw_open": [10.00, 10.00, 10.00, 10.00, 11.00, 10.10, 10.05, 10.02],
        "raw_pre_close": [10.00] * 8,
        "raw_close": [10.00, 10.00, 10.00, 10.00, 11.00, 10.10, 10.05, 10.02],
    })
    p = tmp / "prices.parquet"
    prices.to_parquet(p, index=False)
    return p


def test_full_chain_seal_precommit_reconcile(tmp_path):
    portfolios = {
        "C1": pd.DataFrame({
            "symbol": ["600001", "600002", "600003", "600004"],
            "score": [0.4, 0.3, 0.2, 0.1], "rank": [1, 2, 3, 4],
            "weight_before_overlay": [0.25] * 4,
            "target_weight": [0.25] * 4,
            "risk_overlay": ["none"] * 4,
        }),
    }
    pkg = _seal_package(tmp_path, portfolios)
    prices = _prices(tmp_path)

    # ── precommit (T+1 09:25) ──
    out = precommit(EXEC_DATE, packages_zone=pkg.parent,
                    execution_zone=tmp_path / "exec",
                    prices_path=prices)
    assert out["precommitted"] == 4
    orders = json.loads(
        (tmp_path / "exec" / EXEC_DATE / "orders.json").read_text())
    assert all(o["state"] == "ORDER_PRECOMMITTED" for o in orders)
    # Lot-adjusted 100-share units.
    assert all(o["lot_adjusted_shares"] % 100 == 0 for o in orders)
    # 25% of 500K = 125,000 CNY @ 10.00 -> 12,500 shares -> 12,500 (lot ok).
    assert orders[0]["target_shares"] == 12500

    # ── reconcile (T+1 09:35) — 600001 opens at limit-up -> blocked ──
    out2 = reconcile_from_package(EXEC_DATE,
                                  execution_zone=tmp_path / "exec",
                                  prices_path=prices)
    assert out2["reconciled"] == 3 and out2["failed"] == 1
    orders2 = json.loads(
        (tmp_path / "exec" / EXEC_DATE / "orders.json").read_text())
    by_symbol = {o["symbol"]: o for o in orders2}
    assert by_symbol["600001"]["fill_status"] == "BLOCKED"
    assert by_symbol["600001"]["rejection_reason"] == "limit_up_block"
    assert by_symbol["600002"]["fill_status"] == "FILLED"
    assert by_symbol["600002"]["state"] == "BUY_FILLED"

    # ── idempotent second reconcile: nothing pending ──
    out3 = reconcile_from_package(EXEC_DATE,
                                  execution_zone=tmp_path / "exec",
                                  prices_path=prices)
    assert out3["reconciled"] == 0


def test_precommit_requires_sealed_package(tmp_path):
    with pytest.raises(RuntimeError, match="no SEALED package"):
        precommit(EXEC_DATE, packages_zone=tmp_path / "empty",
                  execution_zone=tmp_path / "exec")
