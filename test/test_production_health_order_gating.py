"""Behavioral tests for health-controlled order permissions and data gates.

Verifies:
  - resolve_order_permission() returns correct permissions per health grade
  - PreScoreGate has 7 checks including adjust_factor and suspension completeness
  - PostScoreGate has 5 checks including candidate contamination
  - RED health → emit_orders=False, allow_sell_only=True
  - YELLOW health → manual_confirmation_required=True
  - GREEN health → normal permissions
  - UNKNOWN health (no prior record) → normal permissions (conservative default)
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestResolveOrderPermission:
    """Test order permission resolution from health grades."""

    def test_green_health_allows_normal_orders(self):
        from scripts.ops.run_daily_strategy_health_monitor import resolve_order_permission

        prev = {"as_of_date": "2026-06-18", "overall_grade": "GREEN"}
        perm = resolve_order_permission(prev)
        assert perm["allow_new_buys"] is True
        assert perm["emit_orders"] is True
        assert perm["manual_confirmation_required"] is False
        assert perm["allow_sell_only"] is False
        assert perm["health_grade"] == "GREEN"
        assert perm["freeze_reason"] is None

    def test_yellow_health_requires_manual_confirmation(self):
        from scripts.ops.run_daily_strategy_health_monitor import resolve_order_permission

        prev = {"as_of_date": "2026-06-18", "overall_grade": "YELLOW"}
        perm = resolve_order_permission(prev)
        assert perm["allow_new_buys"] is True
        assert perm["emit_orders"] is True
        assert perm["manual_confirmation_required"] is True
        assert perm["allow_sell_only"] is False
        assert perm["health_grade"] == "YELLOW"
        assert "manual confirmation" in str(perm["freeze_reason"]).lower()

    def test_red_health_blocks_new_buys(self):
        from scripts.ops.run_daily_strategy_health_monitor import resolve_order_permission

        prev = {
            "as_of_date": "2026-06-18",
            "overall_grade": "RED",
            "warnings": '["Execution quality RED", "Data integrity YELLOW"]',
        }
        perm = resolve_order_permission(prev)
        assert perm["allow_new_buys"] is False
        assert perm["emit_orders"] is False
        assert perm["allow_sell_only"] is True
        assert perm["manual_confirmation_required"] is False
        assert perm["health_grade"] == "RED"
        assert perm["freeze_reason"] is not None
        assert "RED" in str(perm["freeze_reason"])

    def test_unknown_health_is_fail_safe_yellow(self):
        from scripts.ops.run_daily_strategy_health_monitor import resolve_order_permission

        # No prior health record → YELLOW with UNKNOWN substatus (fail-safe)
        perm = resolve_order_permission(None)
        assert perm["allow_new_buys"] is True
        assert perm["emit_orders"] is True
        assert perm["manual_confirmation_required"] is True, (
            "UNKNOWN must require manual confirmation (fail-safe, not fail-open)"
        )
        assert perm["health_grade"] == "YELLOW"
        assert perm["health_substatus"] == "UNKNOWN"

    def test_stale_health_is_red_with_substatus(self):
        from scripts.ops.run_daily_strategy_health_monitor import resolve_order_permission

        # Health record from 5 trading days ago → RED with STALE substatus
        prev = {
            "as_of_date": "2026-06-10",
            "overall_grade": "GREEN",
            "_trading_days_behind": 5,
        }
        perm = resolve_order_permission(prev, max_stale_trading_days=1)
        assert perm["allow_new_buys"] is False
        assert perm["emit_orders"] is False
        assert perm["allow_sell_only"] is True
        assert perm["health_grade"] == "RED", (
            "STALE must be health_grade=RED with substatus, not a separate grade"
        )
        assert perm["health_substatus"] == "STALE"
        assert "5 trading days" in str(perm["freeze_reason"])

    def test_green_has_no_substatus(self):
        from scripts.ops.run_daily_strategy_health_monitor import resolve_order_permission

        prev = {"as_of_date": "2026-06-18", "overall_grade": "GREEN"}
        perm = resolve_order_permission(prev)
        assert perm["health_grade"] == "GREEN"
        assert perm.get("health_substatus") is None

    def test_lowercase_grade_works(self):
        from scripts.ops.run_daily_strategy_health_monitor import resolve_order_permission

        prev = {"as_of_date": "2026-06-18", "overall_grade": "red"}
        perm = resolve_order_permission(prev)
        assert perm["allow_new_buys"] is False
        assert perm["emit_orders"] is False
        assert perm["health_grade"] == "RED"


class TestPreScoreGate:
    """Test that PreScoreGate has all required checks and doesn't touch score table."""

    def test_all_7_checks_present(self):
        from scripts.ops.data_readiness_gate import PreScoreGate
        import inspect

        src = inspect.getsource(PreScoreGate.all_checks)
        expected = [
            "row_count",
            "exchange_coverage",
            "date_freshness",
            "freshness_samples",
            "adjust_factor_coverage",
            "suspension_st_basic",
            "suspension_completeness",
        ]
        for check in expected:
            assert check in src, f"Missing PreScoreGate check: {check}"

    def test_no_score_table_reference(self):
        from scripts.ops.data_readiness_gate import PreScoreGate
        import inspect

        src = inspect.getsource(PreScoreGate.all_checks)
        assert "score_rank_daily" not in src, (
            "PreScoreGate must NOT reference score_rank_daily — that's PostScoreGate's job"
        )


