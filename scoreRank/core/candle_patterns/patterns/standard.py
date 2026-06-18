"""第一层：标准蜡烛图形态（基于 pandas_ta_classic）。

pandas_ta_classic 的 cdl_pattern 返回值约定：
    100  = 看多形态
    -100 = 看空形态
    0    = 无
（部分形态可能有 200/-200，统一按 abs >= 100 判定命中）

我们只取"最后一根 K 线"位置命中的形态，并映射为统一的 key + 中文名 + 多空属性。
"""

from __future__ import annotations

import pandas as pd

from ..utils import get_logger

logger = get_logger("patterns.standard")


# pandas_ta_classic 的 CDL* 列名 → (英文key, 中文名, 方向 long/short)
# 只列常见且实用的形态，避免输出过多噪声
PATTERN_MAP: dict[str, tuple[str, str, str]] = {
    "CDLHAMMER": ("hammer", "锤头线", "long"),
    "CDLINVERTEDHAMMER": ("inverted_hammer", "倒锤头线", "long"),
    "CDLDRAGONFLYDOJI": ("dragonfly_doji", "蜻蜓十字", "long"),
    "CDLDOJI": ("doji", "十字星", "neutral"),
    "CDLSPINNINGTOP": ("spinning_top", "纺锤顶", "neutral"),
    "CDLMARUBOZU": ("marubozu", "光头光脚", "neutral"),
    "CDLENGULFING": ("engulfing", "吞没形态", "neutral"),
    "CDLHARAMI": ("harami", "孕线", "neutral"),
    "CDLPIERCING": ("piercing", "刺穿线", "long"),
    "CDLDARKCLOUDCOVER": ("dark_cloud_cover", "乌云盖顶", "short"),
    "CDLSHOOTINGSTAR": ("shooting_star", "射击之星", "short"),
    "CDLHANGINGMAN": ("hanging_man", "上吊线", "short"),
    "CDLGRAVESTONEDOJI": ("gravestone_doji", "墓碑十字", "short"),
    "CDLMORNINGSTAR": ("morning_star", "晨星", "long"),
    "CDLEVENINGSTAR": ("evening_star", "黄昏星", "short"),
    "CDL3WHITESOLDIERS": ("three_white_soldiers", "红三兵", "long"),
    "CDL3BLACKCROWS": ("three_black_crows", "三只乌鸦", "short"),
    "CDLTWEEZERTOP": ("tweezer_top", "镊子顶", "short"),
    "CDLTweezerBottom": ("tweezer_bottom", "镊子底", "long"),
    "CDLABANDONEDBABY": ("abandoned_baby", "弃婴", "neutral"),
    "CDLMORNINGDOJISTAR": ("morning_doji_star", "十字晨星", "long"),
    "CDLEVENINGDOJISTAR": ("evening_doji_star", "十字黄昏星", "short"),
    "CDL3INSIDE": ("three_inside", "三内升/降", "neutral"),
    "CDL3OUTSIDE": ("three_outside", "三外升/降", "neutral"),
    "CDLHIKKAKE": ("hikkake", "Hikkake形态", "neutral"),
    "CDLADVANCEBLOCK": ("advance_block", "大敌当前", "short"),
    "CDLSTALLEDPATTERN": ("stalled_pattern", "停顿形态", "short"),
    "CDLRISEFALL3METHODS": ("rise_fall_3_methods", "上升/下降三法", "neutral"),
}

