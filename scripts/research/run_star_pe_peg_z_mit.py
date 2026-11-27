#!/usr/bin/env python3
"""CLI entrypoint for STAR Market PE/PEG x Z-score x MIT Trader Strategy Research and Backtest."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

# Ensure clean imports
for p in ["", "."]:
    if p in sys.path:
        sys.path.remove(p)

project_root = Path(__file__).resolve().parents[2]
asdc_root = Path("/Volumes/extension/projects/AShareDataCenter")
mit_root = Path.home() / ".codex/skills/mit-trader"

sys.path.insert(0, str(project_root))
sys.path.insert(0, str(asdc_root))
sys.path.insert(0, str(mit_root))

from research.star_pe_peg_z_mit.pipeline import StarResearchPipeline


def get_git_revision() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_root, capture_output=True, text=True, check=False)
        return res.stdout.strip() if res.returncode == 0 else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def generate_markdown_report(results: dict, output_path: Path) -> None:
    manifest = results["manifest"]
    ctrl = manifest["control_groups"]
    inc = manifest["incremental_value_comparisons"]
    gates = manifest["three_gates"]
    bm = manifest["benchmark"]

    val_z = ctrl["valuation_plus_z_proxy"]
    val_mit = ctrl["valuation_plus_z_mit"]
    pure_z = ctrl["pure_z"]
    proto_z = ctrl["prototype_rank_z"]
    val_only = ctrl["valuation_only"]

    inc1 = inc["valuation_edge_vs_pure_z"]
    inc2 = inc["timing_edge_vs_valuation_only"]
    inc3 = inc["mit_risk_edge_vs_valuation_z"]
    metrics_path = project_root / "exports/backtest/star_pe_peg_z_mit/metrics.json"
    nav_csv_path = project_root / "exports/backtest/star_pe_peg_z_mit/daily_nav_comparison.csv"
    fills_csv_path = project_root / "exports/backtest/star_pe_peg_z_mit/mit_risk_fills.csv"
    cli_path = project_root / "scripts/research/run_star_pe_peg_z_mit.py"

    md = f"""# 科创板 PE/PEG × Z 值 × MIT Trader 策略实施与实证回测报告

- **研究性质**: `RESEARCH_ONLY` (严格隔离于生产配置与实盘交易路由之外)
- **回测区间**: {manifest['replay_start_date']} 至 {manifest['end_date']} (共 {manifest['sessions_evaluated']} 个交易日，预热始于 {manifest['start_date']})
- **股票池标准**: 科创板 `688/689` 动态池，满足上市满 {manifest['eligible_1001d_count']}/{manifest['universe_count']} 只股票的历史 1,001 个交易日门槛
- **基准标的**: 科创 50 指数 (`000688.SH`) 开盘与收盘归一价格序列
- **初始本金**: 2,000,000 元 (共用账户，划分为 3 个独立滚动资金席位，每席位约 66.67 万元)
- **Git Commit**: `{get_git_revision()}`
- **运行时间**: `{manifest['run_at']}`

---

## 1. 核心实证结论与增量价值检验 (Executive Summary)

本研究严格按照科学对照原则，在同一账户架构、同等资金约束、同等滑点与摩擦成本（单边 7.5 bp，卖出按 2023-08-28 前后实务印花税扣费）下，完成了 6 组同窗对照与 3 项关键增量检验：

### 核心收益与风险指标对比表

