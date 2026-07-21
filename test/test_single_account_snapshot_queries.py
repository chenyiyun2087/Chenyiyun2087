from __future__ import annotations

from datetime import date

import pytest

from scripts.ops.export_trusted_strategy_candidates import _infer_total_equity
from scripts.ops.pretrade_risk_check import check_position_limits


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Connection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None):
        self.engine.calls.append((str(statement), dict(params or {})))
        return _ScalarResult(self.engine.value)


class _Engine:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def connect(self):
        return _Connection(self)


def _assert_single_account_pit_query(engine: _Engine, target_date: date) -> None:
    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "account_id" not in sql
    assert "snapshot_date<=" in sql.replace(" ", "")
    assert "ORDER BY snapshot_date DESC LIMIT 1" in sql
    assert params == {"trade_date": target_date}


def test_candidate_nav_uses_latest_single_account_snapshot_asof_date():
    engine = _Engine(500_000)
    target_date = date(2026, 7, 20)

    assert _infer_total_equity(engine, "default", target_date) == 500_000
    _assert_single_account_pit_query(engine, target_date)


@pytest.mark.parametrize("value", [None, 0, -1])
def test_candidate_nav_rejects_missing_or_nonpositive_equity(value):
    engine = _Engine(value)

    with pytest.raises(RuntimeError, match="Cannot infer total equity"):
        _infer_total_equity(engine, "default", date(2026, 7, 20))


def test_pretrade_position_limit_uses_single_account_snapshot_asof_date():
    engine = _Engine(500_000)
    target_date = date(2026, 7, 20)

    result = check_position_limits(engine, "000001", "default", 10_000, target_date)

    assert result.passed is True
    _assert_single_account_pit_query(engine, target_date)


@pytest.mark.parametrize("value", [None, 0, -1])
def test_pretrade_position_limit_blocks_without_positive_nav(value):
    result = check_position_limits(
        _Engine(value), "000001", "default", 10_000, date(2026, 7, 20)
    )

    assert result.passed is False
    assert result.detail == "BLOCKED: account NAV unavailable"
