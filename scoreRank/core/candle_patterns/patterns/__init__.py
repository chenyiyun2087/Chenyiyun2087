"""形态识别层子包。

- standard: pandas_ta_classic 62 个标准蜡烛图形态
- nison: 日本蜡烛图传统形态（自研校准）
- ashare: A股特殊K线（涨停/炸板/一字板/缩量反抽 等）
"""

from .standard import detect_standard_patterns
from .nison import detect_nison_patterns
from .ashare import detect_ashare_patterns

__all__ = [
    "detect_standard_patterns",
    "detect_nison_patterns",
    "detect_ashare_patterns",
]