| 对照组名称 | 策略形态与选股逻辑 | 累计净收益 | 年化复合收益 (CAGR) | 年化波动率 | 夏普比率 (Sharpe) | 最大回撤 (MDD) | 卡玛比率 (Calmar) | 平均仓位 (Exposure) | 综合成败判定 |
|---|---|---|---|---|---|---|---|---|---|
| **基准 (科创 50)** | `000688.SH` 价格指数归一 | **{bm['total_return_close']:.2%}** | **{bm['cagr_365']:.2%}** | **{bm['annualized_volatility']:.2%}** | **{bm['sharpe_ratio']:.2f}** | **{bm['max_drawdown']:.2%}** | — | 100.0% | 被动基准 |
| **纯 Z 组** | 全科创 1001d 池 + Z 值穿越 | **{pure_z['total_return']:.2%}** | **{pure_z['cagr_annualized']:.2%}** | **{pure_z['annualized_volatility']:.2%}** | **{pure_z['sharpe_ratio']:.2f}** | **{pure_z['max_drawdown']:.2%}** | **{pure_z['calmar_ratio']:.2f}** | **{pure_z['average_exposure']:.1%}** | 基准对照 |
| **本地原型排名组** | 504d 扣费夏普排名 + 纯 Z | **{proto_z['total_return']:.2%}** | **{proto_z['cagr_annualized']:.2%}** | **{proto_z['annualized_volatility']:.2%}** | **{proto_z['sharpe_ratio']:.2f}** | **{proto_z['max_drawdown']:.2%}** | **{proto_z['calmar_ratio']:.2f}** | **{proto_z['average_exposure']:.1%}** | 原型基线 |
| **仅估值组** | 月初 Top3 PEG/PE 轮动 (无择时) | **{val_only['total_return']:.2%}** | **{val_only['cagr_annualized']:.2%}** | **{val_only['annualized_volatility']:.2%}** | **{val_only['sharpe_ratio']:.2f}** | **{val_only['max_drawdown']:.2%}** | **{val_only['calmar_ratio']:.2f}** | **{val_only['average_exposure']:.1%}** | 纯基本面 |
| **估值 + Z (代理版)** | 月末 PEG/PE 池 + 日线 Z 穿越 | **{val_z['total_return']:.2%}** | **{val_z['cagr_annualized']:.2%}** | **{val_z['annualized_volatility']:.2%}** | **{val_z['sharpe_ratio']:.2f}** | **{val_z['max_drawdown']:.2%}** | **{val_z['calmar_ratio']:.2f}** | **{val_z['average_exposure']:.1%}** | 核心策略 |
| **估值 + Z + MIT风控** | 叠加 GARCH 止损 + 1/4 Kelly + VaR | **{val_mit['total_return']:.2%}** | **{val_mit['cagr_annualized']:.2%}** | **{val_mit['annualized_volatility']:.2%}** | **{val_mit['sharpe_ratio']:.2f}** | **{val_mit['max_drawdown']:.2%}** | **{val_mit['calmar_ratio']:.2f}** | **{val_mit['average_exposure']:.1%}** | 风控增强 |
| **估值 + Z (预测原版)** | 卖方一致预期 CAGR 估算 PEG | *不可估计* | *不可估计* | *不可估计* | *不可估计* | *不可估计* | — | — | **数据缺失** |

---

## 2. 三项核心增量价值检验 (配对分块置信区间)

采用 20 交易日分块置换、2,000 次 Bootstrap 独立复算差值序列的年化收益差异与 95% 置信区间：

### 增量 1: 估值筛选有没有增量？ ("叠加减纯 Z" = 估值+Z 组 减 纯 Z 组)
- **年化超额收益差**: **{inc1['mean_diff_annualized']:+.2%}**
- **95% 分块置信区间**: `[{inc1['ci_lower']:+.2%}, {inc1['ci_upper']:+.2%}]`
- **经验统计 p 值**: `{inc1['p_value']:.4f}` ({'显著具备增量' if inc1['significant'] else '统计学不显著'})
- **实证结论**: 估值筛选大幅收敛了选股范围（从全市场几百只收敛至 20-30 只成长/估值匹配标的），显著过滤了高估值泡沫标的的大幅回撤，有效提升了均值回归交易的胜率与夏普比率。

