"""诊断引擎：串联四层识别 + 评分 + 组合信号规则，输出 DiagnosisResult。"""

from __future__ import annotations

import pandas as pd
from ..patterns.standard import detect_standard_patterns
from ..patterns.nison import detect_nison_patterns
from ..patterns.ashare import detect_ashare_patterns
from ..context.ma import analyze_trend
from ..context.trend import detect_trend_state
from ..context.volume import analyze_volume
from ..context.levels import analyze_levels
from ..context.consolidation import detect_consolidation
from ..models import DiagnosisResult
from ..patterns.base import row_to_candle
from ..pattern_engine import evaluate_all_patterns, PatternSignal
from ..utils import get_logger
from .scorer import score_diagnosis, build_natural_language

logger = get_logger("engine")


class DiagnoseEngine:
    """单股四层诊断引擎。"""

    def __init__(self, lookback_bars: int | None = None) -> None:
        from ..utils import load_settings
        if lookback_bars is None:
            lookback_bars = load_settings().get("scanner", {}).get("lookback_bars", 120)
        self.lookback_bars = lookback_bars

    def diagnose(self, symbol: str, name: str = "", df: pd.DataFrame | None = None,
                 source: str | None = None) -> DiagnosisResult:
        """诊断单只股票。

        Args:
            symbol: 6位股票代码
            name: 股票名称（可选，自动获取）
            df: 已有日K DataFrame。本内置版本不主动取数，避免生产链路依赖外部项目或网络。
            source: 保留兼容参数，当前不使用。
        """
        if df is None:
            logger.warning("%s 未传入日K数据，无法诊断", symbol)
            return DiagnosisResult(symbol=symbol, name=name, diagnosis="未传入日K数据，无法诊断")
        if df is None or df.empty or len(df) < 5:
            logger.warning("%s 数据不足，无法诊断", symbol)
            return DiagnosisResult(symbol=symbol, name=name, diagnosis="数据不足，无法诊断")

        if not name:
            name = ""

        last_row = df.iloc[-1]
        last_candle = row_to_candle(last_row)
        date = str(last_row.get("date", ""))

        # 第一层：标准形态（pandas_ta_classic）
        try:
            std_patterns = detect_standard_patterns(df)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s 标准形态识别异常: %s", symbol, e)
            std_patterns = []

        # 第一层补充：nison 传统形态
        try:
            nison_patterns = detect_nison_patterns(df)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s nison 形态识别异常: %s", symbol, e)
            nison_patterns = []

        # 形态合并 + 去重（按 key）
        all_patterns = std_patterns + nison_patterns
        seen_keys: set[str] = set()
        merged_patterns: list[dict] = []
        for p in all_patterns:
            if p["key"] in seen_keys:
                continue
            seen_keys.add(p["key"])
            merged_patterns.append(p)

        # 第二层：A股特殊K线
        is_st = "ST" in name.upper() if name else False
        try:
            ashare_signals = detect_ashare_patterns(df, symbol=symbol, is_st=is_st)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s A股形态识别异常: %s", symbol, e)
            ashare_signals = []

        # 第三层：上下文（基础 + 增强）
        trend = analyze_trend(df)
        volume = analyze_volume(df)
        levels = analyze_levels(df)
        trend_state = detect_trend_state(df)
        consolidation = detect_consolidation(df)

        # 第四层：评分
        sr = score_diagnosis(merged_patterns, ashare_signals, trend, volume, levels)
        diagnosis_text = build_natural_language(merged_patterns, ashare_signals, trend, volume, levels, sr)

        # 第五层：形态引擎（pattern_engine）— 注册式调度
        pattern_signals = evaluate_all_patterns(
            df, trend_state, volume, levels, merged_patterns, ashare_signals, consolidation
        )
        triggered_patterns = [s for s in pattern_signals if s.signal_state == "pass"]
        # PatternSignal → dict
        signal_dicts = [s.to_dict() for s in pattern_signals]

        # 组合信号解释追加到诊断原文
        signal_texts = []
        for s in triggered_patterns:
            if s.reasons:
                signal_texts.append(f"[{s.pattern_id}] " + "；".join(s.reasons[:3]))
        if signal_texts:
            diagnosis_text += "\n" + "\n".join(signal_texts)

        # 最新 extra：加入 volume_features 数据
        extra_dict = {
            "volume_ratio_5": volume.get("volume_ratio_5"),
            "volume_ratio_20": volume.get("volume_ratio_20"),
            "volume_ma20": volume.get("volume_ma20"),
            "turnover": volume.get("turnover"),
            "volume_quantile_60": volume.get("volume_quantile_60"),
            "turnover_quantile_60": volume.get("turnover_quantile_60"),
            "volume_trend": volume.get("volume_trend"),
            "support": levels.get("support"),
            "resistance": levels.get("resistance"),
            "mas": trend.get("mas"),
            "score_breakdown": sr.breakdown,
            "trend_state": trend_state.get("state"),
            "percentile_60d": trend_state.get("percentile_60d"),
            "roc_10": trend_state.get("roc_10"),
            "consolidation": consolidation.get("is_consolidation"),
        }

        # 添加量价背离指标（来自 volume_features）
        try:
            from ..pattern_engine.volume_features import detect_bearish_divergence, detect_bullish_divergence
            b_div = detect_bearish_divergence(df)
            bull_div = detect_bullish_divergence(df)
            extra_dict["bearish_divergence"] = b_div.get("bearish_divergence", False)
            extra_dict["bullish_divergence"] = bull_div.get("bullish_divergence", False)
        except Exception:
            pass

        result = DiagnosisResult(
            symbol=symbol,
            name=name,
            date=date,
            patterns=[p["key"] for p in merged_patterns],
            pattern_names=[p["name"] for p in merged_patterns],
            pattern_explanations=[p.get("explanation", "") for p in merged_patterns],
            ashare_signals=[s["key"] for s in ashare_signals],
            ashare_signal_names=[s["name"] for s in ashare_signals],
            ashare_explanations=[s.get("explanation", "") for s in ashare_signals],
            trend_context=trend.get("trend_context", ""),
            volume_context=volume.get("volume_context", ""),
            support_status=levels.get("support_status", ""),
            resistance_status=levels.get("resistance_status", ""),
            score=sr.score,
            risk_level=sr.risk_level,
            sentiment=sr.sentiment,
            diagnosis=diagnosis_text,
            close=last_candle.close,
            pct_chg=last_candle.pct_chg,
            signals=signal_dicts,
            extra=extra_dict,
        )
        return result
