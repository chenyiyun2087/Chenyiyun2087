"""CLI entry point for Local Strategy (scoreRank & chenyiyunSelected) x Z-score x MIT Trader backtest."""

from __future__ import annotations

import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASDC_ROOT = Path("/Volumes/extension/projects/AShareDataCenter")
for p in (str(PROJECT_ROOT), str(ASDC_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from research.local_strategy_z_mit.pipeline import LocalStrategyZPipeline


def main() -> None:
    pipeline = LocalStrategyZPipeline()
    manifest = pipeline.run()

    report_dir = PROJECT_ROOT / "reports" / "local_strategy_z_mit"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "LOCAL_STRATEGY_Z_MIT_REPORT.md"

    bm = manifest["benchmark"]
    cg = manifest["control_groups"]
    inc = manifest["incremental_value_comparisons"]
    tg = manifest["three_gates"]

    def pct(v):
        return f"{v * 100:+.2f}%" if isinstance(v, (int, float)) else str(v)

    def flt(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) else str(v)

    lines = [
        "# 本地策略（scoreRank & chenyiyunSelected）× Z 值 × MIT Trader 组合回测报告",
        "",
        f"- **研究属性**: `RESEARCH_ONLY`",
        f"- **回测区间**: {manifest['replay_start_date']} 至 {manifest['end_date']} (共 {manifest['sessions_evaluated']} 个交易日，预热始于 {manifest['start_date']})",
        f"- **基准指数**: 沪深 300 (`{bm['code']}`)",
        f"- **账户规则**: 2,000,000 元初始资金、3 个滚动席位、主板/创业板 100 股起购、科创板 200 股起购、20 日均成交额 1% 上限、7.5 bp 单边佣金 + 印花税。",
        "",
        "## 1. 六组同窗核心业绩表",
        "",
        "| 策略组合 | 累计收益 | 年化收益 (CAGR) | 年化波动率 | 夏普比率 | 最大回撤 (MDD) | 平均仓位 | 成交笔数 | Three Gates |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| **基准 (沪深 300)** | {pct(bm['total_return_close'])} | {pct(bm['cagr_annualized'])} | {pct(bm['annualized_volatility'])} | {flt(bm['sharpe_ratio'])} | {pct(bm['max_drawdown'])} | 100.0% | — | — |",
    ]
    for key, label in [
        ("scorerank_only", "scoreRank 仅选股 (Top3 月初轮动)"),
        ("scorerank_plus_z", "scoreRank + Z 值择时 (Top30 池)"),
        ("scorerank_plus_z_mit", "scoreRank + Z 值 + MIT 风控"),
        ("chenyiyun_only", "chenyiyunSelected 仅选股 (Top3 月初轮动)"),
        ("chenyiyun_plus_z", "chenyiyunSelected + Z 值择时 (Top30 池)"),
        ("chenyiyun_plus_z_mit", "chenyiyunSelected + Z 值 + MIT 风控"),
    ]:
        r = cg[key]
        lines.append(
            f"| **{label}** | {pct(r['total_return'])} | {pct(r['cagr_annualized'])} | {pct(r['annualized_volatility'])} | "
            f"{flt(r['sharpe_ratio'])} | {pct(r['max_drawdown'])} | {pct(r['average_exposure'])} | {r['fills_total']} | **{tg[key]['status']}** |"
        )

    lines += [
        "",
        "## 2. 增量价值配对分块 Bootstrap 检验",
        "",
        f"- **scoreRank 叠加 Z 值增量 (`scoreRank+Z` 减 `scoreRank_only`)**: 年化超额差 **{pct(inc['scorerank_timing_edge']['mean_diff_annualized'])}**, 95% CI `[{pct(inc['scorerank_timing_edge']['ci_lower'])}, {pct(inc['scorerank_timing_edge']['ci_upper'])}]`, $p={inc['scorerank_timing_edge']['p_value']:.4f}$",
        f"- **scoreRank 叠加 MIT 风控增量 (`scoreRank+Z+MIT` 减 `scoreRank+Z`)**: 年化超额差 **{pct(inc['scorerank_mit_edge']['mean_diff_annualized'])}**, 95% CI `[{pct(inc['scorerank_mit_edge']['ci_lower'])}, {pct(inc['scorerank_mit_edge']['ci_upper'])}]`, $p={inc['scorerank_mit_edge']['p_value']:.4f}$",
        f"- **chenyiyunSelected 叠加 Z 值增量 (`chenyiyun+Z` 减 `chenyiyun_only`)**: 年化超额差 **{pct(inc['chenyiyun_timing_edge']['mean_diff_annualized'])}**, 95% CI `[{pct(inc['chenyiyun_timing_edge']['ci_lower'])}, {pct(inc['chenyiyun_timing_edge']['ci_upper'])}]`, $p={inc['chenyiyun_timing_edge']['p_value']:.4f}$",
        f"- **chenyiyunSelected 叠加 MIT 风控增量 (`chenyiyun+Z+MIT` 减 `chenyiyun+Z`)**: 年化超额差 **{pct(inc['chenyiyun_mit_edge']['mean_diff_annualized'])}**, 95% CI `[{pct(inc['chenyiyun_mit_edge']['ci_lower'])}, {pct(inc['chenyiyun_mit_edge']['ci_upper'])}]`, $p={inc['chenyiyun_mit_edge']['p_value']:.4f}$",
        "",
        "## 3. MIT Three Gates 明细",
        "",
        "| 策略 | 毛 Sharpe | 净 Sharpe | 双倍成本 Sharpe | 正折比例 | 置换 p 值 | DSR | 状态 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key in ["scorerank_only", "scorerank_plus_z", "scorerank_plus_z_mit", "chenyiyun_only", "chenyiyun_plus_z", "chenyiyun_plus_z_mit"]:
        g = tg[key]
        lines.append(
            f"| **{key}** | {g['gross_sharpe']:.4f} | {g['net_sharpe']:.4f} | {g['double_cost_sharpe']:.4f} | {g['positive_fold_ratio']:.2f} | {g['permutation_p_value']:.4f} | {g['dsr_probability']:.4f} | **{g['status']}** |"
        )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
