from dataclasses import dataclass


@dataclass(slots=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003
    slippage_bps: float = 5.0
    lot_size: int = 1
