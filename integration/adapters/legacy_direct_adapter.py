"""Legacy Direct Adapter — 通过同 MySQL 实例的 SQL 查询访问 AShareDataCenter。

包装当前 run_daily.py 中的直接 sys.path hack 和 AShare 模块导入。
输出与 ashare_client.py 相同的契约接口。
"""
from __future__ import annotations

from datetime import date

from integration.contracts import StrategySignalBatch, RiskGateResult
from integration.ashare_client import fetch_strategy_signals, fetch_risk_gate


class LegacyDirectAdapter:
    """AShareDataCenter 直连适配器（SQL封装）。"""

    def __init__(self):
        self._name = "legacy_direct_adapter"

    def get_strategy_signals(self, as_of_date: date) -> StrategySignalBatch | None:
        return fetch_strategy_signals(as_of_date, use_cache=True)

    def get_risk_gate(self, as_of_date: date) -> RiskGateResult | None:
        return fetch_risk_gate(as_of_date)
