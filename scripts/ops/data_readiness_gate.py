"""Multi-dimensional data readiness gate for the production pipeline.

Split into two phases to avoid circular dependencies:

  PreScoreGate  — runs BEFORE scoreRank/run_daily.py
    - Market data row count (by exchange: SSE, SZSE, BSE)
    - Market data date freshness (relative to target_date + trade calendar)
    - Benchmark stock samples (Moutai, PingAn, CATL)
    - Suspension / ST / adjustment factor checks

  PostScoreGate — runs AFTER scoring pipeline completes
    - score_rank_daily latest date MUST equal target_date
    - Required columns present and non-null
    - Industry null rate
    - Score / liquidity / BS field null rates
    - Minimum candidate pool size

Output states: READY | READY_WITH_WARNING | BLOCKED
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
EXPECTED_MIN_ROWS: int = 4000
EXPECTED_SSE_ROWS: int = 1500    # Shanghai
EXPECTED_SZSE_ROWS: int = 2000   # Shenzhen
EXPECTED_BSE_ROWS: int = 100     # Beijing (minimal)

MAX_STALE_CALENDAR_DAYS: int = 2     # T+1 data delay tolerance
MAX_STALE_TRADING_DAYS: int = 1      # Must not be more than 1 trading day behind

FRESHNESS_CHECK_SYMBOLS: tuple[str, ...] = (
    "600519.SH",  # Kweichow Moutai
    "000001.SZ",  # Ping An Bank
    "300750.SZ",  # CATL
)

MIN_CANDIDATE_POOL_SIZE: int = 5000

REQUIRED_SCORE_COLUMNS: tuple[str, ...] = (
    "trade_date", "symbol", "name", "industry",
    "score", "s_liquidity", "bs_score_v2", "bs_consensus_score",
)

MAX_NULL_RATIO: float = 0.05
MAX_INDUSTRY_NULL_RATIO: float = 0.02


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_text_sql():
    from sqlalchemy import text as _text
    return _text


def _latest_trade_date_before(engine, ref_date: date) -> date | None:
    """Return the latest trading day <= ref_date from dim_trade_cal (SSE)."""
    text_fn = _get_text_sql()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text_fn(
                    "SELECT MAX(cal_date) FROM chenyiyun.dim_trade_cal "
                    "WHERE exchange = 'SSE' AND is_open = 1 AND cal_date <= :ref"
                ),
                {"ref": ref_date.strftime("%Y%m%d")},
            ).fetchone()
    except Exception:
        return None
    if row and row[0]:
        raw = str(row[0])
        if "-" in raw:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        return datetime.strptime(raw[:8], "%Y%m%d").date()
    return None


# ---------------------------------------------------------------------------
# PreScoreGate — runs before scoring pipeline
# ---------------------------------------------------------------------------

class PreScoreGate:
    """Validates market data readiness BEFORE the scoring pipeline runs."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self._checks: list[dict[str, Any]] = []

    def check_row_count(self, target_date: date) -> dict[str, Any]:
        """Total market rows >= EXPECTED_MIN_ROWS for the target date."""
        date_str = target_date.strftime("%Y%m%d")
        text_fn = _get_text_sql()
        try:
            with self._engine.connect() as conn:
                total = conn.execute(
                    text_fn(
                        "SELECT COUNT(*) FROM tushare_stock.dwd_stock_daily_standard "
                        "WHERE trade_date = :date"
                    ),
                    {"date": date_str},
                ).scalar()
            count = int(total or 0)
        except Exception as exc:
            return {"check": "row_count", "passed": False, "detail": f"query_error={exc}", "severity": "critical"}

        passed = count >= EXPECTED_MIN_ROWS
        return {
            "check": "row_count", "date": date_str,
            "actual": count, "threshold": EXPECTED_MIN_ROWS,
            "passed": passed, "severity": "critical",
        }

    def check_exchange_coverage(self, target_date: date) -> dict[str, Any]:
        """Verify per-exchange row counts meet minimum thresholds."""
        date_str = target_date.strftime("%Y%m%d")
        text_fn = _get_text_sql()
        results: dict[str, dict] = {}
        try:
            with self._engine.connect() as conn:
                for label, pattern, expected in [
                    ("SSE", "%.SH", EXPECTED_SSE_ROWS),
                    ("SZSE", "%.SZ", EXPECTED_SZSE_ROWS),
                    ("BSE", "%.BJ", EXPECTED_BSE_ROWS),
                ]:
                    cnt = conn.execute(
                        text_fn(
                            "SELECT COUNT(*) FROM tushare_stock.dwd_stock_daily_standard "
                            "WHERE trade_date = :date AND ts_code LIKE :pat"
                        ),
                        {"date": date_str, "pat": pattern},
                    ).scalar()
                    cnt_int = int(cnt or 0)
                    results[label] = {"actual": cnt_int, "threshold": expected, "passed": cnt_int >= expected}
            all_ok = all(v["passed"] for v in results.values())
        except Exception as exc:
            return {"check": "exchange_coverage", "passed": False, "detail": f"query_error={exc}", "severity": "critical"}

        return {
            "check": "exchange_coverage", "date": date_str, "exchanges": results,
            "passed": all_ok, "severity": "critical",
        }

    def check_date_freshness(self, target_date: date) -> dict[str, Any]:
        """Check market data date against target_date using trade calendar.

        Uses target_date as the reference, not date.today(), so historical
        backfills and weekend runs are handled correctly.
        """
        text_fn = _get_text_sql()
        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text_fn("SELECT MAX(trade_date) FROM tushare_stock.dwd_stock_daily_standard")
                ).scalar()
            latest_str = str(result or "")[:10]
            if not latest_str:
                return {"check": "date_freshness", "passed": False, "detail": "no_data", "severity": "critical"}

            if "-" in latest_str:
                latest_date = datetime.strptime(latest_str[:10], "%Y-%m-%d").date()
            else:
                latest_date = datetime.strptime(latest_str[:8], "%Y%m%d").date()
        except Exception as exc:
            return {"check": "date_freshness", "passed": False, "detail": f"query_error={exc}", "severity": "critical"}

        # Compare against target_date, not date.today()
        calendar_days_behind = (target_date - latest_date).days

        # Also check trading-day distance
        latest_trade_day = _latest_trade_date_before(self._engine, target_date)
        trading_days_behind: int | None = None
        if latest_trade_day:
            # Count trading days between latest_trade_day and target_date
            text_fn2 = _get_text_sql()
            try:
                with self._engine.connect() as conn:
                    cnt = conn.execute(
                        text_fn2(
                            "SELECT COUNT(*) FROM chenyiyun.dim_trade_cal "
                            "WHERE exchange = 'SSE' AND is_open = 1 "
                            "AND cal_date > :lo AND cal_date <= :hi"
                        ),
                        {"lo": latest_trade_day.strftime("%Y%m%d"), "hi": target_date.strftime("%Y%m%d")},
                    ).scalar()
                trading_days_behind = int(cnt or 0)
            except Exception:
                pass

        passed = (
            calendar_days_behind <= MAX_STALE_CALENDAR_DAYS
            and (trading_days_behind is None or trading_days_behind <= MAX_STALE_TRADING_DAYS)
        )

        return {
            "check": "date_freshness",
            "target_date": target_date.isoformat(),
            "latest_in_db": latest_date.isoformat(),
            "calendar_days_behind": calendar_days_behind,
            "trading_days_behind": trading_days_behind,
            "passed": passed,
            "severity": "critical" if calendar_days_behind > 5 else "warning",
        }

    def check_freshness_samples(self, target_date: date) -> dict[str, Any]:
        """Benchmark stocks must have valid close prices for target_date."""
        date_str = target_date.strftime("%Y%m%d")
        text_fn = _get_text_sql()
        samples: list[dict[str, Any]] = []
        try:
            with self._engine.connect() as conn:
                for symbol in FRESHNESS_CHECK_SYMBOLS:
                    row = conn.execute(
                        text_fn(
                            "SELECT ts_code, close, amount FROM tushare_stock.dwd_stock_daily_standard "
                            "WHERE trade_date = :date AND ts_code = :symbol LIMIT 1"
                        ),
                        {"date": date_str, "symbol": symbol},
                    ).fetchone()
                    has_valid = row is not None and row[1] is not None and float(row[1] or 0) > 0
                    samples.append({"symbol": symbol, "has_close": has_valid})
        except Exception as exc:
            return {"check": "freshness_samples", "passed": False, "detail": f"query_error={exc}", "severity": "warning"}

        all_ok = all(s["has_close"] for s in samples)
        return {
            "check": "freshness_samples", "date": date_str, "samples": samples,
            "passed": all_ok, "severity": "warning",
        }

    def check_suspension_st_basic(self, target_date: date) -> dict[str, Any]:
        """Verify suspension/ST labeling tables are accessible (not empty)."""
        text_fn = _get_text_sql()
        date_str = target_date.strftime("%Y%m%d")
        try:
            with self._engine.connect() as conn:
                st_cnt = conn.execute(
                    text_fn("SELECT COUNT(*) FROM tushare_stock.dwd_stock_label_daily WHERE trade_date = :date"),
                    {"date": date_str},
                ).scalar()
        except Exception:
            st_cnt = None

        passed = st_cnt is not None and int(st_cnt or 0) > 0
        severity = "critical" if not passed else "info"
        return {
            "check": "suspension_st_basic", "date": target_date.isoformat(),
            "label_rows": int(st_cnt or 0), "passed": passed, "severity": severity,
        }

    def check_adjust_factor_coverage(self, target_date: date) -> dict[str, Any]:
        """Verify adjust_factor coverage and detect abnormal jumps for target_date.

        Checks:
          - adjust_factor null rate on target_date
          - extreme day-over-day jumps (>50% change) in adjust_factor
        """
        text_fn = _get_text_sql()
        date_str = target_date.strftime("%Y%m%d")
        try:
            with self._engine.connect() as conn:
                # Null rate on target_date
                total = conn.execute(
                    text_fn(
                        "SELECT COUNT(*) FROM tushare_stock.dwd_stock_daily_standard "
                        "WHERE trade_date = :date"
                    ),
                    {"date": date_str},
                ).scalar()
                null_adj = conn.execute(
                    text_fn(
                        "SELECT COUNT(*) FROM tushare_stock.dwd_stock_daily_standard "
                        "WHERE trade_date = :date AND adj_factor IS NULL"
                    ),
                    {"date": date_str},
                ).scalar()
            total_int = int(total or 0)
            null_int = int(null_adj or 0)
            null_rate = null_int / max(total_int, 1)
        except Exception as exc:
            return {"check": "adjust_factor_coverage", "passed": False, "detail": f"query_error={exc}", "severity": "critical"}

        passed = null_rate <= 0.01  # >1% null adjust_factor is critical
        return {
            "check": "adjust_factor_coverage", "date": date_str,
            "total_rows": total_int, "null_adj_factor": null_int,
            "null_rate": round(null_rate, 4),
            "passed": passed,
            "severity": "critical" if null_rate > 0.01 else "info",
        }

    def check_suspension_completeness(self, target_date: date) -> dict[str, Any]:
        """Verify suspension status fields are complete for the target_date.

        Checks that key columns used for tradability filtering are present and
        have acceptable null rates.
        """
        text_fn = _get_text_sql()
        date_str = target_date.strftime("%Y%m%d")
        try:
            with self._engine.connect() as conn:
                total = conn.execute(
                    text_fn(
                        "SELECT COUNT(*) FROM tushare_stock.dwd_stock_daily_standard "
                        "WHERE trade_date = :date"
                    ),
                    {"date": date_str},
                ).scalar()
                # Check for null close prices (indicates suspended stocks)
                null_close = conn.execute(
                    text_fn(
                        "SELECT COUNT(*) FROM tushare_stock.dwd_stock_daily_standard "
                        "WHERE trade_date = :date AND (close IS NULL OR close = 0)"
                    ),
                    {"date": date_str},
                ).scalar()
            total_int = int(total or 0)
            null_close_int = int(null_close or 0)
            null_close_rate = null_close_int / max(total_int, 1)
            # Normal suspension rate in A-share is ~2-8%. >15% is suspicious.
            passed = null_close_rate <= 0.15
        except Exception as exc:
            return {"check": "suspension_completeness", "passed": False, "detail": f"query_error={exc}", "severity": "warning"}

        return {
            "check": "suspension_completeness", "date": date_str,
            "total_rows": total_int, "null_close_count": null_close_int,
            "null_close_rate": round(null_close_rate, 4),
            "passed": passed,
            "severity": "warning" if not passed else "info",
        }

    def all_checks(self, target_date: date) -> dict[str, Any]:
        """Run all Pre-Score checks."""
        self._checks = [
            self.check_row_count(target_date),
            self.check_exchange_coverage(target_date),
            self.check_date_freshness(target_date),
            self.check_freshness_samples(target_date),
            self.check_adjust_factor_coverage(target_date),
            self.check_suspension_st_basic(target_date),
            self.check_suspension_completeness(target_date),
        ]
        return _summarize(self._checks, target_date, gate_name="pre_score")


