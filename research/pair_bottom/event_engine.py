"""Major Corporate Events and Scenario Modeling Engine (PAIR_BOTTOM_EVENT_ENHANCED_V1).

Covers:
1. Private Placement Deep Break & Unlock (定增深度破发与临近解禁)
2. Capacity Expansion & Release Lifecycle (CAP01-CAP10)
3. Share Buybacks and Insider Trading (回购与增持)
4. Nested Models M0 to M6 and Ablation Studies.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


CAPACITY_STAGES = {
    "CAP01": "宣布扩产",
    "CAP02": "正式开工/奠基",
    "CAP03": "厂房竣工/主体封顶",
    "CAP04": "设备搬入",
    "CAP05": "客户产品验证",
    "CAP06": "试生产",
    "CAP07": "正式量产",
    "CAP08": "全部产线投产",
    "CAP09": "产能利用率>=80%",
    "CAP10": "满产或接近满产",
}


def classify_placement_break(discount: float) -> str:
    """Classifies placement discount into defined buckets:
    discount = current_price / placement_price - 1 (e.g. -0.479 -> 40%-50% break)
    """
    if pd.isna(discount) or discount >= 0:
        return "PREMIUM_OR_PAR"
    loss_pct = -discount * 100.0
    if loss_pct < 10.0:
        return "BREAK_0_10"
    elif loss_pct < 20.0:
        return "BREAK_10_20"
    elif loss_pct < 30.0:
        return "BREAK_20_30"
    elif loss_pct < 40.0:
        return "BREAK_30_40"
    elif loss_pct < 50.0:
        return "BREAK_40_50"
    else:
        return "BREAK_GE_50"


class PairBottomEventEngine:
    def __init__(self):
        pass

    def evaluate_placement_unlock_event(
        self,
        current_price: float,
        placement_price: Optional[float],
        days_to_unlock: Optional[int],
        unlock_float_ratio: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Evaluates private placement discount and unlock proximity."""
        if placement_price is None or placement_price <= 0:
            return {
                "has_placement": False,
                "placement_discount": np.nan,
                "break_category": "NONE",
                "is_deep_break": False,
                "days_to_unlock": np.nan,
                "is_near_unlock": False,
            }

        discount = (current_price / placement_price) - 1.0
        break_cat = classify_placement_break(discount)
        is_deep_break = discount <= -0.40  # >= 40% loss

        is_near_unlock = False
        if days_to_unlock is not None and not pd.isna(days_to_unlock):
            is_near_unlock = 0 <= days_to_unlock <= 60

        return {
            "has_placement": True,
            "placement_discount": float(discount),
            "break_category": break_cat,
            "is_deep_break": is_deep_break,
            "days_to_unlock": float(days_to_unlock) if days_to_unlock is not None else np.nan,
            "is_near_unlock": is_near_unlock,
            "unlock_float_ratio": float(unlock_float_ratio) if unlock_float_ratio is not None else np.nan,
        }

    def evaluate_buyback_event(
        self,
        has_announced: bool,
        plan_amount: float = 0.0,
        executed_amount: float = 0.0,
        market_cap: float = 0.0,
        high_limit_price: Optional[float] = None,
        current_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Evaluates share buyback program status."""
        cap_ratio = plan_amount / market_cap if market_cap > 0 else 0.0
        exec_ratio = executed_amount / market_cap if market_cap > 0 else 0.0
        completion_pct = executed_amount / plan_amount if plan_amount > 0 else 0.0
        
        price_gap_to_limit = np.nan
        if high_limit_price and current_price and current_price > 0:
            price_gap_to_limit = (high_limit_price / current_price) - 1.0

        return {
            "buyback_announced": has_announced,
            "buyback_plan_amount": plan_amount,
            "buyback_executed_amount": executed_amount,
            "buyback_cap_ratio": cap_ratio,
            "buyback_executed_ratio": exec_ratio,
            "buyback_completion_pct": completion_pct,
            "buyback_price_gap_to_limit": price_gap_to_limit,
        }

    def evaluate_nested_model(
        self,
        is_control_or_pair: str,
        is_oversold: bool,
        is_deep_break: bool,
        is_near_unlock: bool,
        has_capacity_plan: bool,
        has_buyback: bool,
    ) -> str:
        """Determines the nested model tier M0 to M6:
        M0: Ordinary bottom benchmark
        M1: Pair bottom
        M2: Pair bottom + Oversold (drawdown >= 25%)
        M3: M2 + Placement Deep Break (loss >= 40%)
        M4: M3 + Near Unlock (within 60 trading days)
        M5: M4 + Known Capacity Expansion Plan
        M6: M5 + Buyback / Insider Increase
        """
        if is_control_or_pair == "CONTROL":
            return "M0_CONTROL"
        if not is_oversold:
            return "M1_PAIR_ONLY"
        if not is_deep_break:
            return "M2_PAIR_OVERSOLD"
        if not is_near_unlock:
            return "M3_PAIR_DEEP_BREAK"
        if not has_capacity_plan:
            return "M4_PAIR_NEAR_UNLOCK"
        if not has_buyback:
            return "M5_PAIR_CAPACITY"
        return "M6_PAIR_FULL_COMBO"
