"""HTTP API Adapter — 通过 HTTP 访问 AShareDataCenter 服务（未来）。

等 AShareDataCenter 独立服务化后实现。
接口与 ashare_client.py 一致，调用方无需修改。
"""
from __future__ import annotations

from datetime import date

from integration.contracts import StrategySignalBatch, RiskGateResult


class HttpApiAdapter:
    """AShareDataCenter HTTP API 适配器（占位）。"""

    def __init__(self, base_url: str = "http://localhost:8100"):
        self.base_url = base_url.rstrip("/")

    def get_strategy_signals(self, as_of_date: date) -> StrategySignalBatch | None:
        raise NotImplementedError("HTTP API adapter not yet implemented")

    def get_risk_gate(self, as_of_date: date) -> RiskGateResult | None:
        raise NotImplementedError("HTTP API adapter not yet implemented")
