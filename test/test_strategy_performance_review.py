import os
from datetime import datetime, timedelta

import pandas as pd
import pytest

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from scripts.ops import run_strategy_performance_review as review
from web import app as web_app


def _nav_frame(days=70, strategy=None, start="2026-03-01"):
    strategy = strategy or review.DEFAULT_STRATEGY
    dates = pd.bdate_range(start=start, periods=days)
    nav = [1.0 + idx * 0.002 for idx in range(days)]
    if days > 20:
        nav[20] *= 0.94
    return pd.DataFrame(
        {
            "strategy": strategy,
            "trade_date": [d.strftime("%Y-%m-%d") for d in dates],
            "nav": nav,
            "gross_exposure": [0.7] * days,
        }
    )


def test_compute_rolling_window_metrics_uses_recent_63_trade_days():
    frame = _nav_frame()
    review_date = frame.iloc[-1]["trade_date"]

    result = review.compute_rolling_window_metrics(frame, review.DEFAULT_STRATEGY, review_date, 63)

    assert result["status"] == "PASS"
    assert result["freshness_ok"] is True
    assert result["actual_trade_days"] == 63
    assert result["requested_trade_days"] == 63
    assert result["total_return"] > 0
    assert result["max_drawdown"] < 0
    assert result["current_drawdown"] == 0
    assert result["win_rate"] is not None
    assert result["avg_gross_exposure"] == pytest.approx(0.7)


def test_compute_rolling_window_metrics_marks_stale_nav():
    frame = _nav_frame(days=20)

    result = review.compute_rolling_window_metrics(frame, review.DEFAULT_STRATEGY, "2026-06-30", 63)

    assert result["status"] == "STALE"
    assert result["freshness_ok"] is False
    assert result["warnings"]


def test_compute_rolling_window_metrics_fails_closed_on_missing_columns():
    frame = pd.DataFrame({"strategy": [review.DEFAULT_STRATEGY], "trade_date": ["2026-06-01"]})

    with pytest.raises(RuntimeError, match="missing columns"):
        review.compute_rolling_window_metrics(frame, review.DEFAULT_STRATEGY, "2026-06-01", 63)


def test_format_feishu_promotes_recent_three_month_metrics():
    payload = {
        "params": {"review_date": "2026-06-24"},
        "backtests": {
            "primary": {
                "resolved_strategy": "production_governed_vol_position",
                "summary": {
                    "first_date": "2026-01-01",
                    "last_date": "2026-06-24",
                    "trading_days": 100,
                    "total_return": 0.2,
                    "annualized_return": 0.1,
                    "max_drawdown": -0.08,
                    "final_equity": 600000,
                },
                "rolling_window_3m": {
                    "status": "PASS",
                    "window_start": "2026-03-24",
                    "window_end": "2026-06-24",
                    "actual_trade_days": 63,
                    "requested_trade_days": 63,
                    "total_return": 0.06,
                    "annualized_return": 0.25,
                    "max_drawdown": -0.04,
                    "current_drawdown": -0.01,
                    "win_rate": 0.55,
                    "worst_day": -0.02,
                    "volatility": 0.18,
                    "sharpe": 1.2,
                    "calmar": 6.25,
                    "avg_gross_exposure": 0.7,
                    "warnings": [],
                },
            },
            "adaptive_market_style_v22": {
                "summary": {
                    "first_date": "2026-01-01",
                    "last_date": "2026-06-24",
                    "trading_days": 100,
                    "total_return": 0.12,
                    "annualized_return": 0.08,
                    "max_drawdown": -0.05,
                }
            },
        },
        "current": {
            "candidate_summary": {"rows": 1, "weight_sum": 0.7, "industry_counts": {"测试": 1}},
            "order_summary": {"rows": 0, "buy_orders": 0, "sell_orders": 0, "planned_amount": 0},
            "shadow_summary": {},
            "shadow_history": {},
            "live": {"snapshot": {}, "warnings": []},
            "candidates": [{"rank_no": 1, "symbol": "000001", "stock_name": "A", "industry": "测试", "effective_weight": 0.7}],
        },
        "judgement": {"decision": "继续运行", "risk_governor": {"risk_governor_version": "v1", "target_position_ratio": 0.5}},
        "outputs": {"markdown_path": "/tmp/review.md"},
    }

    text = review._format_feishu(payload)

    assert "最近3个月收益评估" in text
    assert "交易日 63/63" in text
    assert "收益 6.00%" in text
    assert "实际回测策略" in text
    assert "当前回测窗口收益/回撤" in text
    assert "主策略三年" not in text


def test_task_command_includes_notify_and_review_window():
    parts = web_app._build_task_script_parts("trusted_strategy_performance_review", {"datestr": "20260624"})

    assert "--notify-feishu" in parts
    assert "--review-window-days" in parts
    assert "--allow-substitute-diagnostic" in parts
    assert "63" in parts
    assert parts[-2:] == ["--date", "20260624"]


def test_performance_review_verifier_requires_rolling_3m_and_notify(tmp_path, monkeypatch):
    fake_root = tmp_path / "web"
    out_dir = tmp_path / "exports" / "production_strategy_reviews" / "20260624_210000_20260624"
    out_dir.mkdir(parents=True)
    md_path = out_dir / "strategy_performance_review.md"
    json_path = out_dir / "strategy_performance_review.json"
    md_path.write_text("# report\n", encoding="utf-8")
    payload = {
        "params": {"review_date": "2026-06-24", "strategy": web_app.TRUSTED_PRODUCTION_STRATEGY},
        "current": {"candidate_summary": {"rows": 1}},
        "backtests": {
            "primary": {
                "summary": {"total_return": 0.1},
                "rolling_window_3m": {
                    "status": "STALE",
                    "freshness_ok": False,
                    "actual_trade_days": 63,
                    "requested_trade_days": 63,
                    "total_return": 0.01,
                    "max_drawdown": -0.01,
                },
            }
        },
        "judgement": {"decision": "继续运行"},
        "outputs": {"markdown_path": str(md_path)},
        "notify_result": "ok",
    }
    json_path.write_text(review.json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    now = datetime.now()
    os.utime(json_path, (now.timestamp(), now.timestamp()))
    monkeypatch.setattr(web_app.app, "root_path", str(fake_root))

    ok, lines = web_app._verify_trusted_strategy_performance_review_result(now - timedelta(seconds=5), now + timedelta(seconds=5))

    assert ok is False
    assert any("rolling_3m_ok=False" in line for line in lines)
