#!/usr/bin/env python3
"""Build one integrated daily strategy digest and publish it once.

The underlying jobs remain independently executable and auditable:

- ``run_rolling_strategy_scorer.py`` computes rolling scores and weights;
- ``export_trusted_strategy_candidates.py`` persists candidates/orders;
- ``run_trusted_strategy_shadow_monitor.py`` persists execution diagnostics;
- ``run_strategy_performance_review.py`` builds the canonical strategy review.

This wrapper adds candidate scores plus the rolling-score snapshot to the
canonical performance review and emits one Feishu digest. Routine source jobs
stay silent; failures, blocks, retries and recoveries remain immediate
scheduler notifications.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.ops import run_strategy_performance_review as base_review
from scripts.ops.feishu_notifier import send_feishu_text_audited


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(value: Any, digits: int = 1) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number * 100:+.{digits}f}%"


def _num(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.{digits}f}"


def _latest_calc_date(engine, table_name: str, review_date: str) -> str | None:
    try:
        with engine.connect() as conn:
            value = conn.execute(
                text(
                    f"SELECT MAX(calc_date) FROM chenyiyun.{table_name} "
                    "WHERE calc_date <= :review_date"
                ),
                {"review_date": review_date},
            ).scalar()
    except Exception:
        return None
    return str(value)[:10] if value is not None else None


def load_rolling_score_snapshot(engine, review_date: str) -> dict[str, Any]:
    """Load the latest persisted rolling scores and weights as-of review day."""
    perf_date = _latest_calc_date(engine, "ads_rolling_3m_strategy_perf", review_date)
    weight_date = _latest_calc_date(engine, "ads_rolling_strategy_weights", review_date)
    warnings: list[str] = []
    if perf_date is None:
        warnings.append("rolling_performance_missing")
    if weight_date is None:
        warnings.append("rolling_weights_missing")

    perf_rows: dict[str, dict[str, Any]] = {}
    weight_rows: dict[str, dict[str, Any]] = {}
    try:
        if perf_date:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """SELECT strategy,total_return,max_drawdown,sharpe,calmar,
                                  final_score,is_qualified,trading_days,window_start,window_end
                           FROM chenyiyun.ads_rolling_3m_strategy_perf
                           WHERE calc_date=:calc_date"""
                    ),
                    {"calc_date": perf_date},
                ).mappings()
                perf_rows = {str(row["strategy"]): dict(row) for row in rows}
        if weight_date:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """SELECT strategy,target_weight,smooth_weight,prev_smooth_weight,
                                  effective_exposure,circuit_breaker_active,circuit_breaker_reason
                           FROM chenyiyun.ads_rolling_strategy_weights
                           WHERE calc_date=:calc_date"""
                    ),
                    {"calc_date": weight_date},
                ).mappings()
                weight_rows = {str(row["strategy"]): dict(row) for row in rows}
    except Exception as exc:
        warnings.append(f"rolling_query_failed:{type(exc).__name__}")

    strategies = sorted(set(perf_rows) | set(weight_rows))
    rows = []
    for strategy in strategies:
        merged = {"strategy": strategy}
        merged.update(perf_rows.get(strategy, {}))
        merged.update(weight_rows.get(strategy, {}))
        rows.append(merged)
    rows.sort(
        key=lambda row: (
            -(_safe_float(row.get("final_score")) or -1.0),
            str(row.get("strategy") or ""),
        )
    )

    effective_exposure = None
    circuit_breaker_active = False
    circuit_breaker_reason = ""
    for row in rows:
        if effective_exposure is None:
            effective_exposure = _safe_float(row.get("effective_exposure"))
        circuit_breaker_active = circuit_breaker_active or bool(
            int(_safe_float(row.get("circuit_breaker_active")) or 0)
        )
        if not circuit_breaker_reason and row.get("circuit_breaker_reason"):
            circuit_breaker_reason = str(row["circuit_breaker_reason"])

    if perf_date and perf_date != review_date:
        warnings.append(f"rolling_performance_stale:{perf_date}")
    if weight_date and weight_date != review_date:
        warnings.append(f"rolling_weights_stale:{weight_date}")
    if not rows:
        warnings.append("rolling_score_rows_empty")

    return {
        "review_date": review_date,
        "performance_date": perf_date,
        "weight_date": weight_date,
        "effective_exposure": effective_exposure,
        "circuit_breaker_active": circuit_breaker_active,
        "circuit_breaker_reason": circuit_breaker_reason,
        "rows": rows,
        "warnings": sorted(set(warnings)),
    }


def build_candidate_score_section(candidates: list[dict[str, Any]]) -> str:
    """Preserve the actionable candidate score content from the old card."""
    rows = list(candidates or [])
    rows.sort(
        key=lambda row: (
            int(_safe_float(row.get("rank_no") or row.get("rank")) or 999999),
            str(row.get("symbol") or row.get("ts_code") or ""),
        )
    )
    lines = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "【今日候选评分】",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not rows:
        lines.append("- 无候选评分记录。")
        return "\n".join(lines)

    for row in rows[:5]:
        rank = int(_safe_float(row.get("rank_no") or row.get("rank")) or 0)
        symbol = str(row.get("symbol") or row.get("ts_code") or "-")
        name = str(row.get("stock_name") or row.get("name") or symbol)
        lines.append(
            f"#{rank} {name}({symbol}) | "
            f"排序分 {_num(row.get('rank_score'), 1)} | "
            f"动态因子 {_num(row.get('dynamic_factor_score'), 1)} | "
            f"流动性 {_num(row.get('liquidity_detail_score'), 1)} | "
            f"目标权重 {_pct(row.get('effective_weight') or row.get('target_weight'))}"
        )
    if len(rows) > 5:
        lines.append(f"其余候选：{len(rows) - 5}只（完整明细保留在生产候选产物与数据库）")
    lines.append("说明：候选分数用于排序与订单草案，不代表资金授权或保证收益。")
    return "\n".join(lines)


def build_rolling_score_section(snapshot: dict[str, Any]) -> str:
    """Render a compact section for the integrated Feishu digest."""
    rows = list(snapshot.get("rows") or [])
    lines = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "【滚动策略评分与权重】",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        (
            f"表现日期：{snapshot.get('performance_date') or '-'} | "
            f"权重日期：{snapshot.get('weight_date') or '-'} | "
            f"有效敞口：{_pct(snapshot.get('effective_exposure'), 0)}"
        ),
    ]
    if snapshot.get("circuit_breaker_active"):
        lines.append(
            f"⚠️ 熔断：{snapshot.get('circuit_breaker_reason') or '已触发，原因未记录'}"
        )
    else:
        lines.append("✅ 轮动熔断：未触发")

    if not rows:
        lines.append("- 暂无可用滚动评分；收益复盘主体仍保留，评分部分标记为缺失。")
    else:
        lines.append("评分 Top3：")
        for index, row in enumerate(rows[:3], start=1):
            lines.append(
                f"#{index} {row.get('strategy')}: "
                f"评分 {_num(row.get('final_score'), 1)} | "
                f"平滑权重 {_pct(row.get('smooth_weight'))} | "
                f"3月收益 {_pct(row.get('total_return'))} | "
                f"Sharpe {_num(row.get('sharpe'))} | "
                f"Calmar {_num(row.get('calmar'))} | "
                f"MDD {_pct(row.get('max_drawdown'))}"
            )

        active = [
            row for row in rows
            if (_safe_float(row.get("smooth_weight")) or 0.0) >= 0.001
        ]
        active.sort(
            key=lambda row: -(_safe_float(row.get("smooth_weight")) or 0.0)
        )
        if active:
            lines.append("T+1研究权重：")
            for row in active[:8]:
                lines.append(
                    f"- {row.get('strategy')}: {_pct(row.get('smooth_weight'))}"
                )
            if len(active) > 8:
                lines.append(f"- 其余活跃策略：{len(active) - 8}个")
        allocated = sum(
            _safe_float(row.get("smooth_weight")) or 0.0 for row in active
        )
        idle = max(0.0, 1.0 - allocated)
        if idle >= 0.001:
            lines.append(f"- 闲置/现金：{_pct(idle)}")

    warnings = list(snapshot.get("warnings") or [])
    if warnings:
        lines.append("数据提醒：" + "；".join(warnings[:5]))
    lines.append("说明：滚动评分只决定研究轮动权重，不替代生产风险总闸或资金授权。")
    return "\n".join(lines)


def run_integrated_review(args: argparse.Namespace) -> dict[str, Any]:
    notify_requested = bool(args.notify_feishu)
    args.notify_feishu = False
    payload = base_review.run_review(args)
    review_date = str(payload["params"]["review_date"])

    engine = create_engine(build_sqlalchemy_url())
    try:
        snapshot = load_rolling_score_snapshot(engine, review_date)
        candidates = list((payload.get("current") or {}).get("candidates") or [])
        candidate_section = build_candidate_score_section(candidates)
        score_section = build_rolling_score_section(snapshot)
        feishu_path = Path(payload["outputs"]["feishu_text_path"])
        integrated_text = (
            feishu_path.read_text(encoding="utf-8").rstrip()
            + candidate_section
            + score_section
            + "\n"
        )
        feishu_path.write_text(integrated_text, encoding="utf-8")

        payload["rolling_strategy_scores"] = snapshot
        payload["notification_mode"] = "INTEGRATED_DAILY_STRATEGY_DIGEST"
        payload["notify_result"] = None
        payload["notify_detail"] = None

        if notify_requested:
            compact_date = review_date.replace("-", "")
            send_text = integrated_text
            if bool(getattr(args, "historical_reissue", False)):
                send_text = "【历史补发】\n" + send_text
            ok, reason = send_feishu_text_audited(
                engine,
                send_text,
                business_date=compact_date,
                notification_type="trusted_strategy_performance_review",
                task_name="trusted_strategy_performance_review",
                dedupe_key=f"trusted_strategy_performance_review:{compact_date}",
            )
            # The existing verifier treats "ok" as the success contract. A
            # successful idempotent rerun is therefore normalized to "ok" and
            # its exact delivery disposition is retained separately.
            payload["notify_result"] = "ok" if ok else reason
            payload["notify_detail"] = reason
            if not ok:
                print(f"[WARN] integrated Feishu digest queued/failed: {reason}")

        json_path = Path(payload["outputs"]["json_path"])
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return payload
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one integrated strategy performance and score digest."
    )
    parser.add_argument("--date", default=None)
    parser.add_argument("--notify-feishu", action="store_true")
    parser.add_argument("--historical-reissue", action="store_true")
    parser.add_argument(
        "--review-window-days", type=int,
        default=base_review.DEFAULT_REVIEW_WINDOW_DAYS,
    )
    parser.add_argument("--allow-substitute-diagnostic", action="store_true")
    parser.add_argument("--output-root", default=str(base_review.DEFAULT_OUTPUT_ROOT))
    resolved = str(base_review._resolve_backtest_default(base_review._BACKTEST_DIR_CACHE))
    parser.add_argument("--vol-backtest-dir", default=resolved)
    parser.add_argument("--vol-backtest-strategy", default=None)
    parser.add_argument("--adaptive-v22-backtest-dir", default=resolved)
    parser.add_argument("--dual-3m-backtest-dir", default=resolved)
    return parser


def main() -> None:
    payload = run_integrated_review(build_parser().parse_args())
    print(
        json.dumps(
            {
                "status": "SUCCESS",
                "params": payload["params"],
                "outputs": payload["outputs"],
                "notification_mode": payload["notification_mode"],
                "notify_result": payload["notify_result"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
