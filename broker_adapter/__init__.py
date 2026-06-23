"""Broker Adapter — 券商接入层。

职责：将 Chenyiyun2087 的订单意图转化为券商委托，并回写成交/持仓。
QMT 不能直接触发策略、绕过风险检查或修改仓位真相。

当前支持：
  - QMT（占位，待 QMT API 就绪后实现）
  - CSV（人工 CSV 导入，已通过 live_tracker 支持）
"""

from broker_adapter.qmt_adapter import QMTAdapter

__all__ = ["QMTAdapter"]