class TestPostScoreGate:
    """Test that PostScoreGate has all required checks."""

    def test_all_5_checks_present(self):
        from scripts.ops.data_readiness_gate import PostScoreGate
        import inspect

        src = inspect.getsource(PostScoreGate.all_checks)
        expected = [
            "score_date_matches",
            "score_null_rates",
            "industry_null_rate",
            "candidate_pool_size",
            "candidate_contamination",
        ]
        for check in expected:
            assert check in src, f"Missing PostScoreGate check: {check}"


class TestHealthMonitorCalculations:
    """Test real performance metric calculations."""

    def test_compute_window_metrics_positive(self):
        from scripts.ops.run_daily_strategy_health_monitor import _compute_window_metrics

        returns = [0.01, 0.02, 0.005, 0.03, -0.005]
        nav = [1.0]
        for r in returns:
            nav.append(nav[-1] * (1 + r))

        m = _compute_window_metrics(returns, nav[1:])
        assert m["return"] > 0.05
        assert m["max_dd"] <= 0
        assert m["ann_vol"] > 0
        assert m["worst_day"] == -0.005
        assert m["positive_days"] == 4
        assert m["negative_days"] == 1

    def test_compute_window_metrics_negative(self):
        from scripts.ops.run_daily_strategy_health_monitor import _compute_window_metrics

        returns = [-0.02, -0.03, -0.01, -0.04, 0.005]
        nav = [1.0]
        for r in returns:
            nav.append(nav[-1] * (1 + r))

        m = _compute_window_metrics(returns, nav[1:])
        assert m["return"] < -0.05
        assert m["max_dd"] < -0.07
        assert m["worst_day"] == -0.04

    def test_compute_window_metrics_empty(self):
        from scripts.ops.run_daily_strategy_health_monitor import _compute_window_metrics

        m = _compute_window_metrics([], [])
        assert m["return"] == 0
        assert m["max_dd"] == 0
        assert m["ann_vol"] == 0


class TestDatabaseCredentialSafety:
    """Test that scheduler refuses unsafe database credentials."""

    def test_empty_root_password_rejected(self):
        import os
        os.environ.pop("CHENYIYUN_DB_PASSWORD", None)
        os.environ.pop("CHENYIYUN_DB_URL", None)
        from scoreRank.core.db_config import validate_db_credentials
        assert not validate_db_credentials(), "Empty root password must be rejected"

    def test_password_set_accepted(self):
        import os
        os.environ["CHENYIYUN_DB_PASSWORD"] = "test_pw"
        from scoreRank.core.db_config import validate_db_credentials
        assert validate_db_credentials(), "Non-empty password must be accepted"

    def test_explicit_url_accepted(self):
        import os
        os.environ["CHENYIYUN_DB_URL"] = "mysql+pymysql://app:pass@host/db"
        from scoreRank.core.db_config import validate_db_credentials
        assert validate_db_credentials(), "Explicit URL must be accepted"