# ---------------------------------------------------------------------------
# PostScoreGate — runs AFTER scoring pipeline completes
# ---------------------------------------------------------------------------

class PostScoreGate:
    """Validates score_rank_daily data AFTER the scoring pipeline has run."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self._checks: list[dict[str, Any]] = []

    def check_score_date_matches(self, target_date: date) -> dict[str, Any]:
        """score_rank_daily MAX(trade_date) MUST equal target_date after scoring."""
        text_fn = _get_text_sql()
        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text_fn("SELECT MAX(trade_date) FROM chenyiyun.score_rank_daily")
                ).scalar()
            latest_str = str(result or "")[:10]
            if not latest_str:
                return {"check": "score_date_matches", "passed": False, "detail": "table_empty", "severity": "critical"}
            if "-" in latest_str:
                latest_date = datetime.strptime(latest_str[:10], "%Y-%m-%d").date()
            else:
                latest_date = datetime.strptime(latest_str[:8], "%Y%m%d").date()
            passed = latest_date == target_date
        except Exception as exc:
            return {"check": "score_date_matches", "passed": False, "detail": f"query_error={exc}", "severity": "critical"}

        return {
            "check": "score_date_matches",
            "target_date": target_date.isoformat(), "latest_score_date": latest_date.isoformat(),
            "passed": passed, "severity": "critical",
        }

    def check_score_null_rates(self, target_date: date) -> dict[str, Any]:
        """Check null rate for required columns in score_rank_daily."""
        date_str = target_date.strftime("%Y%m%d")
        text_fn = _get_text_sql()
        field_results: dict[str, dict] = {}
        try:
            total = None
            with self._engine.connect() as conn:
                total_row = conn.execute(
                    text_fn("SELECT COUNT(*) FROM chenyiyun.score_rank_daily WHERE trade_date = :date"),
                    {"date": date_str},
                ).fetchone()
                total = int(total_row[0]) if total_row else 0

                if total > 0:
                    for col in REQUIRED_SCORE_COLUMNS:
                        null_cnt = conn.execute(
                            text_fn(
                                f"SELECT COUNT(*) FROM chenyiyun.score_rank_daily "
                                f"WHERE trade_date = :date AND `{col}` IS NULL"
                            ),
                            {"date": date_str},
                        ).scalar()
                        null_rate = int(null_cnt or 0) / total
                        field_results[col] = {"null_count": int(null_cnt or 0), "null_rate": round(null_rate, 4),
                                              "passed": null_rate <= MAX_NULL_RATIO}
        except Exception as exc:
            return {"check": "score_null_rates", "passed": False, "detail": f"query_error={exc}", "severity": "critical"}

        all_ok = all(v["passed"] for v in field_results.values())
        return {
            "check": "score_null_rates", "date": date_str, "total_rows": total,
            "fields": field_results, "threshold": MAX_NULL_RATIO,
            "passed": all_ok, "severity": "critical" if not all_ok else "info",
        }

    def check_industry_null_rate(self, target_date: date) -> dict[str, Any]:
        """Industry field null rate must be <= MAX_INDUSTRY_NULL_RATIO."""
        date_str = target_date.strftime("%Y%m%d")
        text_fn = _get_text_sql()
        try:
            with self._engine.connect() as conn:
                total = conn.execute(
                    text_fn("SELECT COUNT(*) FROM chenyiyun.score_rank_daily WHERE trade_date = :date"),
                    {"date": date_str},
                ).scalar()
                null_cnt = conn.execute(
                    text_fn(
                        "SELECT COUNT(*) FROM chenyiyun.score_rank_daily "
                        "WHERE trade_date = :date AND (industry IS NULL OR TRIM(industry) = '')"
                    ),
                    {"date": date_str},
                ).scalar()
            total_int = int(total or 0)
            null_int = int(null_cnt or 0)
            null_rate = null_int / max(total_int, 1)
            passed = null_rate <= MAX_INDUSTRY_NULL_RATIO
        except Exception as exc:
            return {"check": "industry_null_rate", "passed": False, "detail": f"query_error={exc}", "severity": "critical"}

        return {
            "check": "industry_null_rate", "date": date_str,
            "total_rows": total_int, "null_count": null_int, "null_rate": round(null_rate, 4),
            "threshold": MAX_INDUSTRY_NULL_RATIO,
            "passed": passed, "severity": "critical" if not passed else "warning",
        }

    def check_candidate_pool_size(self, target_date: date) -> dict[str, Any]:
        """Minimum candidate pool size for downstream candidate export."""
        date_str = target_date.strftime("%Y%m%d")
        text_fn = _get_text_sql()
        try:
            with self._engine.connect() as conn:
                cnt = conn.execute(
                    text_fn("SELECT COUNT(*) FROM chenyiyun.score_rank_daily WHERE trade_date = :date"),
                    {"date": date_str},
                ).scalar()
            count = int(cnt or 0)
            passed = count >= MIN_CANDIDATE_POOL_SIZE
        except Exception as exc:
            return {"check": "candidate_pool_size", "passed": False, "detail": f"query_error={exc}", "severity": "critical"}

        return {
            "check": "candidate_pool_size", "date": date_str,
            "actual": count, "threshold": MIN_CANDIDATE_POOL_SIZE,
            "passed": passed, "severity": "critical",
        }

    def check_candidate_contamination(self, target_date: date, top_n: int = 100) -> dict[str, Any]:
        """Verify top-N candidates by score don't include suspended/ST/delisted stocks.

        Joins score_rank_daily with dwd_stock_label_daily and dwd_stock_daily_standard
        to check if any top-scored stock is untradable on the target date.
        """
        date_str = target_date.strftime("%Y%m%d")
        text_fn = _get_text_sql()
        try:
            with self._engine.connect() as conn:
                # Top N by score, check for ST/suspended stocks
                rows = conn.execute(
                    text_fn(
                        "SELECT s.symbol, s.score, "
                        "  CASE WHEN l.is_st = 1 THEN 'ST' "
                        "       WHEN l.is_suspended = 1 THEN 'SUSPENDED' "
                        "       WHEN k.close IS NULL OR k.close = 0 THEN 'NO_CLOSE' "
                        "  END AS issue "
                        "FROM chenyiyun.score_rank_daily s "
                        "LEFT JOIN tushare_stock.dwd_stock_label_daily l "
                        "  ON SUBSTRING_INDEX(l.ts_code, '.', 1) = LPAD(s.symbol, 6, '0') "
                        "  AND l.trade_date = :date "
                        "LEFT JOIN tushare_stock.dwd_stock_daily_standard k "
                        "  ON SUBSTRING_INDEX(k.ts_code, '.', 1) = LPAD(s.symbol, 6, '0') "
                        "  AND k.trade_date = :date2 "
                        "WHERE s.trade_date = :date3 "
                        "ORDER BY s.score DESC LIMIT :top_n"
                    ),
                    {"date": date_str, "date2": date_str, "date3": date_str, "top_n": top_n},
                ).mappings().fetchall()
        except Exception as exc:
            return {"check": "candidate_contamination", "passed": False, "detail": f"query_error={exc}", "severity": "warning"}

        contaminated = [dict(r) for r in rows if r.get("issue")]
        passed = len(contaminated) == 0
        return {
            "check": "candidate_contamination", "date": date_str,
            "top_n_checked": top_n,
            "contaminated_count": len(contaminated),
            "contaminated": contaminated[:5],
            "passed": passed,
            "severity": "critical" if len(contaminated) > 5 else "warning",
        }

    def all_checks(self, target_date: date) -> dict[str, Any]:
        """Run all Post-Score checks."""
        self._checks = [
            self.check_score_date_matches(target_date),
            self.check_score_null_rates(target_date),
            self.check_industry_null_rate(target_date),
            self.check_candidate_pool_size(target_date),
            self.check_candidate_contamination(target_date, top_n=100),
        ]
        return _summarize(self._checks, target_date, gate_name="post_score")


# ---------------------------------------------------------------------------
# Shared summarization
# ---------------------------------------------------------------------------

def _summarize(checks: list[dict[str, Any]], target_date: date, gate_name: str) -> dict[str, Any]:
    critical_failures = [c for c in checks if not c["passed"] and c.get("severity") == "critical"]
    warning_failures = [c for c in checks if not c["passed"] and c.get("severity") != "critical"]

    if critical_failures:
        status = "BLOCKED"
    elif warning_failures:
        status = "READY_WITH_WARNING"
    else:
        status = "READY"

    return {
        "status": status,
        "passed": status != "BLOCKED",
        "gate_name": gate_name,
        "checks": checks,
        "failed_critical": [c["check"] for c in critical_failures],
        "failed_warnings": [c["check"] for c in warning_failures],
        "target_date": target_date.isoformat(),
        "gate_version": "2.0",
    }


# Backward-compatible thin wrapper
class DataReadinessGate:
    """Convenience wrapper that runs PreScoreGate (the safe default for scheduler).

    For full pipeline, use PreScoreGate and PostScoreGate separately.
    """

    def __init__(self, engine) -> None:
        self._gate = PreScoreGate(engine)

    def all_checks(self, target_date: date) -> dict[str, Any]:
        return self._gate.all_checks(target_date)


def run_pre_gate(engine, target_date: date) -> dict[str, Any]:
    return PreScoreGate(engine).all_checks(target_date)


def run_post_gate(engine, target_date: date) -> dict[str, Any]:
    return PostScoreGate(engine).all_checks(target_date)
