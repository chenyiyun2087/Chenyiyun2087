import pandas as pd

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
