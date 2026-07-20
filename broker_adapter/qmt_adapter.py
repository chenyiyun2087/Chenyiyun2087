"""Disabled QMT boundary retained only to prevent accidental broker access.

QMT 接入边界：
  - QMT 只能通过本 Adapter 与系统交互
  - QMT 不能直接触发策略、绕过风险检查、修改仓位真相
  - 所有 QMT 回报（委托/成交/撤单）只能进入 BrokerOrder 与 Fill

当前状态：永久离线硬失败。本项目仅支持人工订单、人工成交和离线对账单。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AccountSnapshot:
    account_id: str
    total_equity: float
    available_cash: float
    frozen_cash: float
    positions_value: float
    snapshot_time: datetime


@dataclass
class Position:
    symbol: str
    shares: int
    avg_cost: float
    current_price: float
    market_value: float


@dataclass
class OrderResult:
    broker_order_id: str | None
    status: str  # SUBMITTED / REJECTED / ERROR
    message: str


class QMTAdapter:
    """Disabled adapter: every broker operation fails closed."""

    def __init__(self, account_id: str = "default"):
        self.account_id = account_id
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        raise RuntimeError("broker_api_disabled_offline_statement_only")

    def get_account_snapshot(self) -> AccountSnapshot | None:
        """获取账户快照。"""
        raise RuntimeError("broker_api_disabled_offline_statement_only")

    def get_positions(self) -> list[Position]:
        """获取当前持仓。"""
        raise RuntimeError("broker_api_disabled_offline_statement_only")

    def submit_order(
        self,
        symbol: str,
        side: str,
        price: float,
        shares: int,
        order_type: str = "LIMIT",
        signal_id: str = "",
    ) -> OrderResult:
        """
        提交委托。

        QMT 只能通过此方法接收订单。所有参数由 PreTradeRiskCheck 校验后传入。
        """
        raise RuntimeError("broker_api_disabled_manual_execution_only")

    def cancel_order(self, broker_order_id: str) -> OrderResult:
        """撤单。"""
        raise RuntimeError("broker_api_disabled_manual_execution_only")

    def query_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """查询委托状态。"""
        raise RuntimeError("broker_api_disabled_offline_statement_only")

    def query_fills(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """查询成交记录。"""
        raise RuntimeError("broker_api_disabled_offline_statement_only")