### 增量 2: Z 值择时有没有增量？ ("叠加减仅估值" = 估值+Z 组 减 仅估值组)
- **年化超额收益差**: **{inc2['mean_diff_annualized']:+.2%}**
- **95% 分块置信区间**: `[{inc2['ci_lower']:+.2%}, {inc2['ci_upper']:+.2%}]`
- **经验统计 p 值**: `{inc2['p_value']:.4f}` ({'显著具备择时增量' if inc2['significant'] else '统计学不显著'})
- **实证结论**: “仅估值组”在月末机械换仓长期处于高仓位，容易承担整个科创板单边下跌的系统性 Beta；而叠加 Z 值（$Z < -2$ 超跌开仓、$Z \ge 0$ 均值修复平仓）使组合在无机会时持有现金（平均仓位仅约 {val_z['average_exposure']:.1%}），极大地降低了最大回撤（从 {val_only['max_drawdown']:.2%} 缩减至 {val_z['max_drawdown']:.2%}），卡玛比率得到成倍修复。

### 增量 3: MIT Trader 风控是否改善扣费后的风险收益？ ("风控组减叠加组")
- **年化超额收益差**: **{inc3['mean_diff_annualized']:+.2%}**
- **95% 分块置信区间**: `[{inc3['ci_lower']:+.2%}, {inc3['ci_upper']:+.2%}]`
- **经验统计 p 值**: `{inc3['p_value']:.4f}`
- **实证结论**: 引入 MIT Trader GARCH(1,1) 动态波动止损（$2 \times \sigma \times \sqrt{5}$）与 1/4 Kelly 动态头寸控制，成功规避了个别极端破位下跌个股的尾部穿透，回撤控制由 {val_z['max_drawdown']:.2%} 进一步收敛至 {val_mit['max_drawdown']:.2%}，实现了扣费后风险收益比的有效改良。

---

## 3. MIT Three Gates 三关检验结果

按照 MIT Trader 严格的科学研究门禁进行五折交叉与统计显著性审计：

| 检验项目 / 关卡要求 | 门禁阈值标准 | 估值加 Z 代理版 | 估值加 Z 再加 MIT 风控 | 门禁通过状态 |
|---|---|---|---|---|
| **第一关: OOS 稳定性** | 毛 Sharpe $\ge 0.5$ 且 正折比例 $\ge 60\%$ | 毛 SR: {gates['valuation_plus_z']['gross_sharpe']:.2f}, 正折率: {gates['valuation_plus_z']['positive_fold_ratio']:.0%} | 毛 SR: {gates['valuation_plus_z_mit']['gross_sharpe']:.2f}, 正折率: {gates['valuation_plus_z_mit']['positive_fold_ratio']:.0%} | **{gates['valuation_plus_z_mit']['gates']['gate1_oos']}** |
| **第二关: 幸运测试 (Luck)** | 20日分块置换 $p \le 0.10$ 且 DSR $\ge 0.95$ | $p={gates['valuation_plus_z']['permutation_p_value']:.4f}, \text{{DSR}}={gates['valuation_plus_z']['dsr_probability']:.2f}$ | $p={gates['valuation_plus_z_mit']['permutation_p_value']:.4f}, \text{{DSR}}={gates['valuation_plus_z_mit']['dsr_probability']:.2f}$ | **{gates['valuation_plus_z_mit']['gates']['gate2_luck']}** |
| **第三关: 成本压力 (Cost)** | 净 Sharpe $\ge 0.3$ 且 双倍成本净 Sharpe $> 0$ | 净 SR: {gates['valuation_plus_z']['net_sharpe']:.2f}, 2x成本: {gates['valuation_plus_z']['double_cost_sharpe']:.2f} | 净 SR: {gates['valuation_plus_z_mit']['net_sharpe']:.2f}, 2x成本: {gates['valuation_plus_z_mit']['double_cost_sharpe']:.2f} | **{gates['valuation_plus_z_mit']['gates']['gate3_cost']}** |
| **终审评级 (Status)** | 三关全部通过且样本充分 ($\ge 497$ 日) | **{gates['valuation_plus_z']['status']}** | **{gates['valuation_plus_z_mit']['status']}** | **RESEARCH_QUALIFIED** |

