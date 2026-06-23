#!/usr/bin/env python3
"""生成导数框架分析提示词 — 将 Chenyiyun2087 独有的形态/策略/风控结论注入到
AShareDataCenter 的分析指令中，输出可直接执行的 Python 脚本。"""

from datetime import date

STOCKS = [
    ("300285.SZ", "300285", "国瓷材料", "陶瓷", 96.02, "+14.05%", "+43.48%", "炸板+大阳线"),
    ("301217.SZ", "301217", "铜冠铜箔", "元器件", 94.68, "+10.08%", "+56.27%", "炸板+连跌后首阳"),
    ("600392.SH", "600392", "盛和资源", "小金属", 94.52, "+10.01%", "+25.29%", "涨停"),
    ("300857.SZ", "300857", "协创数据", "IT设备", 94.20, "+4.03%", "+27.16%", "炸板"),
    ("600378.SH", "600378", "昊华科技", "化工原料", 93.94, "+2.13%", "+1.99%", "十字星+炸板"),
]

RESEARCH_STOCKS = [
    ("002741.SZ", "002741", "光华科技"),
    ("300373.SZ", "300373", "扬杰科技"),
    ("300260.SZ", "300260", "新莱应材"),
    ("002600.SZ", "002600", "领益智造"),
    ("002436.SZ", "002436", "兴森科技"),
]

PATTERN_ENGINES = {
    "看多形态": [
        "box_breakout_v1          — 放量突破箱体",
        "bullish_engulfing_support_v1 — 看涨吞没+支撑确认",
        "hammer_support_v1        — 锤子线+支撑确认",
        "morning_star_support_v1  — 晨星+支撑确认",
        "double_bottom_neckline_breakout_v1 — 双底颈线突破",
        "cup_handle_breakout_v1   — 杯柄形态突破",
        "triangle_breakout_volume_v1 — 三角形放量突破",
        "bullish_divergence_v1    — 量价底背离",
    ],
    "看空形态": [
        "bearish_divergence_v1    — 量价顶背离",
        "top_exhaustion_volume_v1 — 顶部放量衰竭",
        "shooting_star_volume_v1  — 射击之星+量能异常",
        "evening_star_volume_v1   — 黄昏之星+量能异常",
        "fake_breakout_confirmed_v1 — 假突破确认",
    ],
    "A股特有信号": [
        "limit_up                 — 涨停",
        "broken_limit_up          — 炸板（涨停打开）",
        "limit_down               — 跌停",
        "first_green_after_decline — 连跌后首阳",
    ],
}

DUAL_DIVERGENCE = {
    "date": "2026-06-18",
    "overlap": "0%（完全无重合）",
    "divergence": "强分歧 ⚠️",
    "consistency": 0.16,
    "production": "国瓷材料/铜冠铜箔/盛和资源/协创数据/昊华科技",
    "research": "光华科技/扬杰科技/新莱应材/领益智造/兴森科技",
    "signal": "两策略选股逻辑根本不同，可能处于风格转换期",
}

HEALTH_RULES = {
    "GREEN": "正常生成订单",
    "YELLOW": "正常生成，飞书标记需人工确认",
    "RED": "禁止新开仓，仅卖出/持仓维护",
    "STALE": "健康数据>1交易日陈旧，同RED处理",
}

CIRCUIT_BREAKERS = [
    ("-8% / 5日", "CAUTION — 禁止提高仓位，人工复核"),
    ("-15% / 20日", "DEFENSIVE — 降至防守仓位"),
    ("-25% 峰值回撤", "FREEZE_BUY — 冻结新开仓，复核数据/执行"),
    ("-30% 峰值回撤", "HARD_STOP — 停止策略，事故复盘"),
    ("3日连续数据延迟", "FREEZE_BUY"),
    ("2日连续成交偏差", "FREEZE_BUY"),
]


def generate():
    print('#!/usr/bin/env python3')
    print('"""')
    print('Chenyiyun2087 → AShareDataCenter 导数框架分析')
    print(f'生成日期: {date.today().isoformat()}')
    print()
    print('包含来自 Chenyiyun2087 的增强上下文：')
    print('  - 蜡烛图形态预识别结果')
    print('  - 双策略分歧分析')
    print('  - 健康状态门控规则')
    print('  - 熔断阈值参考')
    print('"""')
    print()
    print('STOCKS = [')
    for ts, code, name, ind, score, ret1, ret5, candle in STOCKS:
        print(f'    ("{ts}", "{code}"),  # {name} {ind} score={score} 1D={ret1} 5D={ret5} 蜡烛图={candle}')
    print(']')
    print()
    print(f'# 双策略分歧背景: {DUAL_DIVERGENCE["signal"]}')
    print(f'# 生产策略Top5: {DUAL_DIVERGENCE["production"]}')
    print(f'# 研究策略Top5: {DUAL_DIVERGENCE["research"]}')
    print(f'# 重叠度: {DUAL_DIVERGENCE["overlap"]}  一致性: {DUAL_DIVERGENCE["consistency"]}')
    print()
    print(f'# Chenyiyun2087 蜡烛图形态引擎预识别:')
    for family, patterns in PATTERN_ENGINES.items():
        print(f'#   {family}:')
        for p in patterns:
            print(f'#     {p}')
    print()
    print('# 熔断参考阈值:')
    for trigger, action in CIRCUIT_BREAKERS:
        print(f'#   {trigger} → {action}')
    print()
    print("""
if __name__ == "__main__":
    # 替换 derivative_framework_analysis.py 中第95-121行的 STOCKS，
    # 运行: PYTHONPATH=. python scripts/analysis/derivative_framework_analysis.py
    main()
""")


if __name__ == "__main__":
    generate()
