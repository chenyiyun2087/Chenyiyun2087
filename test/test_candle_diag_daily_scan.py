from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.ops.run_candle_diag_daily_scan import (
    _filter_universe_to_target_date,
    _limit_kline_to_trade_date,
    _validate_scan_report,
)


def _report(*, total=2, failed=0, dates=("2026-07-16", "2026-07-16")):
    return SimpleNamespace(
        total=total,
        failed=failed,
        results=[{"date": value} for value in dates],
    )


def test_validate_scan_report_accepts_complete_target_date():
    _validate_scan_report(_report(), "2026-07-16")


def test_validate_scan_report_rejects_any_failed_symbol():
    with pytest.raises(RuntimeError, match="1 只失败"):
        _validate_scan_report(_report(failed=1, dates=("2026-07-16",)), "2026-07-16")


def test_validate_scan_report_allows_suspended_stock_with_older_last_bar():
    _validate_scan_report(_report(dates=("2026-07-15", "2026-07-16")), "2026-07-16")


def test_validate_scan_report_rejects_future_data_date():
    with pytest.raises(RuntimeError, match="数据日期超出目标日"):
        _validate_scan_report(_report(dates=("2026-07-17", "2026-07-17")), "2026-07-16")


def test_validate_scan_report_rejects_market_not_ready_for_target_date():
    with pytest.raises(RuntimeError, match="全市场未到目标日"):
        _validate_scan_report(_report(dates=("2026-07-15", "2026-07-15")), "2026-07-16")


def test_limit_kline_to_trade_date_removes_future_bars():
    frame = pd.DataFrame({"date": ["2026-08-24", "2026-08-25", "2026-08-28"], "close": [1, 2, 3]})
    result = _limit_kline_to_trade_date(frame, "2026-08-24")

    assert result["date"].tolist() == ["2026-08-24"]


def test_filter_universe_excludes_stocks_listed_after_historical_date():
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(fetchall=lambda: [("000001.SZ",), ("920298.BJ",)])

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    class FakeDbSource:
        @staticmethod
        def _get_engine():
            return FakeEngine()

        @staticmethod
        def symbol_to_ts_code(symbol):
            return {"000001": "000001.SZ", "920298": "920298.BJ"}.get(symbol, symbol)

    symbols = pd.DataFrame({"symbol": ["000001", "920298", "688837"], "name": ["平安银行", "某北交所新股", "某科创新股"]})

    result = _filter_universe_to_target_date(symbols, "2026-09-15", FakeDbSource)

    assert result["symbol"].tolist() == ["000001", "920298"]