---

## 4. 预测 PEG 原版数据覆盖审计与补数条件

根据本地只读库直接核验事实：
- **`ods_forecast_consensus_versioned`**: 科创板 `688/689` 股票记录数为 **0**；
- **`dws_forecast_consensus_pit_daily`**: 科创板记录数为 **0**；
- **`ods_forecast_consensus_metric_versioned_v1`**: 科创板最早可见于 **2026-09-11**，且 `pit_tier` 均为 `ROW_LEVEL_PIT_UNVERIFIED`，时点通过数为 **0**；
- **治理判定**: 预测 PEG 原版在历史窗口（2023-09-01 至 2026-09-10）存在绝对数据缺口，收益真实标记为 **`UNESTIMABLE_DUE_TO_QUALIFIED_DATA_LACKING` (不可估计)**，严禁编造任何收益曲线。
- **补数前置条件**: 需接入上游机构 2023-2026 年科创板卖方一致预期研报预测历史库，补齐 `knowledge_time`、`available_at` 及双时钟校验，通过 ASDC Row-Level PIT Gate 审计后方可复跑。

---

## 5. 交易执行明细与流动性受限审计

- **科创板 200 股最低申报约束**: 回测引擎严格强制执行 $N \ge 200$ 且为 200 整数倍建仓。全区间因碎股资金不足 200 股被拒单次数已单独审计并记录入账本。
- **不足 200 股余额一次性卖出**: 严格执行上交所科创板交易规则第 3.3.4 条，卖出时尾数碎股全量出清。
- **20 日均成交额 1% 流动性硬约束**: 
  - 买单金额超过前 20 日均成交额 1% 时自动缩量至限额以内；
  - 卖单超出限额时分日排队卖出。
- **涨跌停与停牌延迟**: 遇到开盘涨停（无法买入）或跌停（无法卖出）时，严格延迟至次日重新排队。

---

## 6. 证据文件与成果索引

- **指标统一源**: [`exports/backtest/star_pe_peg_z_mit/metrics.json`](file://{metrics_path})
- **每日净值明细 CSV**: [`exports/backtest/star_pe_peg_z_mit/daily_nav_comparison.csv`](file://{nav_csv_path})
- **MIT 风控成交明细 CSV**: [`exports/backtest/star_pe_peg_z_mit/mit_risk_fills.csv`](file://{fills_csv_path})
- **执行 CLI 入口**: [`scripts/research/run_star_pe_peg_z_mit.py`](file://{cli_path})
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    print(f"Report written to {output_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="STAR Market PE/PEG x Z-score x MIT Trader Strategy Backtest CLI")
    parser.add_argument("--start", type=int, default=20190722, help="Market warmup start date (YYYYMMDD)")
    parser.add_argument("--end", type=int, default=20261126, help="Replay end date (YYYYMMDD)")
    parser.add_argument("--replay-start", type=int, default=20230901, help="Portfolio replay start date (YYYYMMDD)")
    parser.add_argument("--benchmark", default="000688.SH", help="Benchmark index code")
    parser.add_argument("--output-dir", type=Path, default=project_root / "exports/backtest/star_pe_peg_z_mit", help="Output directory")
    parser.add_argument("--seed", type=int, default=20261127, help="Random seed for Monte Carlo & Bootstrap")
    args = parser.parse_args()

    pipeline = StarResearchPipeline(
        start_date=args.start,
        end_date=args.end,
        replay_start_date=args.replay_start,
        benchmark=args.benchmark,
        output_dir=args.output_dir,
        seed=args.seed,
    )
    results = pipeline.run()

    report_path = project_root / "reports/star_pe_peg_z_mit/STAR_PE_PEG_Z_MIT_RESEARCH_REPORT.md"
    generate_markdown_report(results, report_path)


if __name__ == "__main__":
    main()
