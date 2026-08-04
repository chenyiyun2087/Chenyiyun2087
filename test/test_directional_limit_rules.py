"""A-share directional execution-gate tests (v5.4.1 evidence repair).

The pre-v5.5 shadow blocked limit-DOWN for BUY orders — the wrong
direction.  Canonical rules (scripts/research/execution_market_rules.py):

  BUY  orders block on limit-UP at open (一字涨停不可买)
  SELL orders block on limit-DOWN at open (一字跌停不可卖)

Both block on: suspension, not listed, no open price, no prev close.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_spec = importlib.util.spec_from_file_location(
    "execution_market_rules",
    PROJECT_ROOT / "scripts/research/execution_market_rules.py")
_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rules)

PRECLOSE = 10.00  # main-board stock, prev close 10.00


def test_buy_blocks_limit_up_not_limit_down():
    # +10% open = limit-up for a main-board stock -> BUY blocked.
    allowed, reason = _rules.can_buy_at_open(11.00, PRECLOSE, "600000", 0)
    assert not allowed and reason == "limit_up_block"
    # -10% open = limit-down — a BUY is NOT blocked by limit-down.
    allowed, reason = _rules.can_buy_at_open(9.00, PRECLOSE, "600000", 0)
    assert allowed and reason == ""


def test_sell_blocks_limit_down_not_limit_up():
    # -10% open = limit-down -> SELL blocked.
    allowed, reason = _rules.can_sell_at_open(9.00, PRECLOSE, "600000", 0)
    assert not allowed and reason == "limit_down_block"
    # +10% open = limit-up — a SELL is NOT blocked by limit-up.
    allowed, reason = _rules.can_sell_at_open(11.00, PRECLOSE, "600000", 0)
    assert allowed and reason == ""


def test_buy_blocks_suspension_and_not_listed():
    assert not _rules.can_buy_at_open(10.50, PRECLOSE, "600000", 0,
                                      is_suspended=1)[0]
    assert not _rules.can_buy_at_open(10.50, PRECLOSE, "600000", 0,
                                      is_listed=0)[0]


def test_sell_blocks_suspension():
    assert not _rules.can_sell_at_open(10.50, PRECLOSE, "600000", 0,
                                       is_suspended=1)[0]


def test_missing_open_price_blocks_both_directions():
    assert not _rules.can_buy_at_open(float("nan"), PRECLOSE, "600000", 0)[0]
    assert not _rules.can_sell_at_open(0.0, PRECLOSE, "600000", 0)[0]


def test_board_specific_limits():
    # ChiNext (300xxx) / STAR (688xxx) limit is 20%: at +12% NOT at limit.
    allowed, reason = _rules.can_buy_at_open(11.20, PRECLOSE, "300001", 0)
    assert allowed and reason == ""
    # But at +20% it IS at limit.
    allowed, reason = _rules.can_buy_at_open(12.00, PRECLOSE, "300001", 0)
    assert not allowed and reason == "limit_up_block"
    # ST stock limit is 5%: at +5% it is at limit.
    allowed, reason = _rules.can_buy_at_open(10.50, PRECLOSE, "600000", 1)
    assert not allowed and reason == "limit_up_block"


def test_normal_opens_fill_both_directions():
    assert _rules.can_buy_at_open(10.50, PRECLOSE, "600000", 0) == (True, "")
    assert _rules.can_sell_at_open(10.50, PRECLOSE, "600000", 0) == (True, "")
