import sys

import pandas as pd
import pytest

from scripts.research import compare_bs_sources


class FakeEngine:
    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


def test_missing_source_data_exits_nonzero(monkeypatch, capsys):
    engine = FakeEngine()
    frames = iter([pd.DataFrame({"stock_code": ["000001"]}), pd.DataFrame()])
    monkeypatch.setenv(
        "CHENYIYUN_DB_URL",
        "mysql+pymysql://" + "readonly:test-only@" + "localhost:3306/chenyiyun",
    )
    monkeypatch.setattr(compare_bs_sources, "create_engine", lambda _: engine)
    monkeypatch.setattr(compare_bs_sources, "load_signals", lambda *args: next(frames))
    monkeypatch.setattr(
        sys,
        "argv",
        ["compare_bs_sources.py", "--start", "20260701", "--end", "20260701"],
    )

    with pytest.raises(SystemExit) as exc_info:
        compare_bs_sources.main()

    assert exc_info.value.code == 2
    assert "no data for ml_detect_v3" in capsys.readouterr().err
    assert engine.disposed is True
