from dataclasses import dataclass, field
from typing import Dict, Tuple


DEFAULT_WEIGHTS = {
    "s_breakout": 0.22,
    "s_trend": 0.12,
    "s_volume": 0.12,
    "s_rs": 0.12,
    "s_liquidity": 0.10,
    "s_contraction": 0.10,
}


@dataclass
class PipelineConfig:
    breakout_lookback: int = 55
    trend_short: int = 20
    trend_mid: int = 60
    trend_long: int = 120
    volume_lookback: int = 20
    rs_lookback: int = 20
    atr_period: int = 14
    bb_period: int = 20

    breakout_buy_zone_max: float = 0.05
    breakout_decay_k: float = 0.03

    min_amount_watch: float = 1e7
    min_amount_trade: float = 3e7

    trade_pct: float = 0.90
    watch_pct: float = 0.60

    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    # A-share daily limit approximation by board prefix
    board_limit: Dict[str, float] = field(
        default_factory=lambda: {
            "300": 0.20,
            "688": 0.20,
            "default": 0.10,
        }
    )


@dataclass
class BacktestConfig:
    slippage_bps: float = 10.0
    fee_bps_buy: float = 2.0
    fee_bps_sell: float = 2.0
    stamp_duty_bps_sell: float = 5.0
    min_hold_days: int = 1  # T+1 friendly
    equal_weight: bool = True
    horizons: Tuple[int, ...] = (5, 10, 20)
