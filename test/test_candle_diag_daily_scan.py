from types import SimpleNamespace

import pytest

from scripts.ops.run_candle_diag_daily_scan import _validate_scan_report


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