# 各形态的实战意义解释
PATTERN_EXPLANATIONS: dict[str, str] = {
    "hammer":
        "锤头线是经典底部反转信号。下降趋势末端出现，表明空方一度打压至低点后多方强力收复，"
        "下影线长度至少为实体的2倍。需次日阳线确认，配合放量更可靠。",
    "inverted_hammer":
        "倒锤头线是潜在底部反转信号。下降趋势中出现，上影线长说明多方开始试探上攻，"
        "虽收在低处但空方力量衰竭。需次日高开阳线确认。",
    "dragonfly_doji":
        "蜻蜓十字是强烈底部反转信号。开盘收盘在同一价位附近，下影线极长，"
        "说明空方多次打压均被多方顽强拉回，趋势可能逆转。",
    "doji":
        "十字星表示多空力量均衡，市场进入犹豫期。上升趋势后出现可能见顶回落，"
        "下降趋势后出现可能触底反弹，高成交量十字星信号更强。",
    "spinning_top":
        "纺锤顶实体较小，多空拉锯明显。上升趋势中出现表示上涨动力衰减，"
        "下降趋势中出现表示下跌动能减弱，通常预示趋势可能放缓或进入震荡。",
    "marubozu":
        "光头光脚K线表示一方完全主导市场。光头光脚阳线说明多方强势逼空，"
        "次日大概率延续上涨；光头光脚阴线说明空方碾压，后市继续看跌。",
    "engulfing":
        "吞没形态是强烈反转信号。阳包阴：多方完全收复前日失地，底部反转信号，"
        "实体越大信号越强；阴包阳：空方吞噬前日涨幅，顶部反转信号，需警惕。",
    "harami":
        "孕线是趋势减弱信号。大实体后紧跟小实体，表明原趋势动能不足，"
        "市场可能进入整理或反转。需后续K线方向确认。",
    "piercing":
        "刺穿线是看涨反转信号。下降趋势中一根阴线后次日跳空低开但收于前日中点之上，"
        "说明空方攻势被多方强力反击，底部获得支撑。",
    "dark_cloud_cover":
        "乌云盖顶是强烈见顶信号。上升趋势中一根阳线后次日高开低走收于前日中点以下，"
        "乌云盖顶越深看跌信号越强，配合成交量放大更可靠。",
    "shooting_star":
        "射击之星是顶部反转信号。上升趋势末端出现，上影线至少为实体的2倍，"
        "开盘后冲高但遭到空方打压回落，多头冲高被套，见顶概率大。",
    "hanging_man":
        "上吊线是顶部反转信号。形态与锤头相同但出现在上升趋势中，"
        "下影线长说明高位有巨量抛盘涌出，虽然收盘守住但上攻动能已衰竭。",
    "gravestone_doji":
        "墓碑十字是强烈见顶信号。开盘收盘在最低点附近，上影线极长，"
        "说明多方全力拉升但被空方彻底打压至最低价，常用于判断波段顶部。",
    "morning_star":
        "晨星是强烈底部反转信号。由阴线、星线（十字/锤头）、阳线三根K线组成，"
        "第三根阳线收复第一根阴线的一半以上时反转信号确认，底部确立概率大。",
    "evening_star":
        "黄昏星是强烈顶部反转信号。由阳线、星线（十字/锤头）、阴线三根K线组成，"
        "第三根阴线跌破第一根阳线一半以下时见顶信号确认，宜减仓。",
    "three_white_soldiers":
        "红三兵是强势上涨延续信号。连续三根中小阳线稳步推高，收盘价逐日上升，"
        "说明多方稳步进攻，若在上涨初期出现则趋势大概率延续。",
    "three_black_crows":
        "三只乌鸦是强势下跌延续信号。连续三根中小阴线步步走低，收盘价逐日下降，"
        "说明空方持续施压，若在高位出现则趋势大概率转空。",
    "tweezer_top":
        "镊子顶是双顶反转信号。连续两根K线在相同高点价位受阻，"
        "说明该价位有强大抛压，上升趋势可能受阻或形成双顶形态。",
    "tweezer_bottom":
        "镊子底是双底反转信号。连续两根K线在相同低点价位获得支撑，"
        "说明该价位有强劲买盘，下降趋势可能止跌或形成双底形态。",
    "abandoned_baby":
        "弃婴形态是强烈反转信号。由三根K线组成，中间星线跳空与两侧K线形成缺口，"
        "代表趋势彻底断裂，常出现在关键转折点，信号极为可靠但较罕见。",
    "morning_doji_star":
        "十字晨星是底部反转信号。与晨星类似但中间为十字星，"
        "第三根阳线确认后底部反转概率更高，十字星体现了多空力量的转折。",
    "evening_doji_star":
        "十字黄昏星是顶部反转信号。与黄昏星类似但中间为十字星，"
        "第三根阴线确认后见顶信号更强，高位出现应格外警惕。",
    "advance_block":
        "大敌当前是看跌信号。连续三根阳线但每根实体逐步缩小、上影线加长，"
        "说明多方上攻力竭，空方开始反扑，短期可能面临调整。",
    "stalled_pattern":
        "停顿形态是看跌信号。连续阳线后出现一根小实体或十字星，"
        "说明上涨势头停滞，市场在当前位置出现多空分歧。",
    "rise_fall_3_methods":
        "上升/下降三法是趋势中继形态。上升途中短暂回调或下降途中短暂反弹后"
        "原趋势继续，属于持续信号而非反转信号，不必急于反向操作。",
    "three_inside":
        "三内升/降是趋势减弱信号。大实体后连续两根小实体孕线，"
        "说明原趋势动能正在丧失，市场可能进入整理阶段。",
    "three_outside":
        "三外升/降是强势方向信号。阳包阴后跟随阳线确认看涨，"
        "或阴包阳后跟随阴线确认看跌，实体越大型号越可靠。",
    "hikkake":
        "Hikkake形态是短线反转陷阱信号。先向一个方向假突破后迅速反向运行，"
        "适合短线交易者捕捉反向行情，可靠性中等。",
    "large_bullish":
        "大阳线实体显著大于近期均值，表明多方强势主导。突破关键压力位时出现"
        "具有技术意义，但连续大涨后出现需警惕多头情绪过热。",
    "large_bearish":
        "大阴线实体显著大于近期均值，表明空方强势主导。跌破关键支撑时出现"
        "属于破位信号，如伴随放量则后续大概率继续下跌。",
    "bullish_engulfing":
        "阳包阴是强烈看涨反转信号。多方完全收复前日失地，收盘高于前日开盘价，"
        "实体覆盖越彻底信号越可靠，关注次日是否站稳确认。",
    "bearish_engulfing":
        "阴包阳是强烈看跌反转信号。空方吞噬前日全部涨幅，收盘低于前日开盘价，"
        "高位出现应果断降低仓位，成交量放大时信号加强。",
    # nison 补充解释（key 与 PATTERN_MAP 的差异）
    "hammer_like":
        "锤头/上吊线类形态，下影线长而上影线短。出现在下降趋势末端为锤头线（底部反转信号），"
        "出现在上涨末端则为上吊线（顶部反转信号），需结合趋势位置判断。",
    "shooting_star_like":
        "射击之星类形态，上影线长至少为实体的2倍。高位出现说明冲高遭空方猛烈打压，"
        "多头信心受挫，顶部反转概率较高。",
    # ashare 特殊信号解释
    "one_word_limit_up":
        "一字涨停板，开盘即封死涨停价，全天无成交（或极少量），说明买盘极度旺盛，"
        "空方毫无抵抗。次日大概率高开或继续涨停，但连续一字板后开板风险急剧增大。",
    "one_word_limit_down":
        "一字跌停板，开盘即封死跌停价，全天无法卖出。说明市场恐慌性抛售，流动性枯竭，"
        "可能有利空消息未释放，次日大概率继续下跌，不宜抄底。",
    "limit_up":
        "涨停板，涨幅达到上限。多方强势主导，买盘踊跃，"
        "若在关键位置突破时涨停则趋势确认，若已连续大涨则需警惕次日高开低走。",
    "limit_down":
        "跌停板，跌幅达到下限。空方强势打压，卖盘密集，"
        "若在关键支撑位跌停则可能破位，宜等恐慌释放后企稳再判断。",
    "broken_limit_up":
        "炸板（涨停打开）是危险信号。盘中一度涨停后抛盘汹涌打开涨停，"
        "说明主力趁机出货或市场情绪逆转，当日追高者全部被套，次日大概率低开。",
    "re_seal_limit_up":
        "开板回封是强势信号。昨日炸板后今日重新封板，说明经过换手后买盘重新主导，"
        "洗盘意图明显，后续仍有上涨空间，但需注意成交量是否持续放大。",
    "long_upper_shadow_pullback":
        "冲高回落长上影线，说明盘中多方曾强力拉升但遭空方高位打压，"
        "收盘回落至低位。这是典型的顶部试盘或出货信号，尤其在连续上涨后出现需警惕见顶。",
    "high_vol_long_bearish":
        "高位放量长阴是强烈见顶/出货信号。价格处于相对高位，当日放量暴跌，"
        "说明主力集中出逃，散户接盘。成交量越大、阴线实体越长，顶部信号越可靠。",
    "weak_rebound_low_volume":
        "缩量反抽是下跌中继信号。连跌后出现反弹但量能明显不足，"
        "说明反弹缺乏资金认可，属于技术性超跌反抽而非反转，后续大概率继续下行。",
    "first_green_after_decline":
        "连跌后首根阳线可能是止跌信号。连续多日阴跌后首次收阳，"
        "说明空方力量阶段性衰竭，但需次日放量阳线确认底部，否则可能只是下跌中继。",
    "recovered_after_limit_down":
        "跌停后3日内收复跌停实体，说明恐慌情绪被快速消化，"
        "多头迅速回补，属于强势修复信号，后续有望企稳反弹。",
    "unrecovered_limit_down":
        "跌停后3日内未能收复跌停开盘价，说明空方持续压制，"
        "市场信心尚未恢复，弱势格局延续，短期不宜介入。",
    "bullish_failure":
        "反包失败（阳线后紧接阴线跌破阳线中点），说明反弹被空方压制，"
        "多头反攻无力，市场仍然由空方主导，后市大概率继续下跌。",
}


