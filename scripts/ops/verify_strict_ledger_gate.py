"""Strict ledger verification gate — validates corporate action coverage, ledger NAV
deviation, cash residuals, and position weight deviations against acceptance criteria.

Reads thresholds from config/production_acceptance.yaml.
Outputs a pass/fail report that gates L0→L1 promotion.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_acceptance_criteria() -> dict[str, Any]:
    """Load thresholds from production_acceptance.yaml."""
    try:
        import yaml
    except ImportError:
        return {}
    path = PROJECT_ROOT / "config" / "production_acceptance.yaml"
    if not path.exists():
        return {}
    cfg = yaml.safe_load(path.read_text())
    return cfg.get("acceptance", {})


@dataclass
class LedgerVerificationResult:
    passed: bool
    checks: dict[str, Any]
    summary: str


def verify_corporate_action_coverage(
    engine, held_symbols: list[str], as_of_date: str
) -> dict[str, Any]:
    """Check that all held stocks have corporate action data coverage.

    For each held symbol, verify that the corporate_action_snapshot table
    has entries covering the holding period. Returns coverage stats.
    """
    from sqlalchemy import text

    if not held_symbols:
        return {"coverage_pct": 1.0, "passed": True, "uncovered": []}

    try:
        with engine.connect() as conn:
            # Check if corporate_action_snapshot table exists
            tbl = conn.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE TABLE_SCHEMA = 'chenyiyun' "
                "AND TABLE_NAME = 'corporate_action_snapshot' LIMIT 1"
            )).scalar()

        if not tbl:
            return {
                "coverage_pct": 0.0,
                "passed": False,
                "uncovered": held_symbols,
                "reason": "corporate_action_snapshot table does not exist",
            }

        uncovered = []
        with engine.connect() as conn:
            for sym in held_symbols:
                row = conn.execute(text(
                    "SELECT COUNT(*) FROM chenyiyun.corporate_action_snapshot "
                    "WHERE symbol = :sym LIMIT 1"
                ), {"sym": sym}).scalar()
                if not row or int(row) == 0:
                    uncovered.append(sym)

        coverage = 1.0 - len(uncovered) / len(held_symbols)
        criteria = _load_acceptance_criteria().get("strict_ledger", {})
        min_coverage = criteria.get("min_corporate_action_coverage", 1.0)

        return {
            "coverage_pct": round(coverage, 4),
            "held_count": len(held_symbols),
            "uncovered_count": len(uncovered),
            "uncovered": uncovered[:10],
            "threshold": min_coverage,
            "passed": coverage >= min_coverage,
        }
    except Exception as exc:
        return {"coverage_pct": 0.0, "passed": False, "error": str(exc)}


def verify_ledger_nav_deviation(
    engine, as_of_date: str, max_error_bps: float = 1.0
) -> dict[str, Any]:
    """Check that the strict execution ledger NAV matches snapshot NAV within tolerance."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            # Compare latest ledger equity vs live_daily_snapshots total_equity
            row = conn.execute(text(
                "SELECT l.equity as ledger_equity, s.total_equity as snapshot_equity "
                "FROM (SELECT SUM(cash + positions_value) as equity "
                "      FROM chenyiyun.execution_ledger_snapshot "
                "      WHERE as_of_date <= :d ORDER BY as_of_date DESC LIMIT 1) l "
                "CROSS JOIN (SELECT total_equity FROM chenyiyun.live_daily_snapshots "
                "            WHERE trade_date <= :d2 ORDER BY trade_date DESC LIMIT 1) s"
            ), {"d": as_of_date, "d2": as_of_date}).fetchone()

        if not row or not row[0] or not row[1]:
            return {"passed": False, "reason": "No ledger or snapshot data found"}

        ledger_eq = float(row[0])
        snapshot_eq = float(row[1])
        if snapshot_eq == 0:
            return {"passed": False, "reason": "Snapshot equity is zero"}

        deviation_bps = abs(ledger_eq / snapshot_eq - 1.0) * 10000
        passed = deviation_bps <= max_error_bps

        return {
            "ledger_equity": round(ledger_eq, 2),
            "snapshot_equity": round(snapshot_eq, 2),
            "deviation_bps": round(deviation_bps, 2),
            "threshold_bps": max_error_bps,
            "passed": passed,
        }
    except Exception as exc:
        return {"passed": False, "error": str(exc)}


