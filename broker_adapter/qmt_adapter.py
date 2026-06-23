"""QMT 券商适配器（占位）。

QMT 接入边界：
  - QMT 只能通过本 Adapter 与系统交互
  - QMT 不能直接触发策略、绕过风险检查、修改仓位真相
  - 所有 QMT 回报（委托/成交/撤单）只能进入 BrokerOrder 与 Fill

当前状态：占位实现。等 QMT API 环境就绪后实现 submit_order/cancel_order/query_orders/query_fills。
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
    """QMT 券商适配器 — 占位实现，待 QMT API 就绪。"""

    def __init__(self, account_id: str = "default"):
        self.account_id = account_id
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """连接 QMT。当前占位。"""
        logger.warning("QMT adapter: connect() not implemented — placeholder")
        return False

    def get_account_snapshot(self) -> AccountSnapshot | None:
        """获取账户快照。"""
        logger.warning("QMT adapter: get_account_snapshot() not implemented")
        return None

    def get_positions(self) -> list[Position]:
        """获取当前持仓。"""
        logger.warning("QMT adapter: get_positions() not implemented")
        return []

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
        logger.warning("QMT adapter: submit_order() not implemented — placeholder")
        return OrderResult(
            broker_order_id=None,
            status="ERROR",
            message="QMT adapter not connected",
        )

    def cancel_order(self, broker_order_id: str) -> OrderResult:
        """撤单。"""
        logger.warning("QMT adapter: cancel_order() not implemented")
        return OrderResult(
            broker_order_id=broker_order_id,
            status="ERROR",
            message="QMT adapter not connected",
        )

    def query_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """查询委托状态。"""
        logger.warning("QMT adapter: query_orders() not implemented")
        return []

    def query_fills(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """查询成交记录。"""
        logger.warning("QMT adapter: query_fills() not implemented")
        return []
