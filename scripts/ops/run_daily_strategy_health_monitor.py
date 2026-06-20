"""Daily strategy health monitor — multi-window performance check.

Assesses strategy health across 4 dimensions:
  1. Execution quality (shadow monitor data, last 5 trading days)
  2. Strategy performance (live_daily_snapshots, 20/60-day windows)
  3. Risk governor state (ads_production_risk_decisions)
  4. Data integrity (DataReadinessGate)

Each dimension returns GREEN / YELLOW / RED. Overall grade is the worst of the four.
Persists results to chenyiyun.ads_strategy_health_daily and optionally sends Feishu.
"""

from __future__ import annotations

import argparse
import json as json_module
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DDL_HEALTH_TABLE = """
CREATE TABLE IF NOT EXISTS chenyiyun.ads_strategy_health_daily (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    as_of_date DATE NOT NULL,
    overall_grade VARCHAR(16) NOT NULL COMMENT 'GREEN / YELLOW / RED',
    execution_grade VARCHAR(16) COMMENT 'Shadow monitor based grade',
    performance_grade VARCHAR(16) COMMENT 'Multi-window performance grade',
    risk_governor_grade VARCHAR(16) COMMENT 'Risk governor state grade',
    data_integrity_grade VARCHAR(16) COMMENT 'DataReadinessGate grade',
    execution_detail JSON,
    performance_detail JSON,
    risk_detail JSON,
    data_detail JSON,
    warnings JSON,
    active_strategies JSON COMMENT 'Current primary + selection strategy',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY idx_date (as_of_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Daily strategy health assessment';
"""

# Thresholds for GREEN / YELLOW grading
GREEN_THRESHOLDS: dict[str, float] = {
    "max_slippage_bps": 50,
    "max_blocked_ratio": 0.10,
    "max_consecutive_bad_days": 1,
    "min_executable_ratio": 0.85,
}
YELLOW_THRESHOLDS: dict[str, float] = {
    "max_slippage_bps": 150,
    "max_blocked_ratio": 0.25,
    "max_consecutive_bad_days": 2,
    "min_executable_ratio": 0.65,
}


def _grade_value(
    value: float,
    green_at: float,
    yellow_at: float,
    higher_is_better: bool = True,
) -> str:
    """Grade a scalar metric as GREEN, YELLOW, or RED."""
    if higher_is_better:
        if value >= green_at:
            return "GREEN"
        if value >= yellow_at:
            return "YELLOW"
    else:
        if value <= green_at:
            return "GREEN"
        if value <= yellow_at:
            return "YELLOW"
    return "RED"


