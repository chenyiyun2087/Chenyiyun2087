"""Shared Feishu notification module for all production ops scripts.

Consolidates the duplicated _load_feishu_webhook / _send_feishu_text functions
that previously existed in three separate scripts. Also provides a standardized
strategy identity block for consistent governor/selection strategy display.
"""

from __future__ import annotations

import json
import os
import ssl
from typing import TYPE_CHECKING, Optional
from urllib import error, request

if TYPE_CHECKING:
    from sqlalchemy import Engine

from scripts.ops.production_config import load_production_config
from scripts.strategy_display import strategy_display_name


def _get_text_sql():
    """Lazy import of sqlalchemy.text to avoid import errors in test environments."""
    from sqlalchemy import text as _text
    return _text


def load_feishu_webhook(engine: Engine) -> str | None:
    """Load Feishu webhook URL from env var FEISHU_WEBHOOK_URL or DB.

    Priority:
    1. Environment variable FEISHU_WEBHOOK_URL
    2. Database table app_notification_channel (channel_key='feishu', enabled=1)
    """
    env_url = str(os.environ.get("FEISHU_WEBHOOK_URL") or "").strip()
    if env_url.startswith(("http://", "https://")):
        return env_url

    text_fn = _get_text_sql()

    # Check if table exists before querying (avoid errors on fresh DB)
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text_fn(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'chenyiyun' "
                    "AND table_name = 'app_notification_channel' LIMIT 1"
                )
            ).scalar()
        if not exists:
            return None
    except Exception:
        return None

    sql = text_fn(
        """
        SELECT webhook_url
        FROM chenyiyun.app_notification_channel
        WHERE channel_key = 'feishu'
          AND enabled = 1
          AND webhook_url IS NOT NULL
          AND TRIM(webhook_url) <> ''
        LIMIT 1
        """
    )
    try:
        with engine.connect() as conn:
            value = conn.execute(sql).scalar()
    except Exception:
        return None

    url = str(value or "").strip()
    return url if url.startswith(("http://", "https://")) else None


def send_feishu_text(webhook_url: str, content: str) -> tuple[bool, str]:
    """Send a Feishu text message. Returns (success, detail).

    TLS certificate errors are treated as hard failures — no insecure fallback.
    Callers must handle failures by logging, alerting, and retrying with backoff.
    """
    payload = json.dumps(
        {"msg_type": "text", "content": {"text": content}},
        ensure_ascii=False,
    ).encode("utf-8")

    req = request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=12) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        body = (
            exc.read().decode("utf-8", errors="ignore")
            if hasattr(exc, "read")
            else str(exc)
        )
        return False, f"http_error={exc.code}; body={body[:200]}"
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return False, (
                f"TLS_CERTIFICATE_ERROR: certificate verification failed for "
                f"{webhook_url[:60]}... — refusing to send without verified TLS. "
                f"Check Feishu webhook certificate validity."
            )
        return False, f"url_error={exc}"
    except Exception as exc:
        return False, f"exception={exc}"

    if status < 200 or status >= 300:
        return False, f"http_status={status}; body={body[:200]}"

    try:
        parsed = json.loads(body) if body else {}
    except Exception:
        return True, "http_ok"

    if isinstance(parsed, dict):
        errcode = parsed.get("errcode")
        code = parsed.get("code")
        if errcode not in (None, 0, "0"):
            return False, f"errcode={errcode}; body={body[:200]}"
        if code not in (None, 0, "0"):
            return False, f"code={code}; body={body[:200]}"

    return True, "ok"


