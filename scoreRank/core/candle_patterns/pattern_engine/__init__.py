"""形态引擎：分层模式识别模块。

架构：
1. ohlcv_features.py      — K线基础特征
2. volume_features.py     — 成交量/换手率/OBV/MFI
3. consolidation.py       — 平台/箱体/盘整检测
4. candlestick_confirm.py — 蜡烛图确认信号
5. top_exhaustion.py      — 顶部风险信号
6. double_bottom.py       — 双底/三重底突破
7. triangle_breakout.py   — 三角形突破
8. cup_handle.py          — 杯柄形态
9. fake_breakout.py       — 假突破检测
10. divergence.py          — 量价背离信号
11. signal_registry.py     — 信号注册、统一输出格式
"""

from .signal_registry import evaluate_all_patterns, PatternSignal, pattern_registry

# 导入各模块触发 @register 装饰器注册
from . import consolidation         # noqa: F401  — box_breakout_v1
from . import candlestick_confirm   # noqa: F401  — bullish_engulfing_support_v1, hammer_support_v1, morning_star_support_v1
from . import top_exhaustion        # noqa: F401  — top_exhaustion_volume_v1, shooting_star_volume_v1, evening_star_volume_v1
from . import double_bottom         # noqa: F401  — double_bottom_neckline_breakout_v1
from . import triangle_breakout     # noqa: F401  — triangle_breakout_volume_v1
from . import cup_handle            # noqa: F401  — cup_handle_breakout_v1
from . import fake_breakout         # noqa: F401  — fake_breakout_confirmed_v1
from . import divergence            # noqa: F401  — bearish_divergence_v1, bullish_divergence_v1
from . import volume_features       # noqa: F401
from . import ohlcv_features        # noqa: F401

__all__ = [
    "evaluate_all_patterns",
    "PatternSignal",
    "pattern_registry",
]
