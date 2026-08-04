"""T+1 execution-date resolution tests (v5.4.1 evidence repair).

execution_date MUST come from the trade calendar as the first open day
strictly after signal_date — never from "latest score date".  These tests
use synthetic calendars (no DB, no snapshots) so they run in CI.

Required cases:
  - Friday signal -> Monday execution
  - pre-holiday signal -> post-holiday execution
  - market-closed day is never an execution date
  - T and T+1 are never the same date
  - calendar exhaustion fails closed
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_spec = importlib.util.spec_from_file_location(
    "run_daily_shadow", PROJECT_ROOT / "scripts/ops/run_daily_shadow.py")
_shadow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shadow)

# Synthetic 2026-08 calendar with a public holiday week 2026-08-17..21
# (closed) — open days only:
# 08-03(M) 04(Tu) 05(W) 06(Th) 07(F) | 10(M) 11(Tu) 12(W) 13(Th) 14(F)
# 17..21 CLOSED | 24(M) 25(Tu) 26(W) 27(Th) 28(F) | 31(M)
OPEN_DAYS = [
    "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07",
    "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14",
    "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28",
    "2026-08-31",
]


def test_friday_signal_monday_execution():
    assert _shadow.next_open_day(OPEN_DAYS, "2026-08-07") == "2026-08-10"


def test_pre_holiday_signal_post_holiday_execution():
    assert _shadow.next_open_day(OPEN_DAYS, "2026-08-14") == "2026-08-24"


def test_closed_day_never_execution_date():
    # A closed day (08-17) is not in the open list at all; execution lands
    # on the first open day after any signal date.
    assert "2026-08-17" not in OPEN_DAYS
    assert _shadow.next_open_day(OPEN_DAYS, "2026-08-14") != "2026-08-17"


def test_t_and_t_plus_1_never_same():
    # The last calendar day raises (no successor — covered by the
    # exhaustion test below); every other signal date must map forward.
    for d in OPEN_DAYS[:-1]:
        assert _shadow.next_open_day(OPEN_DAYS, d) != d, f"T+1 == T for {d}"


def test_strictly_after_semantics():
    # A signal on the LAST calendar day has no successor -> fail closed.
    with pytest.raises(ValueError):
        _shadow.next_open_day(OPEN_DAYS, "2026-08-31")


def test_true_blind_start_enforced():
    with pytest.raises(RuntimeError):
        _shadow._check_true_blind("2026-08-03")
    # 2026-08-05 (the declared start) is allowed.
    _shadow._check_true_blind("2026-08-05")