def ensure_notification_delivery_table(engine: "Engine") -> None:
    """Create the notification delivery audit table if it does not exist."""
    text_fn = _get_text_sql()
    with engine.begin() as conn:
        conn.execute(
            text_fn(
                """
                CREATE TABLE IF NOT EXISTS chenyiyun.app_notification_delivery (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    business_date VARCHAR(8) NOT NULL,
                    notification_type VARCHAR(64) NOT NULL,
                    task_name VARCHAR(64) NULL,
                    channel_key VARCHAR(32) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    reason TEXT NULL,
                    dedupe_key VARCHAR(160) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    KEY idx_notification_delivery_date (business_date, task_name, notification_type),
                    KEY idx_notification_delivery_dedupe (dedupe_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        )


def record_notification_delivery(
    engine: "Engine",
    *,
    business_date: str,
    notification_type: str,
    task_name: str | None,
    channel_key: str,
    status: str,
    reason: str,
    dedupe_key: str | None = None,
) -> None:
    """Persist one notification delivery attempt."""
    ensure_notification_delivery_table(engine)
    text_fn = _get_text_sql()
    with engine.begin() as conn:
        conn.execute(
            text_fn(
                """
                INSERT INTO chenyiyun.app_notification_delivery
                    (business_date, notification_type, task_name, channel_key, status, reason, dedupe_key)
                VALUES (:business_date, :notification_type, :task_name, :channel_key, :status, :reason, :dedupe_key)
                """
            ),
            {
                "business_date": str(business_date or ""),
                "notification_type": str(notification_type or "unknown"),
                "task_name": task_name,
                "channel_key": str(channel_key or "feishu"),
                "status": str(status or "unknown"),
                "reason": str(reason or "")[:2000],
                "dedupe_key": dedupe_key,
            },
        )


def send_feishu_text_audited(
    engine: "Engine",
    content: str,
    *,
    business_date: str,
    notification_type: str,
    task_name: str | None = None,
    dedupe_key: str | None = None,
) -> tuple[bool, str]:
    """Send a Feishu message and record the delivery result."""
    webhook = load_feishu_webhook(engine)
    if not webhook:
        record_notification_delivery(
            engine,
            business_date=business_date,
            notification_type=notification_type,
            task_name=task_name,
            channel_key="feishu",
            status="no_webhook",
            reason="no enabled Feishu webhook",
            dedupe_key=dedupe_key,
        )
        return False, "no_webhook"

    ok, reason = send_feishu_text(webhook, content)
    record_notification_delivery(
        engine,
        business_date=business_date,
        notification_type=notification_type,
        task_name=task_name,
        channel_key="feishu",
        status="ok" if ok else "failed",
        reason=reason,
        dedupe_key=dedupe_key,
    )
    return ok, reason


def strategy_identity_block() -> str:
    """Return a standardized strategy identity block for all Feishu cards.

    Shows both governor and selection strategies with their Chinese display names
    and internal strategy IDs.
    """
    config = load_production_config()
    governor = str(config.get("primary_strategy", "-"))
    selection = str(config.get("primary_selection_strategy", governor))
    risk_profile = str(config.get("risk_profile", "-"))
    position_ratio = config.get("position_ratio", 0.70)

    return (
        f"生产策略：{strategy_display_name(governor, include_id=True)}\n"
        f"选股内核：{strategy_display_name(selection, include_id=True)}\n"
        f"风险档位：{risk_profile} / 当前目标仓位 {int(position_ratio * 100)}%"
    )


# ---------------------------------------------------------------------------
# Four-card Feishu notification system
# ---------------------------------------------------------------------------


def format_trade_card(
    asof_date: str,
    candidate_count: int,
    order_count: int,
    buy_count: int,
    sell_count: int,
    buy_amount: float,
    sell_amount: float,
    top5_lines: list[str],
    order_lines: list[str],
    data_status: str = "READY",
) -> str:
    """Format a Trade Card (交易指令卡) for Feishu.

    Shows candidate Top5, order drafts, and data readiness status.
    """
    lines = [
        "【交易指令卡】",
        f"信号日：{asof_date}",
        strategy_identity_block(),
        f"数据就绪：{data_status}",
        f"候选数：{candidate_count} | 订单数：{order_count}（BUY {buy_count} / SELL {sell_count}）",
        f"买入额：{buy_amount:,.2f} | 卖出额：{sell_amount:,.2f}",
        "",
        "候选 Top5：",
    ]
    lines.extend(top5_lines[:5] if top5_lines else ["- 无候选"])
    lines.append("")
    lines.append("订单草案：")
    lines.extend(order_lines[:10] if order_lines else ["- 无需调仓"])

    return "\n".join(lines)


def format_health_card(
    asof_date: str,
    overall_grade: str,
    execution_grade: str,
    performance_grade: str,
    risk_grade: str,
    data_grade: str,
    warnings: list[str] | None = None,
) -> str:
    """Format a Health Card (风险健康卡) for Feishu.

    Shows 4-dimension GREEN/YELLOW/RED health grades and active warnings.
    """
    grade_emoji = {"GREEN": "✅", "YELLOW": "⚠️", "RED": "🚨"}

    lines = [
        f"{grade_emoji.get(overall_grade, '❓')} 【风险健康卡】",
        f"日期：{asof_date}",
        f"总体：{overall_grade}",
        strategy_identity_block(),
        "",
        "分项评分：",
        f"- 执行质量：{execution_grade} {grade_emoji.get(execution_grade, '')}",
        f"- 策略表现：{performance_grade} {grade_emoji.get(performance_grade, '')}",
        f"- 风险总闸：{risk_grade} {grade_emoji.get(risk_grade, '')}",
        f"- 数据完整性：{data_grade} {grade_emoji.get(data_grade, '')}",
    ]

    if warnings:
        lines.extend(["", "⚠️ 告警："])
        lines.extend(f"- {w}" for w in warnings[:5])

    return "\n".join(lines)


def format_shadow_card(
    signal_date: str,
    execution_date: str,
    total_orders: int,
    buy_orders: int,
    sell_orders: int,
    executable_orders: int,
    blocked_orders: int,
    warning_orders: int,
    avg_slippage_bps: float,
    max_slippage_bps: float,
    validation_status: str,
    validation_actions: str,
    bad_order_lines: list[str] | None = None,
    large_slippage_lines: list[str] | None = None,
) -> str:
    """Format a Shadow Card (影子候选卡) for Feishu.

    Shows shadow execution quality with explicit "research only" disclaimer.
    """
    lines = [
        "【影子盘成交监控】",
        f"信号日：{signal_date}，执行日：{execution_date}",
        strategy_identity_block(),
        f"订单：{total_orders}（BUY {buy_orders} / SELL {sell_orders}）",
        f"可成交：{executable_orders}，不可成交：{blocked_orders}，大滑点警告：{warning_orders}",
        f"平均不利滑点：{avg_slippage_bps:.1f} bps",
        f"最大不利滑点：{max_slippage_bps:.1f} bps",
        f"验收结果：{validation_status} / {validation_actions}",
        "",
        "⚠️ 研究候选，不构成当前交易指令",
    ]

    if bad_order_lines:
        lines.extend(["", "不可成交订单："])
        lines.extend(bad_order_lines[:10])

    if large_slippage_lines:
        lines.extend(["", "大滑点警告："])
        lines.extend(large_slippage_lines[:5])

    return "\n".join(lines)


def format_incident_card(
    incident_type: str,
    incident_detail: str,
    asof_date: str,
    severity: str = "RED",
    action_taken: str = "",
) -> str:
    """Format an Incident & Freeze Card (事故冻结卡) for Feishu.

    Only pushed on exceptions: data anomalies, reconciliation failures,
    excessive drawdown, order matching errors.
    """
    severity_emoji = {"RED": "🚨", "YELLOW": "⚠️"}

    lines = [
        f"{severity_emoji.get(severity, '🚨')} 【事故冻结卡】",
        f"日期：{asof_date}",
        f"类型：{incident_type}",
        f"严重程度：{severity}",
        "",
        strategy_identity_block(),
        "",
        f"详情：{incident_detail}",
    ]

    if action_taken:
        lines.append(f"已执行动作：{action_taken}")

    return "\n".join(lines)