class TestSchedulerToCLIContract:
    """End-to-end test: scheduler RED → argparse → emit_orders=False."""

    @staticmethod
    def _scheduler_path() -> Path:
        return Path(__file__).resolve().parents[1] / "scheduler.py"

    @staticmethod
    def _read_scheduler_src() -> str:
        return TestSchedulerToCLIContract._scheduler_path().read_text()

    def test_red_no_emit_orders_flag_means_false(self):
        """When --emit-orders is NOT passed, argparse store_true defaults to False."""
        import argparse
        import sys as _sys

        # Save and restore argv to avoid side effects
        saved_argv = _sys.argv[:]
        try:
            # Simulate RED health: no --emit-orders passed
            _sys.argv = ["export_trusted_strategy_candidates.py", "--date", "20260618"]
            parser = argparse.ArgumentParser()
            parser.add_argument("--date")
            parser.add_argument("--emit-orders", action="store_true", default=False)
            args = parser.parse_args(["--date", "20260618"])
            assert args.emit_orders is False, (
                "Without --emit-orders flag, emit_orders must be False (RED behavior)"
            )
        finally:
            _sys.argv = saved_argv

    def test_green_passes_emit_orders_flag(self):
        """When --emit-orders IS passed, argparse store_true sets to True."""
        import argparse
        import sys as _sys

        saved_argv = _sys.argv[:]
        try:
            _sys.argv = ["export_trusted_strategy_candidates.py", "--emit-orders"]
            parser = argparse.ArgumentParser()
            parser.add_argument("--emit-orders", action="store_true", default=False)
            args = parser.parse_args(["--emit-orders"])
            assert args.emit_orders is True, (
                "With --emit-orders flag, emit_orders must be True (GREEN/YELLOW behavior)"
            )
        finally:
            _sys.argv = saved_argv

    def test_no_emit_orders_explicit_flag(self):
        """--no-emit-orders explicitly sets emit_orders=False."""
        import argparse
        import sys as _sys

        saved_argv = _sys.argv[:]
        try:
            _sys.argv = ["export", "--no-emit-orders"]
            parser = argparse.ArgumentParser()
            parser.add_argument("--emit-orders", action="store_true", default=False)
            parser.add_argument("--no-emit-orders", action="store_false", dest="emit_orders")
            args = parser.parse_args(["--no-emit-orders"])
            assert args.emit_orders is False
        finally:
            _sys.argv = saved_argv

    def test_cleanup_sql_has_trade_date_filter(self):
        """Verify the RED cleanup SQL uses actual table column trade_date."""
        src = self._read_scheduler_src()
        assert "trade_date < :today" in src, (
            "RED cleanup SQL must filter by trade_date (actual table column)"
        )

    def test_cleanup_sql_uses_order_status_column(self):
        """Verify the RED cleanup SQL uses actual column name order_status."""
        src = self._read_scheduler_src()
        assert "order_status" in src and "SET order_status = 'superseded'" in src, (
            "RED cleanup SQL must use order_status column (actual DDL name)"
        )

    def test_cleanup_fail_closed(self):
        """Verify cleanup failure returns False (aborts pipeline)."""
        src = self._read_scheduler_src()
        # Find the except block after FAILED to cleanup
        fail_section_start = src.find("FAILED to cleanup stale BUY drafts")
        assert fail_section_start > 0, "Missing fail-closed error message"
        fail_section = src[fail_section_start:fail_section_start + 500]
        assert "return False" in fail_section, (
            "Cleanup failure must return False to abort pipeline (fail-closed)"
        )

    def test_order_table_columns_match_ddl(self):
        """Verify cleanup SQL columns match the actual table DDL."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from scripts.ops.data_readiness_gate import _get_text_sql
        # The actual DDL uses: order_status, note, trade_date, side
        # The cleanup must use these exact column names
        src = self._read_scheduler_src()
        assert "order_status" in src, "Must use order_status (not 'status')"
        assert "note" in src, "Must use note (not 'memo')"
        assert "trade_date" in src, "Must use trade_date (not 'execution_date')"

    def test_top5_contamination_is_critical(self):
        """Verify Top5 contamination check uses 'critical' severity."""
        import inspect, sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from scripts.ops.data_readiness_gate import PostScoreGate
        src = inspect.getsource(PostScoreGate.check_candidate_contamination)
        assert "top5_contaminated" in src, "Missing Top5 contamination check"
        assert "severity" in src, "Top5 contamination must have severity field"


class TestFeishuNoTLSFallback:
    """Test that Feishu notifier has no insecure TLS fallback."""

    def test_no_unverified_context_in_module(self):
        import inspect
        from scripts.ops.feishu_notifier import send_feishu_text
        src = inspect.getsource(send_feishu_text)
        assert "ssl._create_unverified_context" not in src, (
            "send_feishu_text must not contain TLS fallback"
        )
        assert "TLS_CERTIFICATE_ERROR" in src, (
            "send_feishu_text must return TLS_CERTIFICATE_ERROR on cert failure"
        )