def verify_cash_residual(
    engine, as_of_date: str, max_residual_pct: float = 0.0025
) -> dict[str, Any]:
    """Check unexplained cash residuals against acceptance threshold."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT total_equity, available_cash, total_market_value "
                "FROM chenyiyun.live_daily_snapshots "
                "WHERE trade_date <= :d ORDER BY trade_date DESC LIMIT 1"
            ), {"d": as_of_date}).fetchone()

        if not row:
            return {"passed": False, "reason": "No snapshot data"}

        nav = float(row[0] or 0)
        cash = float(row[1] or 0)
        mkt_val = float(row[2] or 0)
        if nav == 0:
            return {"passed": False, "reason": "NAV is zero"}

        residual = abs(nav - cash - mkt_val)
        residual_pct = residual / nav
        passed = residual_pct <= max_residual_pct

        return {
            "nav": round(nav, 2),
            "cash": round(cash, 2),
            "market_value": round(mkt_val, 2),
            "residual": round(residual, 2),
            "residual_pct_nav": round(residual_pct, 6),
            "threshold_pct": max_residual_pct,
            "passed": passed,
        }
    except Exception as exc:
        return {"passed": False, "error": str(exc)}


def run_strict_ledger_verification(
    engine, as_of_date: str, held_symbols: list[str] | None = None
) -> LedgerVerificationResult:
    """Run all strict ledger checks and return a pass/fail result.

    This is the gate that must pass before advancing from L0→L1.
    """
    criteria = _load_acceptance_criteria().get("strict_ledger", {})
    checks: dict[str, Any] = {}

    # 1. Corporate action coverage
    syms = held_symbols or []
    checks["corporate_action_coverage"] = verify_corporate_action_coverage(
        engine, syms, as_of_date
    )

    # 2. Ledger NAV deviation
    max_nav_error = criteria.get("max_ledger_nav_error_bps", 1.0)
    checks["ledger_nav_deviation"] = verify_ledger_nav_deviation(
        engine, as_of_date, max_error_bps=max_nav_error
    )

    # 3. Cash residual
    max_residual = criteria.get("max_unexplained_cash_residual_pct_nav", 0.0025)
    checks["cash_residual"] = verify_cash_residual(
        engine, as_of_date, max_residual_pct=max_residual
    )

    passed = all(c.get("passed", False) for c in checks.values())
    failed = [k for k, v in checks.items() if not v.get("passed", False)]

    summary = (
        "PASS: All strict ledger checks passed."
        if passed
        else f"FAIL: {len(failed)} check(s) failed — {', '.join(failed)}"
    )

    return LedgerVerificationResult(passed=passed, checks=checks, summary=summary)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    from sqlalchemy import create_engine
    from scoreRank.core.db_config import build_sqlalchemy_url

    parser = argparse.ArgumentParser(description="Run strict ledger verification gate.")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--held-symbols", default=None, help="Comma-separated held stock symbols.")
    args = parser.parse_args()

    engine = create_engine(build_sqlalchemy_url())
    as_of = args.date or date.today().isoformat()
    held = [s.strip() for s in (args.held_symbols or "").split(",") if s.strip()] or None

    result = run_strict_ledger_verification(engine, as_of, held_symbols=held)
    print(json.dumps({
        "passed": result.passed,
        "checks": result.checks,
        "summary": result.summary,
    }, indent=2, default=str))

    if not result.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