def ensure_health_table(engine) -> None:
    """Create the health table if it doesn't exist."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(DDL_HEALTH_TABLE))


# ---------------------------------------------------------------------------
# Dimension graders
# ---------------------------------------------------------------------------


def grade_execution(engine, as_of_date: str) -> dict[str, Any]:
    """Grade execution quality from recent shadow monitoring data."""
    from sqlalchemy import text

    sql = text(
        """
        SELECT execution_date, validation_status, validation_actions,
               total_orders, executable_orders, blocked_orders, warning_orders,
               avg_slippage_bps, max_adverse_slippage_bps,
               shadow_vs_theory_gap
        FROM chenyiyun.ads_trusted_strategy_shadow_daily
        WHERE execution_date <= :asof
        ORDER BY execution_date DESC
        LIMIT 5
        """
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, {"asof": as_of_date}).mappings().fetchall()
    except Exception as exc:
        return {"grade": "RED", "reason": f"query_error={exc}", "detail": {}}

    if not rows:
        return {"grade": "YELLOW", "reason": "no_shadow_data", "detail": {}}

    day_grades: list[dict[str, str]] = []
    detail: dict[str, Any] = {}
    for row in rows:
        d = dict(row)
        date_str = str(d.get("execution_date", ""))
        total = max(int(d.get("total_orders", 0)), 1)
        exec_ratio = int(d.get("executable_orders", 0)) / total
        blocked_ratio = int(d.get("blocked_orders", 0)) / total
        avg_slip = float(d.get("avg_slippage_bps", 0) or 0)

        g = {
            "executable_ratio": _grade_value(
                exec_ratio,
                GREEN_THRESHOLDS["min_executable_ratio"],
                YELLOW_THRESHOLDS["min_executable_ratio"],
            ),
            "blocked_ratio": _grade_value(
                blocked_ratio,
                GREEN_THRESHOLDS["max_blocked_ratio"],
                YELLOW_THRESHOLDS["max_blocked_ratio"],
                higher_is_better=False,
            ),
            "slippage": _grade_value(
                avg_slip,
                GREEN_THRESHOLDS["max_slippage_bps"],
                YELLOW_THRESHOLDS["max_slippage_bps"],
                higher_is_better=False,
            ),
        }
        day_grades.append(g)
        detail[date_str] = {
            **g,
            "total_orders": int(d.get("total_orders", 0)),
            "executable_orders": int(d.get("executable_orders", 0)),
            "blocked_orders": int(d.get("blocked_orders", 0)),
            "avg_slippage_bps": avg_slip,
            "validation_status": d.get("validation_status"),
        }

    all_grades = [g for day in day_grades for g in day.values()]
    if "RED" in all_grades:
        overall = "RED"
    elif "YELLOW" in all_grades:
        overall = "YELLOW"
    else:
        overall = "GREEN"

    return {"grade": overall, "detail": detail, "days_checked": len(rows)}


def _compute_window_metrics(
    daily_returns: list[float], nav_series: list[float]
) -> dict[str, Any]:
    """Compute real 20/60 day performance metrics from sorted returns and NAV."""
    if not daily_returns:
        return {"return": 0, "max_dd": 0, "ann_vol": 0, "worst_day": 0, "positive_days": 0, "negative_days": 0}

    total_return = 1.0
    for r in daily_returns:
        total_return *= (1.0 + r)
    total_return -= 1.0

    # Max drawdown from NAV peak
    peak = nav_series[0] if nav_series else 1.0
    max_dd = 0.0
    for nav in nav_series:
        if nav > peak:
            peak = nav
        dd = (nav / peak - 1.0) if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    ann_vol = 0.0
    if len(daily_returns) >= 5:
        import math
        std = (sum((r - sum(daily_returns) / len(daily_returns)) ** 2
                   for r in daily_returns) / (len(daily_returns) - 1)) ** 0.5
        ann_vol = std * (252 ** 0.5)

    return {
        "return": round(total_return, 6),
        "max_dd": round(max_dd, 6),
        "ann_vol": round(ann_vol, 6),
        "worst_day": round(min(daily_returns), 6) if daily_returns else 0,
        "positive_days": sum(1 for r in daily_returns if r > 0),
        "negative_days": sum(1 for r in daily_returns if r < 0),
    }


def grade_performance(engine, as_of_date: str) -> dict[str, Any]:
    """Grade multi-window performance from live_daily_snapshots.

    Computes real 20-day and 60-day metrics:
      - return, max drawdown, annualized volatility, worst single day
    Also fetches recent shadow execution quality (slippage, unfilled ratio).
    """
    from sqlalchemy import text

    # Load live snapshots filtered by strategy context
    sql_snap = text(
        """
        SELECT trade_date, daily_return, total_equity
        FROM chenyiyun.live_daily_snapshots
        WHERE trade_date <= :asof
        ORDER BY trade_date ASC
        """
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql_snap, {"asof": as_of_date}).mappings().fetchall()
    except Exception as exc:
        return {"grade": "YELLOW", "reason": f"query_error={exc}", "detail": {}}

    if len(rows) < 5:
        return {"grade": "YELLOW", "reason": f"insufficient_snapshots({len(rows)})", "detail": {}}

    # Full series in chronological order
    returns_all = [float(r.get("daily_return", 0) or 0) for r in rows]
    nav_all = [float(r.get("total_equity", 0) or 0) for r in rows]

    # 20-day window (most recent 20)
    ret_20 = returns_all[-20:] if len(returns_all) >= 20 else returns_all
    nav_20 = nav_all[-20:] if len(nav_all) >= 20 else nav_all
    # 60-day window
    ret_60 = returns_all[-60:] if len(returns_all) >= 60 else returns_all
    nav_60 = nav_all[-60:] if len(nav_all) >= 60 else nav_all

    m20 = _compute_window_metrics(ret_20, nav_20)
    m60 = _compute_window_metrics(ret_60, nav_60)

    # Fetch shadow execution quality for recent 20 trading days
    sql_shadow = text(
        """
        SELECT AVG(avg_slippage_bps) AS avg_slip,
               AVG(CAST(blocked_orders AS DECIMAL(10,4)) / NULLIF(total_orders, 0)) AS unfilled_ratio
        FROM chenyiyun.ads_trusted_strategy_shadow_daily
        WHERE execution_date <= :asof
        ORDER BY execution_date DESC
        LIMIT 20
        """
    )
    try:
        with engine.connect() as conn:
            sr = conn.execute(sql_shadow, {"asof": as_of_date}).mappings().first()
        shadow_avg_slip = float(sr["avg_slip"] or 0) if sr else None
        shadow_unfilled = float(sr["unfilled_ratio"] or 0) if sr else None
    except Exception:
        shadow_avg_slip = None
        shadow_unfilled = None

    # Grade based on 20-day return and drawdown
    ret20 = m20["return"]
    dd20 = m20["max_dd"]
    if ret20 > 0.02 and dd20 > -0.05:
        overall = "GREEN"
    elif ret20 > -0.05 and dd20 > -0.15:
        overall = "YELLOW"
    else:
        overall = "RED"

    return {
        "grade": overall,
        "detail": {
            "window_20d": m20,
            "window_60d": m60,
            "shadow_20d_avg_slippage_bps": shadow_avg_slip,
            "shadow_20d_avg_unfilled_ratio": shadow_unfilled,
            "snapshots_total": len(rows),
        },
    }


def grade_risk_governor(engine, as_of_date: str) -> dict[str, Any]:
    """Check current risk governor state."""
    from sqlalchemy import text

    sql = text(
        """
        SELECT risk_decision, target_position_ratio, allow_new_buys, reasons_json
        FROM chenyiyun.ads_production_risk_decisions
        WHERE trade_date = :asof
        ORDER BY id DESC LIMIT 1
        """
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(sql, {"asof": as_of_date}).mappings().first()
    except Exception as exc:
        return {"grade": "YELLOW", "reason": f"query_error={exc}", "detail": {}}

    if not row:
        return {"grade": "YELLOW", "reason": "no_risk_decision_for_today", "detail": {}}

    d = dict(row)
    decision = str(d.get("risk_decision", ""))
    detail: dict[str, Any] = {
        "risk_decision": decision,
        "target_position_ratio": float(d.get("target_position_ratio", 0) or 0),
        "allow_new_buys": bool(d.get("allow_new_buys", True)),
    }

    if decision == "freeze_buy":
        return {"grade": "RED", "reason": f"risk_decision={decision}", "detail": detail}
    if decision in ("defensive_only", "reduce_position"):
        return {"grade": "YELLOW", "reason": f"risk_decision={decision}", "detail": detail}
    return {"grade": "GREEN", "reason": f"risk_decision={decision}", "detail": detail}


def grade_data_integrity(engine, as_of_date_str: str) -> dict[str, Any]:
    """Check data freshness via DataReadinessGate."""
    try:
        from scripts.ops.data_readiness_gate import DataReadinessGate

        gate = DataReadinessGate(engine)
        as_of_date = date.fromisoformat(as_of_date_str[:10])
        result = gate.all_checks(as_of_date)
    except Exception as exc:
        return {"grade": "RED", "reason": f"gate_error={exc}", "detail": {}}

    if result["status"] == "READY":
        return {"grade": "GREEN", "detail": result}
    if result["status"] == "READY_WITH_WARNING":
        return {"grade": "YELLOW", "reason": "warnings_present", "detail": result}
    return {"grade": "RED", "reason": "data_blocked", "detail": result}


# ---------------------------------------------------------------------------
# Aggregate and persist
# ---------------------------------------------------------------------------


def build_health_summary(engine, as_of_date: str) -> dict[str, Any]:
    """Build the full health summary dict for a given date."""
    execution = grade_execution(engine, as_of_date)
    performance = grade_performance(engine, as_of_date)
    risk = grade_risk_governor(engine, as_of_date)
    data = grade_data_integrity(engine, as_of_date)

    grades = [
        execution["grade"],
        performance["grade"],
        risk["grade"],
        data["grade"],
    ]

    if "RED" in grades:
        overall = "RED"
    elif "YELLOW" in grades:
        overall = "YELLOW"
    else:
        overall = "GREEN"

    warnings: list[str] = []
    for label, g in [
        ("执行质量", execution),
        ("策略表现", performance),
        ("风险总闸", risk),
        ("数据完整性", data),
    ]:
        if g["grade"] in ("RED", "YELLOW"):
            warnings.append(f"{label} {g['grade']}: {g.get('reason', '')}")

    from scripts.ops.production_config import load_production_config

    config = load_production_config()
    return {
        "as_of_date": as_of_date,
        "overall_grade": overall,
        "execution_grade": execution["grade"],
        "performance_grade": performance["grade"],
        "risk_governor_grade": risk["grade"],
        "data_integrity_grade": data["grade"],
        "execution_detail": execution,
        "performance_detail": performance,
        "risk_detail": risk,
        "data_detail": data,
        "warnings": warnings,
        "active_strategies": {
            "primary_strategy": config.get("primary_strategy", ""),
            "primary_selection_strategy": config.get("primary_selection_strategy", ""),
        },
    }


def persist_health(engine, health: dict[str, Any]) -> None:
    """Write health summary to database."""
    from sqlalchemy import text

    ensure_health_table(engine)

    insert_sql = text(
        """
        INSERT INTO chenyiyun.ads_strategy_health_daily
            (as_of_date, overall_grade, execution_grade, performance_grade,
             risk_governor_grade, data_integrity_grade,
             execution_detail, performance_detail, risk_detail, data_detail,
             warnings, active_strategies)
        VALUES
            (:asof, :overall, :exec, :perf, :risk, :data,
             :exec_d, :perf_d, :risk_d, :data_d, :warnings, :strategies)
        ON DUPLICATE KEY UPDATE
            overall_grade = VALUES(overall_grade),
            execution_grade = VALUES(execution_grade),
            performance_grade = VALUES(performance_grade),
            risk_governor_grade = VALUES(risk_governor_grade),
            data_integrity_grade = VALUES(data_integrity_grade),
            execution_detail = VALUES(execution_detail),
            performance_detail = VALUES(performance_detail),
            risk_detail = VALUES(risk_detail),
            data_detail = VALUES(data_detail),
            warnings = VALUES(warnings),
            active_strategies = VALUES(active_strategies)
        """
    )

    with engine.begin() as conn:
        conn.execute(
            insert_sql,
            {
                "asof": health["as_of_date"],
                "overall": health["overall_grade"],
                "exec": health["execution_grade"],
                "perf": health["performance_grade"],
                "risk": health["risk_governor_grade"],
                "data": health["data_integrity_grade"],
                "exec_d": json_module.dumps(health["execution_detail"], ensure_ascii=False, default=str),
                "perf_d": json_module.dumps(health["performance_detail"], ensure_ascii=False, default=str),
                "risk_d": json_module.dumps(health["risk_detail"], ensure_ascii=False, default=str),
                "data_d": json_module.dumps(health["data_detail"], ensure_ascii=False, default=str),
                "warnings": json_module.dumps(health["warnings"], ensure_ascii=False),
                "strategies": json_module.dumps(health["active_strategies"], ensure_ascii=False),
            },
        )


# ---------------------------------------------------------------------------
# Order permission resolution
# ---------------------------------------------------------------------------

ORDER_PERMISSION_GRADE_RULES = {
    "GREEN": {
        "allow_new_buys": True,
        "emit_orders": True,
        "manual_confirmation_required": False,
        "allow_sell_only": False,
    },
    "YELLOW": {
        "allow_new_buys": True,
        "emit_orders": True,
        "manual_confirmation_required": True,
        "allow_sell_only": False,
    },
    "RED": {
        "allow_new_buys": False,
        "emit_orders": False,
        "manual_confirmation_required": False,
        "allow_sell_only": True,
    },
    "STALE": {
        "allow_new_buys": False,
        "emit_orders": False,
        "manual_confirmation_required": False,
        "allow_sell_only": True,
    },
    "UNKNOWN": {
        "allow_new_buys": True,
        "emit_orders": True,
        "manual_confirmation_required": True,
        "allow_sell_only": False,
    },
}


def get_previous_trading_day_health(engine, as_of_date: str) -> dict[str, Any] | None:
    """Read the most recent health record before as_of_date.

    Returns None if no prior health record exists (e.g., first run).
    Includes _trading_days_behind: number of trading days between the health
    record date and as_of_date. Used for staleness detection.
    """
    from sqlalchemy import text

    sql = text(
        """
        SELECT h.as_of_date, h.overall_grade, h.execution_grade, h.performance_grade,
               h.risk_governor_grade, h.data_integrity_grade, h.warnings, h.active_strategies,
               (SELECT COUNT(*) FROM chenyiyun.dim_trade_cal
                WHERE exchange = 'SSE' AND is_open = 1
                  AND cal_date > h.as_of_date AND cal_date <= :asof
               ) AS _trading_days_behind
        FROM chenyiyun.ads_strategy_health_daily h
        WHERE h.as_of_date < :asof
        ORDER BY h.as_of_date DESC
        LIMIT 1
        """
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(sql, {"asof": as_of_date}).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def resolve_order_permission(
    previous_health: dict[str, Any] | None,
    max_stale_trading_days: int = 1,
) -> dict[str, Any]:
    """Determine order-generation permissions from previous-day health grade.

    Fail-safe rules:
      - No prior health record → YELLOW (manual confirmation; don't silently allow)
      - Health record older than max_stale_trading_days → RED (data is stale, freeze buys)
      - GREEN → normal order generation
      - YELLOW → orders generated but require manual confirmation
      - RED → no new buys, sell-only mode

    Returns a dict with:
      allow_new_buys, emit_orders, manual_confirmation_required, allow_sell_only,
      health_grade, health_date, freeze_reason
    """
    # No prior health record → fail-safe: YELLOW (require human confirmation)
    if previous_health is None:
        return {
            "allow_new_buys": True,
            "emit_orders": True,
            "manual_confirmation_required": True,
            "allow_sell_only": False,
            "health_grade": "UNKNOWN",
            "health_date": None,
            "freeze_reason": "No prior health record — orders require manual confirmation (fail-safe).",
        }

    # Staleness check: if the health record is too old, treat as RED
    health_date_str = str(previous_health.get("as_of_date", ""))
    trading_days_behind = previous_health.get("_trading_days_behind")
    if trading_days_behind is not None and int(trading_days_behind) > max_stale_trading_days:
        return {
            "allow_new_buys": False,
            "emit_orders": False,
            "manual_confirmation_required": False,
            "allow_sell_only": True,
            "health_grade": "STALE",
            "health_date": health_date_str,
            "freeze_reason": (
                f"Health record is {trading_days_behind} trading days old "
                f"(max {max_stale_trading_days}). Data may be unreliable."
            ),
        }

    grade = str(previous_health.get("overall_grade", "UNKNOWN")).upper()
    rules = ORDER_PERMISSION_GRADE_RULES.get(grade, ORDER_PERMISSION_GRADE_RULES["GREEN"])
    health_date = str(previous_health.get("as_of_date", ""))

    freeze_reason = None
    if grade == "RED":
        warnings = previous_health.get("warnings")
        if isinstance(warnings, str):
            import json as _json
            try:
                warnings = _json.loads(warnings)
            except Exception:
                warnings = [warnings]
        freeze_reason = f"Health RED ({health_date}): " + (
            "; ".join(warnings[:3]) if warnings else "no specific warnings"
        )
    elif grade == "YELLOW":
        freeze_reason = f"Health YELLOW ({health_date}): manual confirmation required"

    return {
        "allow_new_buys": bool(rules["allow_new_buys"]),
        "emit_orders": bool(rules["emit_orders"]),
        "manual_confirmation_required": bool(rules["manual_confirmation_required"]),
        "allow_sell_only": bool(rules["allow_sell_only"]),
        "health_grade": grade,
        "health_date": health_date,
        "freeze_reason": freeze_reason,
    }


# ---------------------------------------------------------------------------
# Feishu formatting
# ---------------------------------------------------------------------------


def format_health_feishu(health: dict[str, Any]) -> str:
    """Format health summary as a Feishu text card."""
    grade_emoji = {"GREEN": "✅", "YELLOW": "⚠️", "RED": "🚨"}

    from scripts.ops.feishu_notifier import strategy_identity_block

    lines = [
        f"{grade_emoji.get(health['overall_grade'], '❓')} 【每日策略健康监测】",
        f"日期：{health['as_of_date']}",
        f"总体：{health['overall_grade']}",
        strategy_identity_block(),
        "",
        "分项评分：",
        f"- 执行质量：{health['execution_grade']} {grade_emoji.get(health['execution_grade'], '')}",
        f"- 策略表现：{health['performance_grade']} {grade_emoji.get(health['performance_grade'], '')}",
        f"- 风险总闸：{health['risk_governor_grade']} {grade_emoji.get(health['risk_governor_grade'], '')}",
        f"- 数据完整性：{health['data_integrity_grade']} {grade_emoji.get(health['data_integrity_grade'], '')}",
    ]

    if health["warnings"]:
        lines.extend(["", "⚠️ 告警："])
        lines.extend(f"- {w}" for w in health["warnings"][:5])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def run_health_monitor(engine, as_of_date: str, args: argparse.Namespace) -> dict[str, Any]:
    """Run the full health monitoring cycle."""
    health = build_health_summary(engine, as_of_date)
    persist_health(engine, health)

    notify_result = None
    if args.notify_feishu:
        from scripts.ops.feishu_notifier import load_feishu_webhook, send_feishu_text

        webhook = load_feishu_webhook(engine)
        if webhook:
            ok, reason = send_feishu_text(webhook, format_health_feishu(health))
            notify_result = reason if not ok else "ok"

    return {**health, "notify_result": notify_result, "db_write": True}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run daily strategy health monitor."
    )
    parser.add_argument(
        "--date",
        default=None,
        help="YYYY-MM-DD or YYYYMMDD. Defaults to today.",
    )
    parser.add_argument(
        "--notify-feishu",
        action="store_true",
        help="Send health card via Feishu.",
    )
    _args = parser.parse_args()

    from sqlalchemy import create_engine

    from scoreRank.core.db_config import build_sqlalchemy_url

    engine = create_engine(build_sqlalchemy_url())
    as_of = _args.date or datetime.now().strftime("%Y-%m-%d")
    result = run_health_monitor(engine, as_of, _args)
    print(json_module.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
