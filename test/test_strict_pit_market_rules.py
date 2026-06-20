from datetime import date

import pytest

from scripts.research.execution_market_rules import limit_prices
from scripts.research.strict_execution_ledger import CorporateAction
from scripts.research_trusted_strategy_account_backtest import _validate_corporate_action_pit


def test_pit_requires_previous_trading_day_close_cutoff():
    action = CorporateAction("000001", date(2026, 1, 6), action_type="dividend_cash", source_event_id="x", announcement_date="2026-01-05", effective_date=date(2026, 1, 6), as_of_timestamp="2026-01-05T15:01:00+08:00")
    with pytest.raises(RuntimeError, match="pit_cutoff"):
        _validate_corporate_action_pit({date(2026, 1, 6): [action]}, [date(2026, 1, 5), date(2026, 1, 6)])


def test_market_rule_limit_prices_are_tick_aligned():
    assert limit_prices(10, "000001", 0) == (11.0, 9.0)
    assert limit_prices(10, "300001", 0) == (12.0, 8.0)
    assert limit_prices(10, "000001", 1) == (10.5, 9.5)
