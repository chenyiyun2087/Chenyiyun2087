import pandas as pd
import pytest

from chenyiyunSelected.local_strategy_adapter import LocalHighDividendStrategy


def test_filter_kcbj():
    src = ["688001.SH", "830001.BJ", "430001.BJ", "000001.SZ"]
    got = LocalHighDividendStrategy._filter_kcbj(src)
    assert got == ["000001.SZ"]


def test_pct_slice_ordering():
    df = pd.DataFrame(
        {
            "ts_code": ["a", "b", "c", "d"],
            "x": [1.0, 4.0, 3.0, 2.0],
        }
    )
    out = LocalHighDividendStrategy._pct_slice(df, "x", ascending=False, start=0, end=0.5)
    assert out["ts_code"].tolist() == ["b", "c"]


def test_build_daily_signals_empty(monkeypatch):
    strategy = LocalHighDividendStrategy(provider=None)  # type: ignore[arg-type]
    monkeypatch.setattr(strategy, "pick", lambda asof=None: pd.DataFrame())

    signals = strategy.build_daily_signals()
    assert list(signals.columns) == ["trade_date", "ts_code", "signal", "target_weight", "rank"]
    assert signals.empty


def test_save_daily_signals_table_name_validation():
    strategy = LocalHighDividendStrategy(provider=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        strategy.save_daily_signals(
            pd.DataFrame(
                [{"trade_date": "2026-01-01", "ts_code": "000001.SZ", "signal": "BUY", "target_weight": 1.0, "rank": 1}]
            ),
            table="bad-table;drop",
        )