def get_explanation(key: str) -> str:
    """获取形态 key 对应的中文解释文本。"""
    return PATTERN_EXPLANATIONS.get(key, "")


def get_pattern_name(key: str) -> str:
    """通过英文 key 反查中文名。"""
    for _col, (k, name, _dir) in PATTERN_MAP.items():
        if k == key:
            return name
    return key


def detect_standard_patterns(df: pd.DataFrame) -> list[dict]:
    """对 df 最后一根 K 线做标准形态识别。

    返回 [{key, name, direction, value}]，方向已根据正负号修正
    （engulfing/harami 等中性形态会带上 long/short）。
    """
    if df is None or len(df) < 5:
        return []

    # pandas_ta_classic 已在 patterns/__init__.py 中静默 import
    try:
        import pandas_ta_classic as ta  # noqa: F401 — 缓存命中，不会重复加载
    except ImportError:
        logger.warning("pandas_ta_classic 未安装，跳过标准形态识别")
        return []

    work = df.copy()
    try:
        # cdl_pattern accessor 懒加载蜡烛图子模块，每个子模块在首次加载时
        # 都会 print "Please install TA-Lib..."（直接写 fd）。
        # contextlib/ sys.stdout 替换均无效，必须 os.dup2 重定向 fd 1+2。
        import os
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        real_fd1 = os.dup(1)
        real_fd2 = os.dup(2)
        try:
            os.dup2(devnull_fd, 1)
            os.dup2(devnull_fd, 2)
            work.ta.cdl_pattern(name="all", append=True)
        finally:
            os.dup2(real_fd1, 1)
            os.dup2(real_fd2, 2)
            os.close(devnull_fd)
            os.close(real_fd1)
            os.close(real_fd2)
    except Exception as e:  # noqa: BLE001
        logger.warning("cdl_pattern 调用失败: %s", e)
        return []

    last = work.iloc[-1]
    results: list[dict] = []

    for col, (key, name, direction) in PATTERN_MAP.items():
        if col not in work.columns:
            continue
        val = last[col]
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if pd.isna(val) or abs(val) < 100:
            continue

        # 修正方向：正值偏多，负值偏空
        if direction == "neutral":
            final_dir = "long" if val > 0 else "short"
        else:
            final_dir = direction

        ex = PATTERN_EXPLANATIONS.get(key, "")
        results.append({"key": key, "name": name, "direction": final_dir, "value": int(val), "explanation": ex})

    return results
