"""Readable display names for trusted strategy ids."""

from __future__ import annotations


STRATEGY_DISPLAY_NAMES = {
    "tiered_liquidity_then_bs_v2": "流动性分层B点进攻策略",
    "tiered_liquidity_then_bs_v2_industry_cap2": "流动性分层B点进攻策略（单行业最多2只）",
    "tiered_liquidity_then_bs_v2_industry_cap1": "流动性分层B点进攻策略（单行业最多1只）",
    "tiered_liquidity_then_bs_v2_industry_penalty_5pt": "流动性分层B点进攻策略（行业集中惩罚）",
    "baseline_full_dynamic_factor_industry_cap2": "动态因子均衡策略（单行业最多2只）",
    "baseline_full_liquidity": "纯流动性防守策略",
    "baseline_full_liquidity_shadow": "纯流动性防守策略（影子）",
    "baseline_full_liquidity_detail": "流动性质量防守策略",
    "baseline_full_liquidity_detail_vol_position": "流动性质量稳健策略（波动仓位）",
    "baseline_full_liquidity_detail_vol_position_shadow": "流动性质量稳健策略（波动仓位影子）",
    "baseline_full_liquidity_detail_hist_mdd_position": "流动性质量稳健策略（回撤仓位）",
    "baseline_full_liquidity_detail_hist_mdd_position_shadow": "流动性质量稳健策略（回撤仓位影子）",
    "baseline_full_liquidity_detail_hold12_shadow": "流动性质量防守策略（12日持有影子）",
    "baseline_full_liquidity_detail_market_gate_pos50_shadow": "流动性质量防守策略（市场门禁50%仓位影子）",
    "baseline_full_liquidity_detail_market_gate": "流动性质量防守策略（市场门禁）",
    "baseline_full_score": "基础综合评分策略",
    "adaptive_market_style": "市场风格自适应生产策略",
    "adaptive_style_shadow": "市场风格自适应生产策略（影子）",
    "adaptive_style_switch": "市场风格自适应切换策略",
    "adaptive_style_switch_dynamic_position": "市场风格自适应切换策略（动态仓位）",
    "dual_system_adaptive_route": "双系统自适应路由策略",
    "ashare_auto_shadow": "AShare AUTO策略（影子）",
    "ashare_trend_breakout_shadow": "AShare趋势突破策略（影子）",
    "ashare_hybrid_conservative_shadow": "AShare保守混合策略（影子）",
}


def strategy_display_name(strategy_id: object, *, include_id: bool = False) -> str:
    """Return a Chinese strategy name while keeping unknown ids readable."""
    strategy_key = str(strategy_id or "").strip()
    if not strategy_key:
        return "-"
    display = STRATEGY_DISPLAY_NAMES.get(strategy_key, strategy_key)
    if include_id and display != strategy_key:
        return f"{display}（{strategy_key}）"
    return display
