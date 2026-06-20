"""Multi-dimensional data readiness gate for the production pipeline.

Replaces the single 'count(*) > 1000' check in scheduler.py with a structured,
multi-dimensional contract that validates data completeness before allowing the
pipeline to proceed.

Output states:
  READY               — All checks passed, pipeline can proceed.
  READY_WITH_WARNING   — Core checks passed but some warnings (pipeline can proceed).
  BLOCKED              — Critical checks failed, pipeline must wait or abort.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Minimum expected row counts (A-share market has ~5000 listed stocks)
EXPECTED_MIN_ROWS: int = 4000
EXPECTED_SH_ROWS: int = 1500
EXPECTED_SZ_ROWS: int = 2000

# Maximum allowed staleness in calendar days (tolerates T+1 data delay)
MAX_STALE_DAYS: int = 2

# Key stocks sampled for data freshness verification
FRESHNESS_CHECK_SYMBOLS: tuple[str, ...] = (
    "600519.SH",  # Kweichow Moutai
    "000001.SZ",  # Ping An Bank
    "300750.SZ",  # CATL
)

# Minimum pool size for candidate export
MIN_CANDIDATE_POOL_SIZE: int = 5000

# Required score_rank_daily columns
REQUIRED_SCORE_COLUMNS: tuple[str, ...] = (
    "trade_date", "symbol", "name", "industry",
    "score", "s_liquidity", "bs_score_v2", "bs_consensus_score",
)

# Maximum allowed null ratio per column
MAX_NULL_RATIO: float = 0.05


class DataReadinessGate:
    """Multi-dimensional data readiness validator.

    Usage:
        engine = create_engine(build_sqlalchemy_url())
        gate = DataReadinessGate(engine)
        result = gate.all_checks(target_date)
        if result["status"] == "BLOCKED":
            logger.error(f"Pipeline blocked: {result['failed_checks']}")
    """

    def __init__(self, engine) -> None:
        self._engine = engine
        self._checks: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_row_count(self, target_date: date) -> dict[str, Any]:
        """Check that the market data table has sufficient rows."""
        date_str = target_date.strftime("%Y%m%d")
        try:
            from sqlalchemy import text
            with self._engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM tushare_stock.dwd_stock_daily_standard "
                        "WHERE trade_date = :date"
                    ),
                    {"date": date_str},
                ).scalar()
            count = int(result or 0)
        except Exception as exc:
            return {
                "check": "row_count",
                "date": date_str,
                "passed": False,
                "detail": f"query_error={exc}",
            }

        passed = count >= EXPECTED_MIN_ROWS
        return {
            "check": "row_count",
            "date": date_str,
            "actual": count,
            "threshold": EXPECTED_MIN_ROWS,
            "passed": passed,
            "severity": "critical",
        }

    def check_date_freshness(self, target_date: date) -> dict[str, Any]:
        """Check that the latest data is not too stale."""
        try:
            from sqlalchemy import text
            with self._engine.connect() as conn:
                result = conn.execute(
                    text("SELECT MAX(trade_date) FROM tushare_stock.dwd_stock_daily_standard")
                ).scalar()
            latest_str = str(result or "")[:10]
            if not latest_str:
                return {
                    "check": "date_freshness",
                    "passed": False,
                    "detail": "no_data_in_table",
                    "severity": "critical",
                }

            # Parse latest date
            if "-" in latest_str:
                latest_date = datetime.strptime(latest_str[:10], "%Y-%m-%d").date()
            else:
                latest_date = datetime.strptime(latest_str[:8], "%Y%m%d").date()

            today = date.today()
            days_behind = (today - latest_date).days
            passed = days_behind <= MAX_STALE_DAYS

            return {
                "check": "date_freshness",
                "target_date": target_date.isoformat(),
                "latest_in_db": latest_date.isoformat(),
                "days_behind": days_behind,
                "threshold_days": MAX_STALE_DAYS,
                "passed": passed,
                "severity": "critical" if days_behind > 7 else "warning",
            }
        except Exception as exc:
            return {
                "check": "date_freshness",
                "passed": False,
                "detail": f"query_error={exc}",
                "severity": "critical",
            }

    def check_freshness_samples(self, target_date: date) -> dict[str, Any]:
        """Verify specific benchmark stocks have valid data for the target date."""
        date_str = target_date.strftime("%Y%m%d")
        samples: list[dict[str, Any]] = []
        try:
            from sqlalchemy import text
            with self._engine.connect() as conn:
                for symbol in FRESHNESS_CHECK_SYMBOLS:
                    row = conn.execute(
                        text(
                            "SELECT ts_code, trade_date, close, amount "
                            "FROM tushare_stock.dwd_stock_daily_standard "
                            "WHERE trade_date = :date AND ts_code = :symbol LIMIT 1"
                        ),
                        {"date": date_str, "symbol": symbol},
                    ).fetchone()
                    has_valid_close = (
                        row is not None
                        and row[2] is not None
                        and float(row[2] or 0) > 0
                    )
                    samples.append({
                        "symbol": symbol,
                        "has_close": has_valid_close,
                        "close": float(row[2]) if row and row[2] else None,
                    })

            all_ok = all(s["has_close"] for s in samples)
            return {
                "check": "freshness_samples",
                "date": date_str,
                "samples": samples,
                "passed": all_ok,
                "severity": "warning" if not all_ok else "info",
            }
        except Exception as exc:
            return {
                "check": "freshness_samples",
                "date": date_str,
                "passed": False,
                "detail": f"query_error={exc}",
                "severity": "warning",
            }

    def check_score_table_freshness(self, target_date: date) -> dict[str, Any]:
        """Check score_rank_daily has data for or near the target date."""
        date_str = target_date.strftime("%Y%m%d")
        try:
            from sqlalchemy import text
            with self._engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT MAX(trade_date) FROM chenyiyun.score_rank_daily"
                    )
                ).scalar()
            latest_str = str(result or "")[:10]
            if not latest_str:
                return {
                    "check": "score_table_freshness",
                    "passed": False,
                    "detail": "score_rank_daily_is_empty",
                    "severity": "critical",
                }

            if "-" in latest_str:
                latest_date = datetime.strptime(latest_str[:10], "%Y-%m-%d").date()
            else:
                latest_date = datetime.strptime(latest_str[:8], "%Y%m%d").date()

            # Score data should be within 1 day of target
            days_gap = abs((target_date - latest_date).days)
            passed = days_gap <= 1

            return {
                "check": "score_table_freshness",
                "target_date": target_date.isoformat(),
                "latest_score_date": latest_date.isoformat(),
                "days_gap": days_gap,
                "passed": passed,
                "severity": "critical" if days_gap > 3 else "warning",
            }
        except Exception as exc:
            return {
                "check": "score_table_freshness",
                "passed": False,
                "detail": f"query_error={exc}",
                "severity": "warning",
            }

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def all_checks(self, target_date: date) -> dict[str, Any]:
        """Run all readiness checks and return a consolidated result.

        Returns:
            dict with keys:
                status: "READY" | "READY_WITH_WARNING" | "BLOCKED"
                passed: bool (True if not BLOCKED)
                checks: list of per-check results
                target_date: ISO date string
                gate_version: str
        """
        self._checks = [
            self.check_row_count(target_date),
            self.check_date_freshness(target_date),
            self.check_freshness_samples(target_date),
            self.check_score_table_freshness(target_date),
        ]

        critical_failures = [
            c for c in self._checks
            if not c["passed"] and c.get("severity") == "critical"
        ]
        warning_failures = [
            c for c in self._checks
            if not c["passed"] and c.get("severity") != "critical"
        ]

        if critical_failures:
            status = "BLOCKED"
        elif warning_failures:
            status = "READY_WITH_WARNING"
        else:
            status = "READY"

        return {
            "status": status,
            "passed": status != "BLOCKED",
            "checks": self._checks,
            "failed_critical": [c["check"] for c in critical_failures],
            "failed_warnings": [c["check"] for c in warning_failures],
            "target_date": target_date.isoformat(),
            "gate_version": "1.0",
        }


def run_gate_check(engine, target_date: date) -> dict[str, Any]:
    """Convenience function: run DataReadinessGate and return result."""
    gate = DataReadinessGate(engine)
    return gate.all_checks(target_date)
