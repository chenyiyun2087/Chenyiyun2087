from dataclasses import dataclass


@dataclass(slots=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003
    slippage_bps: float = 5.0
    lot_size: int = 1
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    min_commission_cny: float = 5.0
