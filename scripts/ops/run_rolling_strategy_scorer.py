#!/usr/bin/env python3
"""滚动窗口策略轮动——每日评分与权重分配

Run 3 最佳配置的生产版本。

核心逻辑：
  1. 读取最新全策略回测的 nav.csv
  2. 对每个策略计算滚动 63 个交易日（≈3 个月）表现
  3. 综合评分（40% Sharpe + 25% Calmar + 20% 回撤 + 15% 全历史锚定）
  4. 横截面对比 → baseline 锚定加分 → Softmax 分配权重
  5. 熔断检查 → 平滑过渡 → 落库 ads_rolling_strategy_weights
  6. 推送飞书策略权重卡

用法：
  python scripts/ops/run_rolling_strategy_scorer.py                          # 自动找最新回测目录
  python scripts/ops/run_rolling_strategy_scorer.py --review-dir PATH        # 指定回测目录
  python scripts/ops/run_rolling_strategy_scorer.py --no-push                # 不推送飞书
  python scripts/ops/run_rolling_strategy_scorer.py --dry-run                # 不落库

调度时间：每日 21:20（在 trusted_strategy_backtest 完成后，candidates 之前）
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# ——— 路径自举 ———
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ——— 网络强制 ———
from project_network import enforce_direct_network

enforce_direct_network()

import pymysql
from scoreRank.core.db_config import build_pymysql_config, build_sqlalchemy_url

# ——— 飞书通知 ———
from scripts.ops.feishu_notifier import (
    strategy_identity_block,
)

# ============================================================================
# 配置常量（Run 3 最佳参数）
# ============================================================================

TAU = 0.8                # softmax 温度
WINDOW_DAYS = 63         # 滚动窗口交易日数
RISK_FREE = 0.02         # 无风险利率
TARGET_EXPOSURE = 0.70   # 目标敞口
MAX_DELTA = 0.20         # 单日调仓上限
CB_CONSECUTIVE_DAYS = 15 # 熔断连续天数阈值
ANCHOR_STRATEGY = "baseline_full_liquidity_detail_vol_position"

# 全历史质量门槛：排除总收益 < -20% 的策略
FULL_HISTORY_MIN_RETURN = -0.20

# ============================================================================
# 数据结构
# ============================================================================


@dataclass
class DailyNAV:
    date: str
    equity: float


@dataclass
class StrategyNAV:
    strategy: str
    navs: list[DailyNAV] = field(default_factory=list)

    def dates(self):
        return [n.date for n in self.navs]

    def equities(self):
        return [n.equity for n in self.navs]

    def slice(self, start_idx: int, end_idx: int) -> "StrategyNAVSlice":
        return StrategyNAVSlice(self, start_idx, end_idx)


@dataclass
class StrategyNAVSlice:
    strategy: StrategyNAV
    start_idx: int
    end_idx: int

    @property
    def start_date(self):
        return self.strategy.navs[self.start_idx].date

    @property
    def end_date(self):
        return self.strategy.navs[self.end_idx - 1].date

    @property
    def trading_days(self):
        return self.end_idx - self.start_idx

    def returns(self):
        eq = self.strategy.equities()
        return [
            (eq[i] - eq[i - 1]) / eq[i - 1]
            for i in range(self.start_idx + 1, self.end_idx)
        ]

    def total_return(self):
        s = self.strategy.navs[self.start_idx].equity
        e = self.strategy.navs[self.end_idx - 1].equity
        return (e - s) / s if s > 0 else 0.0

    def max_drawdown(self):
        eq = self.strategy.equities()
        peak = eq[self.start_idx]
        mdd = 0.0
        for i in range(self.start_idx, self.end_idx):
            v = eq[i]
            if v > peak:
                peak = v
            dd = (v - peak) / peak if peak > 0 else 0.0
            if dd < mdd:
                mdd = dd
        return mdd

    def sharpe(self, risk_free: float = 0.02):
        rets = self.returns()
        if len(rets) < 5:
            return 0.0
        mean_ret = sum(rets) / len(rets)
        if mean_ret == 0:
            return 0.0
        var = (
            sum((r - mean_ret) ** 2 for r in rets) / (len(rets) - 1)
            if len(rets) > 1
            else 0.0
        )
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        ann_ret = mean_ret * 252
        ann_std = std * math.sqrt(252)
        return (ann_ret - risk_free) / ann_std if ann_std > 0 else 0.0

    def calmar(self):
        mdd = self.max_drawdown()
        total_ret = self.total_return()
        years = self.trading_days / 252
        ann_ret = (
            (1 + total_ret) ** (1 / years) - 1
            if years > 0 and total_ret > -1
            else total_ret
        )
        return ann_ret / abs(mdd) if mdd != 0 else 0.0

    def daily_win_rate(self):
        rets = self.returns()
        if not rets:
            return 0.0
        return sum(1 for r in rets if r > 0) / len(rets)

    def annualized_volatility(self):
        rets = self.returns()
        if len(rets) < 5:
            return 0.0
        mean_ret = sum(rets) / len(rets)
        var = (
            sum((r - mean_ret) ** 2 for r in rets) / (len(rets) - 1)
            if len(rets) > 1
            else 0.0
        )
        return math.sqrt(var) * math.sqrt(252)


@dataclass
class Rolling3MPerf:
    strategy: str
    window_start: str
    window_end: str
    trading_days: int
    total_return: float
    max_drawdown: float
    sharpe: float
    calmar: float
    daily_win_rate: float
    annualized_vol: float


@dataclass
class FullHistoryRef:
    strategy: str
    total_return: float
    max_dd: float
    sharpe: float
    calmar: float
    trade_count: int


# ============================================================================
# 数据加载
# ============================================================================


def _safe_float(val, default=0.0):
    if val is None or str(val).strip() == "":
        return default
    return float(val)


def find_latest_review_dir() -> Optional[str]:
    """自动查找最新的全策略回测输出目录"""
    exports_base = os.path.join(
        PROJECT_ROOT, "exports", "signal_research"
    )
    if not os.path.isdir(exports_base):
        return None

    # 匹配 production_all_strategy_review 目录
    candidates = []
    for name in os.listdir(exports_base):
        full = os.path.join(exports_base, name)
        if os.path.isdir(full) and "production_all_strategy_review" in name:
            candidates.append(full)

    if not candidates:
        return None

    candidates.sort(reverse=True)  # 最新优先
    return candidates[0]


def load_nav_data(review_dir: str) -> dict[str, StrategyNAV]:
    """加载 nav.csv，过滤死策略"""
    nav_path = os.path.join(review_dir, "nav.csv")
    if not os.path.exists(nav_path):
        raise FileNotFoundError(f"nav.csv 不存在: {nav_path}")

    data: dict[str, StrategyNAV] = defaultdict(
        lambda: StrategyNAV(strategy="")
    )
    with open(nav_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            s = row.get("strategy", "")
            date_val = row.get("trade_date", row.get("date", ""))
            equity = float(row.get("equity", row.get("nav", 0)))
            if not data[s].strategy:
                data[s].strategy = s
            data[s].navs.append(DailyNAV(date=date_val, equity=equity))

    for s in data.values():
        s.navs.sort(key=lambda x: x.date)

    # 过滤死策略
    alive = {}
    for name, s in data.items():
        equities = s.equities()
        if len(equities) < 10:
            continue
        total_ret = (
            (equities[-1] - equities[0]) / equities[0] if equities[0] > 0 else 0
        )
        try:
            import statistics
            stdev = statistics.stdev(equities)
        except Exception:
            stdev = 0.0
        if abs(total_ret) < 0.0001 and stdev < 0.1:
            continue
        alive[name] = s

    return alive


def load_full_history_refs(review_dir: str) -> dict[str, FullHistoryRef]:
    """从 strategy_summary.csv 读取全历史锚定数据"""
    path = os.path.join(review_dir, "strategy_summary.csv")
    refs = {}
    if not os.path.exists(path):
        return refs

    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            s = row.get("strategy", "")
            total_ret = _safe_float(row.get("total_return"))
            mdd = _safe_float(row.get("max_drawdown"))
            sharpe = _safe_float(row.get("sharpe"))
            calmar = _safe_float(row.get("calmar"))
            trades = int(_safe_float(row.get("completed_round_trips")))
            if trades <= 0 and total_ret == 0.0:
                continue
            refs[s] = FullHistoryRef(
                strategy=s,
                total_return=total_ret,
                max_dd=mdd,
                sharpe=sharpe,
                calmar=calmar,
                trade_count=trades,
            )
    return refs


# ============================================================================
# 评分引擎（Run 3 公式）
# ============================================================================


def score_strategy(perf: Rolling3MPerf, full_ref: Optional[FullHistoryRef]) -> float:
    """综合评分：0-100（Run 3 公式）"""
    if abs(perf.total_return) < 0.0001 and perf.annualized_vol < 0.0001:
        return 0.0
    if full_ref and full_ref.trade_count <= 0:
        return 0.0

    sharpe_norm = min(max(perf.sharpe, -2.0), 4.0) / 4.0
    s1 = 0.40 * max(sharpe_norm, 0)

    calmar_norm = min(max(perf.calmar, -5.0), 10.0) / 10.0
    s2 = 0.25 * max(calmar_norm, 0)

    if abs(perf.max_drawdown) < 0.0001 and abs(perf.total_return) < 0.0001:
        dd_factor = 0.0
    else:
        dd_factor = max(0, 1.0 - abs(perf.max_drawdown) / 0.30)
    s3 = 0.20 * dd_factor

    if full_ref:
        full_calmar_norm = min(max(full_ref.calmar, -2.0), 5.0) / 5.0
        s4 = 0.15 * max(full_calmar_norm, 0)
    else:
        s4 = 0.15 * 0.5

    raw_score = (s1 + s2 + s3 + s4) * 100

    # 全历史 Calmar 惩罚
    if full_ref and full_ref.calmar < 0.0:
        raw_score *= 0.7
    elif full_ref and full_ref.calmar < 0.15:
        raw_score *= 0.85

    if perf.trading_days < 10:
        raw_score *= 0.3
    if full_ref and full_ref.trade_count > 0 and full_ref.trade_count < 10:
        raw_score *= 0.4

    return min(max(raw_score, 0), 100)


def score_all_strategies(
    perfs: dict[str, Rolling3MPerf],
    full_refs: dict[str, FullHistoryRef],
) -> dict[str, float]:
    """横截面对比 + 全历史质量门槛 + baseline 锚定"""
    raw_scores = {}
    for s, p in perfs.items():
        raw_scores[s] = score_strategy(p, full_refs.get(s))

    # 全历史质量门槛
    qualified = {}
    for k, v in raw_scores.items():
        if v <= 0:
            continue
        ref = full_refs.get(k)
        if ref and ref.total_return < FULL_HISTORY_MIN_RETURN:
            continue
        qualified[k] = v

    if len(qualified) < 2:
        return {k: (80.0 if k in qualified else 0.0) for k in raw_scores}

    vals = list(qualified.values())
    v_min, v_max = min(vals), max(vals)
    if v_max - v_min < 1.0:
        enhanced = {k: (50.0 if k in qualified else 0.0) for k in raw_scores}
    else:
        enhanced = {}
        for k in raw_scores:
            if k in qualified:
                enhanced[k] = 20.0 + (qualified[k] - v_min) / (v_max - v_min) * 60.0
            else:
                enhanced[k] = 0.0

    # baseline 锚定加分
    if ANCHOR_STRATEGY in enhanced and enhanced[ANCHOR_STRATEGY] > 0:
        anchor_ref = full_refs.get(ANCHOR_STRATEGY)
        if anchor_ref and anchor_ref.total_return > 0.05:
            enhanced[ANCHOR_STRATEGY] = min(enhanced[ANCHOR_STRATEGY] + 8.0, 85.0)

    return enhanced


def allocate_weights(
    scores: dict[str, float],
    target_exposure: float,
    prev_weights: dict[str, float],
    tau: float = TAU,
) -> dict[str, float]:
    """Softmax → 单策略上限50% → 重新归一化 → 平滑过渡"""
    active = {k: v for k, v in scores.items() if v > 0}
    if not active:
        return {k: 0.0 for k in scores}

    exp_scores = {k: math.exp(v / tau) for k, v in active.items()}
    total_exp = sum(exp_scores.values())

    raw = {}
    for k in scores:
        if k in exp_scores:
            raw[k] = (exp_scores[k] / total_exp) * target_exposure
        else:
            raw[k] = 0.0

    for k in raw:
        raw[k] = min(raw[k], 0.50)
        raw[k] = max(raw[k], 0.0)

    raw_total = sum(raw.values())
    if raw_total > 0:
        raw = {k: v / raw_total * target_exposure for k, v in raw.items()}

    smooth = {}
    for k in raw:
        prev = prev_weights.get(k, 0.0)
        target = raw[k]
        delta = target - prev
        if abs(delta) <= MAX_DELTA:
            smooth[k] = target
        else:
            smooth[k] = prev + (delta / abs(delta)) * MAX_DELTA

    return smooth


def check_circuit_breaker(
    perfs: dict[str, Rolling3MPerf],
    target_exposure: float,
    cb_counter: int,
) -> tuple[float, str, int]:
    """熔断检查（Run 3 配置）"""
    active_perfs = [p for p in perfs.values() if p.trading_days > 10]
    if not active_perfs:
        return 1.0, "无有效策略数据", 0

    best_sharpe = max(p.sharpe for p in active_perfs)
    best_by_sharpe = max(active_perfs, key=lambda p: p.sharpe)

    all_negative = best_sharpe < 0
    severe_dd = best_by_sharpe.max_drawdown < -0.20
    trigger = all_negative or severe_dd

    cb_counter = cb_counter + 1 if trigger else 0

    if cb_counter >= CB_CONSECUTIVE_DAYS:
        if all_negative and severe_dd:
            reason = "最佳Sharpe为负 & 回撤>20% → 敞口降到35%"
        elif all_negative:
            reason = "最佳策略滚动Sharpe为负 → 敞口降到35%"
        else:
            reason = (
                f"最佳策略({best_by_sharpe.strategy})"
                f"回撤{best_by_sharpe.max_drawdown:.1%}>20% → 敞口降到35%"
            )
        return 0.35 / target_exposure, reason, cb_counter

    return 1.0, "正常", cb_counter


# ============================================================================
# 数据库操作
# ============================================================================


CREATE_TABLE_PERF_SQL = """
CREATE TABLE IF NOT EXISTS ads_rolling_3m_strategy_perf (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    calc_date DATE NOT NULL COMMENT '计算日期（T日）',
    strategy VARCHAR(128) NOT NULL COMMENT '策略名称',
    window_start DATE NOT NULL COMMENT '滚动窗口起始日',
    window_end DATE NOT NULL COMMENT '滚动窗口截止日',
    trading_days INT NOT NULL COMMENT '窗口内交易日数',
    total_return DECIMAL(14,8) DEFAULT 0 COMMENT '窗口总收益',
    max_drawdown DECIMAL(14,8) DEFAULT 0 COMMENT '窗口最大回撤（负值）',
    sharpe DECIMAL(14,8) DEFAULT 0 COMMENT '年化Sharpe',
    calmar DECIMAL(14,8) DEFAULT 0 COMMENT '年化Calmar',
    daily_win_rate DECIMAL(10,6) DEFAULT 0 COMMENT '日胜率',
    annualized_vol DECIMAL(14,8) DEFAULT 0 COMMENT '年化波动率',
    raw_score DECIMAL(10,4) DEFAULT 0 COMMENT '原始评分（惩罚后、横截面前）',
    final_score DECIMAL(10,4) DEFAULT 0 COMMENT '最终评分（横截面对比后）',
    is_qualified TINYINT(1) DEFAULT 1 COMMENT '是否通过全历史质量门槛',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_date_strategy (calc_date, strategy),
    INDEX idx_calc_date (calc_date),
    INDEX idx_strategy (strategy)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='滚动3月策略表现（每日更新）';
"""

CREATE_TABLE_WEIGHTS_SQL = """
CREATE TABLE IF NOT EXISTS ads_rolling_strategy_weights (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    calc_date DATE NOT NULL COMMENT '计算日期（T日，权重用于T+1执行）',
    strategy VARCHAR(128) NOT NULL COMMENT '策略名称',
    target_weight DECIMAL(10,6) DEFAULT 0 COMMENT '目标权重（softmax+约束后）',
    smooth_weight DECIMAL(10,6) DEFAULT 0 COMMENT '平滑后权重（实际T+1使用）',
    prev_smooth_weight DECIMAL(10,6) DEFAULT 0 COMMENT '前一交易日平滑权重',
    raw_score DECIMAL(10,4) DEFAULT 0 COMMENT '绝对原始分（惩罚后）',
    final_score DECIMAL(10,4) DEFAULT 0 COMMENT '横截面对比后评分',
    effective_exposure DECIMAL(10,6) DEFAULT 0 COMMENT '有效敞口（熔断调整后）',
    circuit_breaker_active TINYINT(1) DEFAULT 0 COMMENT '熔断是否触发',
    circuit_breaker_reason VARCHAR(255) DEFAULT '' COMMENT '熔断原因',
    meta_equity DECIMAL(14,2) DEFAULT NULL COMMENT '元策略累计权益（回测口径）',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_date_strategy (calc_date, strategy),
    INDEX idx_calc_date (calc_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='滚动策略轮动权重（每日T日计算，T+1执行）';
"""


def ensure_tables(conn):
    """确保数据库表存在"""
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_PERF_SQL)
        cur.execute(CREATE_TABLE_WEIGHTS_SQL)
    conn.commit()


def load_prev_weights(conn, strategies: list[str]) -> dict[str, float]:
    """加载最近一期的平滑权重作为前值"""
    prev = {s: 0.0 for s in strategies}
    sql = """
        SELECT strategy, smooth_weight
        FROM ads_rolling_strategy_weights
        WHERE calc_date = (
            SELECT MAX(calc_date) FROM ads_rolling_strategy_weights
        )
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                prev[row[0]] = float(row[1])
    except Exception as e:
        import traceback
        print(f"[DEBUG] load_prev_weights 查询失败: {e}")
        traceback.print_exc()
    return prev


def save_perf(conn, calc_date: str, perfs: dict[str, Rolling3MPerf],
              raw_scores: dict[str, float], final_scores: dict[str, float],
              qualified: set):
    """保存滚动3月表现到数据库"""
    sql = """
        INSERT INTO ads_rolling_3m_strategy_perf
            (calc_date, strategy, window_start, window_end, trading_days,
             total_return, max_drawdown, sharpe, calmar, daily_win_rate,
             annualized_vol, raw_score, final_score, is_qualified)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            window_start=VALUES(window_start), window_end=VALUES(window_end),
            trading_days=VALUES(trading_days), total_return=VALUES(total_return),
            max_drawdown=VALUES(max_drawdown), sharpe=VALUES(sharpe),
            calmar=VALUES(calmar), daily_win_rate=VALUES(daily_win_rate),
            annualized_vol=VALUES(annualized_vol), raw_score=VALUES(raw_score),
            final_score=VALUES(final_score), is_qualified=VALUES(is_qualified)
    """
    with conn.cursor() as cur:
        for s, p in perfs.items():
            cur.execute(
                sql,
                (
                    calc_date, s, p.window_start, p.window_end, p.trading_days,
                    p.total_return, p.max_drawdown, p.sharpe, p.calmar,
                    p.daily_win_rate, p.annualized_vol,
                    raw_scores.get(s, 0), final_scores.get(s, 0),
                    1 if s in qualified else 0,
                ),
            )
    conn.commit()


def save_weights(conn, calc_date: str, scores: dict[str, float],
                 weights: dict[str, float], target_weights: dict[str, float],
                 prev_weights: dict[str, float], effective_exposure: float,
                 cb_active: bool, cb_reason: str):
    """保存权重分配到数据库"""
    sql = """
        INSERT INTO ads_rolling_strategy_weights
            (calc_date, strategy, target_weight, smooth_weight,
             prev_smooth_weight, raw_score, final_score,
             effective_exposure, circuit_breaker_active, circuit_breaker_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            target_weight=VALUES(target_weight),
            smooth_weight=VALUES(smooth_weight),
            prev_smooth_weight=VALUES(prev_smooth_weight),
            raw_score=VALUES(raw_score), final_score=VALUES(final_score),
            effective_exposure=VALUES(effective_exposure),
            circuit_breaker_active=VALUES(circuit_breaker_active),
            circuit_breaker_reason=VALUES(circuit_breaker_reason)
    """
    with conn.cursor() as cur:
        for s in scores:
            cur.execute(
                sql,
                (
                    calc_date, s,
                    target_weights.get(s, 0),
                    weights.get(s, 0),
                    prev_weights.get(s, 0),
                    0,  # raw_score (already stored in perf table)
                    scores.get(s, 0),
                    effective_exposure,
                    1 if cb_active else 0,
                    cb_reason if cb_active else "",
                ),
            )
    conn.commit()


# ============================================================================
# 飞书推送
# ============================================================================


def build_strategy_weight_card(
    calc_date: str,
    weights: dict[str, float],
    scores: dict[str, float],
    effective_exposure: float,
    cb_active: bool,
    cb_reason: str,
    perfs: dict[str, Rolling3MPerf],
) -> str:
    """构建策略权重卡片"""

    ranked = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    ranked_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    lines = []
    lines.append("【滚动策略轮动——每日权重卡】")
    lines.append(f"计算日期：{calc_date}")
    lines.append(strategy_identity_block())
    lines.append("")

    # 熔断状态
    if cb_active:
        lines.append(f"⚠️ 熔断触发：{cb_reason}")
        lines.append(f"有效敞口：{effective_exposure:.0%}")
    else:
        lines.append(f"✅ 正常状态 | 目标敞口：{effective_exposure:.0%}")
    lines.append("")

    # 权重分配
    lines.append("--- 策略权重分配（T+1 执行）---")
    total_w = 0.0
    for name, w in ranked:
        if w < 0.001:
            continue
        score = scores.get(name, 0)
        perf = perfs.get(name)
        ret_3m = perf.total_return if perf else 0
        lines.append(
            f"  {name}: 权重 {w:.1%} | 评分 {score:.0f} | 3月收益 {ret_3m:+.1%}"
        )
        total_w += w

    idle = 1.0 - total_w
    if idle > 0.01:
        lines.append(f"  闲置资金: {idle:.1%}（无风险收益）")

    lines.append("")
    lines.append("--- 评分 Top3 ---")
    for i, (name, score) in enumerate(ranked_scores[:3], 1):
        perf = perfs.get(name)
        if perf:
            lines.append(
                f"  #{i} {name}: 评分 {score:.0f} | "
                f"Sharpe {perf.sharpe:+.2f} | Calmar {perf.calmar:+.2f} | "
                f"回撤 {perf.max_drawdown:.1%}"
            )

    lines.append("")
    lines.append("--- 滚动窗口表现 ---")
    for name in [n for n, _ in ranked[:5]]:
        perf = perfs.get(name)
        if perf:
            lines.append(
                f"  {name}: "
                f"窗口 {perf.window_start}~{perf.window_end} "
                f"({perf.trading_days}d) | "
                f"收益 {perf.total_return:+.2%} | "
                f"胜率 {perf.daily_win_rate:.1%}"
            )

    lines.append("")
    lines.append("💡 权重每日微调，次日开盘执行。影子盘验证中，不产生实盘订单。")

    return "\n".join(lines)


# ============================================================================
# 主逻辑
# ============================================================================


def run(args):
    """主入口"""

    # ——— 1. 确定回测目录 ———
    review_dir = args.review_dir
    if not review_dir:
        review_dir = find_latest_review_dir()
    if not review_dir:
        print("[ERROR] 未找到全策略回测目录。请先运行 trusted_strategy_backtest。")
        sys.exit(1)

    print(f"[INFO] 使用回测目录: {review_dir}")
    calc_date = getattr(args, 'calc_date', None) or date.today().isoformat()
    print(f"[INFO] 权重计算日期: {calc_date}")

    # ——— 2. 加载数据 ———
    nav_data = load_nav_data(review_dir)
    full_refs = load_full_history_refs(review_dir)

    strategies = list(nav_data.keys())
    print(f"[INFO] 活跃策略: {len(strategies)} 个")
    for s in strategies:
        ref = full_refs.get(s)
        ref_str = (
            f"全期收益 {ref.total_return:+.1%}, Calmar {ref.calmar:+.2f}"
            if ref
            else "无锚定数据"
        )
        print(f"  - {s}: {ref_str}")

    # ——— 3. 计算滚动3月表现 ———
    all_dates = sorted(
        set().union(*[set(nav_data[s].dates()) for s in strategies])
    )
    if len(all_dates) < WINDOW_DAYS:
        print(f"[ERROR] 数据不足 {WINDOW_DAYS} 天")
        sys.exit(1)

    current_date = all_dates[-1]  # 最新日期
    print(f"[INFO] 最新数据日期: {current_date}")

    perfs = {}
    for s in strategies:
        nav = nav_data[s]
        dates = nav.dates()
        try:
            end_idx = dates.index(current_date) + 1
        except ValueError:
            print(f"[WARN] {s}: 日期 {current_date} 不在NAV中，跳过")
            continue

        start_idx_s = max(0, end_idx - WINDOW_DAYS)
        if end_idx - start_idx_s < 10:
            print(f"[WARN] {s}: 窗口数据不足10天，跳过")
            continue

        sl = nav.slice(start_idx_s, end_idx)
        perfs[s] = Rolling3MPerf(
            strategy=s,
            window_start=sl.start_date,
            window_end=sl.end_date,
            trading_days=sl.trading_days,
            total_return=sl.total_return(),
            max_drawdown=sl.max_drawdown(),
            sharpe=sl.sharpe(RISK_FREE),
            calmar=sl.calmar(),
            daily_win_rate=sl.daily_win_rate(),
            annualized_vol=sl.annualized_volatility(),
        )
        print(
            f"  {s}: 3月收益 {perfs[s].total_return:+.2%} | "
            f"Sharpe {perfs[s].sharpe:+.2f} | "
            f"Calmar {perfs[s].calmar:+.2f} | "
            f"回撤 {perfs[s].max_drawdown:.1%}"
        )

    # ——— 4. 评分 ———
    raw_scores = {}
    for s in strategies:
        if s in perfs:
            raw_scores[s] = score_strategy(perfs[s], full_refs.get(s))
        else:
            raw_scores[s] = 0.0

    final_scores = score_all_strategies(perfs, full_refs)
    qualified = {
        s for s, sc in final_scores.items()
        if sc > 0 and (
            s not in full_refs
            or full_refs[s].total_return >= FULL_HISTORY_MIN_RETURN
        )
    }

    for s in sorted(final_scores, key=final_scores.get, reverse=True):
        qual_mark = "✅" if s in qualified else "❌"
        print(
            f"  Score {s}: raw={raw_scores.get(s,0):.1f} → final={final_scores[s]:.1f} {qual_mark}"
        )

    # ——— 5. 权重计算（不依赖DB） ———
    prev_weights = {s: 0.0 for s in strategies}
    cb_counter = 0

    cb_multiplier, cb_reason, new_cb_counter = check_circuit_breaker(
        perfs, TARGET_EXPOSURE, cb_counter
    )
    cb_active = cb_multiplier < 1.0
    effective_exposure = TARGET_EXPOSURE * cb_multiplier

    if cb_active:
        print(f"[WARN] 熔断触发 ({new_cb_counter}/{CB_CONSECUTIVE_DAYS}天): {cb_reason}")

    # ——— 6. 权重分配（初始计算，从零开始） ———
    target_weights = allocate_weights(
        final_scores, effective_exposure, prev_weights
    )

    # ——— 7. 数据库操作 ———
    if not args.dry_run:
        db_cfg = build_pymysql_config()
        db_cfg.pop('cursorclass', None)  # 使用默认 tuple cursor
        # 如果有 unix_socket，优先使用（移除冲突的 host/port）
        if db_cfg.get('unix_socket'):
            db_cfg.pop('host', None)
            db_cfg.pop('port', None)
        conn = pymysql.connect(**db_cfg)
        try:
            ensure_tables(conn)

            # 加载前一日权重
            prev_weights = load_prev_weights(conn, strategies)
            print(f"[INFO] 前一日权重: { {k: f'{v:.1%}' for k, v in prev_weights.items() if v > 0.01} }")

            # 读取前一日熔断连续天数
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM ads_rolling_strategy_weights "
                        "WHERE circuit_breaker_active = 1 "
                        "AND calc_date > DATE_SUB(%s, INTERVAL 30 DAY)",
                        (calc_date,),
                    )
                    row = cur.fetchone()
                    if row:
                        cb_counter = row[0]
            except Exception:
                pass

            # 用真实前值重新算
            if any(v > 0 for v in prev_weights.values()):
                cb_multiplier, cb_reason, new_cb_counter = check_circuit_breaker(
                    perfs, TARGET_EXPOSURE, cb_counter
                )
                cb_active = cb_multiplier < 1.0
                effective_exposure = TARGET_EXPOSURE * cb_multiplier
                target_weights = allocate_weights(
                    final_scores, effective_exposure, prev_weights
                )
                if cb_active:
                    print(f"[WARN] 熔断触发 (更新后): {cb_reason}")
            else:
                # 首次运行，直接使用目标权重（初始 ramp）
                target_weights = allocate_weights(
                    final_scores, effective_exposure, prev_weights
                )

            print(f"[INFO] 分配权重 (有效敞口 {effective_exposure:.0%}):")
            for s, w in sorted(target_weights.items(), key=lambda x: x[1], reverse=True):
                if w > 0.01:
                    print(f"  {s}: {w:.1%}")

            # 落库（perf 用 NAV 日期，weights 用业务日期）
            save_perf(conn, current_date, perfs, raw_scores, final_scores, qualified)
            save_weights(
                conn, calc_date, final_scores, target_weights,
                target_weights,
                prev_weights, effective_exposure, cb_active, cb_reason,
            )
            print("[INFO] 数据已写入数据库")

            # 飞书推送
            if not args.no_push:
                card = build_strategy_weight_card(
                    current_date, target_weights, final_scores,
                    effective_exposure, cb_active, cb_reason, perfs,
                )
                try:
                    from scripts.ops.feishu_notifier import (
                        send_feishu_text_audited,
                    )
                    from sqlalchemy import create_engine
                    engine = create_engine(build_sqlalchemy_url())
                    business_date = str(calc_date).replace("-", "")[:8]
                    ok, reason = send_feishu_text_audited(
                        engine, card, business_date=business_date,
                        notification_type="rolling_strategy_scorer",
                        task_name="rolling_strategy_scorer",
                        dedupe_key=f"rolling_strategy_scorer:{business_date}",
                    )
                    engine.dispose()
                    if ok:
                        print("[INFO] 飞书推送成功")
                    else:
                        print(f"[WARN] 飞书通知已进入补偿队列: {reason}")
                except Exception as e:
                    print(f"[WARN] 飞书推送失败: {e}")
        finally:
            conn.close()
    else:
        # dry-run: 仍然输出飞书预览
        if not args.no_push:
            card = build_strategy_weight_card(
                current_date, target_weights, final_scores,
                effective_exposure, cb_active, cb_reason, perfs,
            )
            print("\n--- 飞书推送预览 ---")
            print(card)
            print("--- 预览结束 ---")

    # ——— 11. 输出 JSON（供上游消费） ———
    output = {
        "calc_date": current_date,
        "review_dir": review_dir,
        "weights": {k: round(v, 6) for k, v in target_weights.items()},
        "scores": {k: round(v, 2) for k, v in final_scores.items()},
        "effective_exposure": round(effective_exposure, 4),
        "circuit_breaker": cb_active,
        "circuit_breaker_reason": cb_reason,
        "top3_strategies": sorted(final_scores, key=final_scores.get, reverse=True)[:3],
    }

    output_path = os.path.join(review_dir, "rolling_rotation_weights.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[INFO] 权重JSON已保存: {output_path}")

    return output


# ============================================================================
# CLI
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="滚动窗口策略轮动——每日评分与权重分配"
    )
    parser.add_argument(
        "--review-dir",
        help="全策略回测输出目录（默认自动查找最新）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="不推送飞书通知",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不写入数据库",
    )
    parser.add_argument(
        "--calc-date",
        default=date.today().isoformat(),
        help="计算日期（默认今天）",
    )
    args = parser.parse_args()

    try:
        run(args)
    except Exception as e:
        print(f"[FATAL] {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
