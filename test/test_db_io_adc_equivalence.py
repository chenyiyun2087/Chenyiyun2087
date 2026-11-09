"""测试 db_io.fetch_bars_batch 通过 ADCClient 取数的字段契约、Fail-Closed 与研究回退。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from cross_project.execution_context import ExecutionContext
from scoreRank.core.db_io import fetch_bars_batch


def test_fetch_bars_batch_adc_client_success():
    mock_df = pd.DataFrame([
        {
            "symbol": "000001",
            "ts_code": "000001.SZ",
            "trade_date": 20261106,
            "open": 10.1,
            "high": 10.5,
            "low": 9.9,
            "close": 10.3,
            "volume": 60000.0,
            "amount": 618000.0,
        }
    ])

    mock_adc = MagicMock()
    mock_adc.data.get_daily_bars_batch.return_value = mock_df

    with patch("cross_project.adc_client.ADCClient", return_value=mock_adc):
        df = fetch_bars_batch(
            engine=None,
            symbols=["000001"],
            adj_type="qfq",
            start_date="2026-11-06",
            end_date="2026-11-06",
        )

        assert not df.empty
        assert isinstance(df["trade_date"].iloc[0], pd.Timestamp)
        assert df["symbol"].iloc[0] == "000001"
        assert df["close"].iloc[0] == 10.3
        mock_adc.data.get_daily_bars_batch.assert_called_once_with(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            adj_type="qfq",
        )


def test_fetch_bars_batch_fails_closed_in_production():
    # 正式模式下：ADCClient 抛异常时，严格 Fail-Closed 阻断，拒绝静默回退
    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("connection error")):
        with pytest.raises(RuntimeError) as exc_info:
            fetch_bars_batch(
                engine=None,
                symbols=["000001"],
                adj_type="qfq",
                start_date="2026-11-06",
                end_date="2026-11-06",
                execution_context=None,  # 默认生产模式
            )
        assert "正式策略生产取数阻断" in str(exc_info.value)
        assert "严格禁止静默回退未认证 SQL" in str(exc_info.value)


def test_fetch_bars_batch_allows_fallback_in_research_mode():
    # 研究模式下 (research_only=True)：允许回退备用 SQL 查询以供离线诊断
    mock_rows = [{
        "ts_code": "000001.SZ",
        "trade_date": 20261106,
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "volume": 50000.0,
        "amount": 510000.0,
    }]

    res_ctx = ExecutionContext.create_research(20261106, reason="OFFLINE_TEST")

    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("connection error")), \
         patch("scoreRank.core.db_io._fetch_rows", return_value=mock_rows):
        df = fetch_bars_batch(
            engine=None,
            symbols=["000001"],
            adj_type="qfq",
            start_date="2026-11-06",
            end_date="2026-11-06",
            execution_context=res_ctx,
        )
        assert not df.empty
        assert df["symbol"].iloc[0] == "000001"
        assert df["close"].iloc[0] == 10.2
