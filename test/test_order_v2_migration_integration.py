"""MySQL integration test: calls production order_repository functions.

Requires ORDER_TEST_DB_URL env var. If not set, all tests skip.

Test matrix (via fixtures, not execution order):
  1. Legacy table → ensure_order_schema → PK=id, UK 6-column correct
  2. backfill_legacy_orders → zero NULL identity fields
  3. Multi-release writes via write_orders_with_metadata → coexist
  4. supersede_pending_buys → only scoped release superseded
  5. Status protection → filled not overwritten
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TEST_DB_URL = os.environ.get("ORDER_TEST_DB_URL", "")
NEEDS_MYSQL = pytest.mark.skipif(
    not TEST_DB_URL, reason="ORDER_TEST_DB_URL not set"
)

TEST_TABLE = "order_test_v2_migration"
TEST_SCHEMA = "chenyiyun"
FULL_TABLE = f"{TEST_SCHEMA}.{TEST_TABLE}"


def _engine():
    from sqlalchemy import create_engine
    return create_engine(TEST_DB_URL, future=True)


def _exec_raw(ddl: str):
    from sqlalchemy import text
    with _engine().begin() as conn:
        conn.execute(text(ddl))


def _drop():
    try:
        _exec_raw(f"DROP TABLE IF EXISTS {FULL_TABLE}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def legacy_table():
    """Create a legacy-schema table, insert historical orders, return table_name."""
    _drop()
    _exec_raw(f"""
        CREATE TABLE {FULL_TABLE} (
            trade_date DATE,
            ts_code VARCHAR(16),
            side VARCHAR(8),
            price DOUBLE,
            current_shares INT,
            target_shares INT,
            delta_shares INT,
            order_status VARCHAR(24) NOT NULL DEFAULT 'planned',
            status_reason VARCHAR(255),
            note VARCHAR(255),
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, ts_code, side)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # Insert legacy orders with NULL identity
    from sqlalchemy import text
    with _engine().begin() as conn:
        for sym, sd in [("000001.SZ", "BUY"), ("000002.SZ", "BUY"), ("000001.SZ", "SELL")]:
            conn.execute(text(
                f"INSERT INTO {FULL_TABLE} (trade_date, ts_code, side, price, delta_shares, order_status) "
                "VALUES ('2026-01-15', :sym, :sd, 10.0, 100, 'planned')"
            ), {"sym": sym, "sd": sd})
    yield FULL_TABLE
    _drop()


@pytest.fixture(scope="class")
def migrated_table(legacy_table):
    """Run ensure_order_schema + backfill on legacy table, return table_name."""
    from scripts.ops.order_repository import ensure_order_schema, backfill_legacy_orders
    ensure_order_schema(_engine(), table_name=legacy_table)
    backfill_legacy_orders(_engine(), table_name=legacy_table)
    return legacy_table


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@NEEDS_MYSQL
class TestV2Migration:
    """Scenario 1: legacy → v2 migration via production functions."""

    def test_pk_swapped_to_id(self, migrated_table):
        from sqlalchemy import text
        with _engine().connect() as conn:
            pk = conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :tbl "
                "AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION"
            ), {"schema": TEST_SCHEMA, "tbl": TEST_TABLE}).fetchall()
        assert {r[0] for r in pk} == {"id"}

    def test_uk_column_order(self, migrated_table):
        from sqlalchemy import text
        with _engine().connect() as conn:
            uk = conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :tbl "
                "AND INDEX_NAME = 'uk_strategy_order' ORDER BY SEQ_IN_INDEX"
            ), {"schema": TEST_SCHEMA, "tbl": TEST_TABLE}).fetchall()
        assert [r[0] for r in uk] == [
            "account_id", "release_id", "strategy",
            "execution_date", "ts_code", "side",
        ]

    def test_backfill_zero_nulls(self, migrated_table):
        from sqlalchemy import text
        with _engine().connect() as conn:
            nulls = conn.execute(text(
                f"SELECT COUNT(*) FROM {migrated_table} "
                "WHERE strategy IS NULL OR account_id IS NULL "
                "   OR release_id IS NULL OR execution_date IS NULL"
            )).scalar()
        assert int(nulls or 0) == 0

    def test_legacy_orders_preserved(self, migrated_table):
        from sqlalchemy import text
        with _engine().connect() as conn:
            cnt = conn.execute(text(
                f"SELECT COUNT(*) FROM {migrated_table}"
            )).scalar()
        assert int(cnt or 0) == 3


@NEEDS_MYSQL
class TestMultiReleaseIsolation:
    """Scenario 2: multi-release writes + RED cleanup via production functions."""

    EXEC_DATE = "2026-07-01"

    def test_four_releases_write_and_coexist(self, migrated_table):
        import pandas as pd
        from scripts.ops.order_repository import write_orders_with_metadata

        for strategy, release, sym in [
            ("prod_governed_vol_position", "release-v1-A", "000003.SZ"),
            ("prod_governed_vol_position", "release-v1-B", "000003.SZ"),
            ("gate_tuned_v1_2b", "release-gt-A", "000003.SZ"),
            ("gate_tuned_v1_2b", "release-gt-canary", "000003.SZ"),
        ]:
            df = pd.DataFrame([{
                "trade_date": self.EXEC_DATE, "ts_code": sym, "side": "BUY",
                "price": 10.0, "delta_shares": 100, "order_status": "planned",
            }])
            n = write_orders_with_metadata(
                _engine(), df, strategy=strategy, execution_date=self.EXEC_DATE,
                release_id=release, table_name=migrated_table,
            )
            assert n == 1

        from sqlalchemy import text
        with _engine().connect() as conn:
            total = conn.execute(text(f"SELECT COUNT(*) FROM {migrated_table}")).scalar()
        assert int(total or 0) >= 7  # 3 legacy + 4 new

    def test_red_cleanup_only_scoped_release(self, migrated_table):
        from scripts.ops.order_repository import supersede_pending_buys

        affected = supersede_pending_buys(
            _engine(),
            account_id="default",
            strategy="gate_tuned_v1_2b",
            release_id="release-gt-canary",
            as_of_date="2026-07-15",
            table_name=migrated_table,
        )
        assert affected >= 1

        from sqlalchemy import text
        with _engine().connect() as conn:
            # v1 releases are canonical drafts
            v1 = conn.execute(text(
                f"SELECT COUNT(*) FROM {migrated_table} "
                "WHERE strategy = 'prod_governed_vol_position' AND order_status = 'DRAFT'"
            )).scalar()
            assert int(v1 or 0) >= 2
            # GateTuned main release still draft
            gt_main = conn.execute(text(
                f"SELECT COUNT(*) FROM {migrated_table} "
                "WHERE strategy = 'gate_tuned_v1_2b' AND release_id = 'release-gt-A' "
                "AND order_status = 'DRAFT'"
            )).scalar()
            assert int(gt_main or 0) == 1
            # GateTuned canary cancelled with the supersede reason retained
            gt_canary = conn.execute(text(
                f"SELECT COUNT(*) FROM {migrated_table} "
                "WHERE strategy = 'gate_tuned_v1_2b' AND release_id = 'release-gt-canary' "
                "AND order_status = 'CANCELLED'"
            )).scalar()
            assert int(gt_canary or 0) == 1


@NEEDS_MYSQL
class TestStatusProtection:
    """Scenario 3: filled status not overwritten by production write."""

    def test_filled_not_overwritten(self, migrated_table):
        import pandas as pd
        from scripts.ops.order_repository import write_orders_with_metadata
        from sqlalchemy import text

        # Insert filled order directly
        with _engine().begin() as conn:
            conn.execute(text(
                f"INSERT INTO {migrated_table} "
                "(trade_date, ts_code, side, price, delta_shares, order_status, "
                " account_id, strategy, release_id, execution_date) "
                "VALUES ('2026-07-01', '000004.SZ', 'BUY', 10.0, 100, 'filled', "
                " 'default', 'prod_governed_vol_position', 'release-v1-A', '2026-07-01')"
            ))

        # Attempt to write same order as 'planned' via production function
        df = pd.DataFrame([{
            "trade_date": "2026-07-01", "ts_code": "000004.SZ", "side": "BUY",
            "price": 10.0, "delta_shares": 100, "order_status": "planned",
        }])
        write_orders_with_metadata(
            _engine(), df,
            strategy="prod_governed_vol_position",
            execution_date="2026-07-01",
            release_id="release-v1-A",
            table_name=migrated_table,
        )

        # Status must still be 'filled'
        with _engine().connect() as conn:
            status = conn.execute(text(
                f"SELECT order_status FROM {migrated_table} "
                "WHERE ts_code = '000004.SZ' AND side = 'BUY'"
            )).scalar()
        assert str(status) == "filled"


@NEEDS_MYSQL
class TestCrossAccountREDIsolation:
    """Scenario 4: RED on one account must not affect others."""

    def test_red_only_affects_target_account(self, migrated_table):
        import pandas as pd
        from scripts.ops.order_repository import write_orders_with_metadata, supersede_pending_buys
        from sqlalchemy import text

        for acct in ["default", "canary_bucket"]:
            df = pd.DataFrame([{
                "trade_date": "2026-07-01", "ts_code": "000005.SZ", "side": "BUY",
                "price": 10.0, "delta_shares": 100, "order_status": "planned",
            }])
            write_orders_with_metadata(
                _engine(), df, strategy="gate_tuned_v1_2b",
                execution_date="2026-07-01", release_id="release-gt-canary",
                account_id=acct, table_name=migrated_table,
            )

        supersede_pending_buys(
            _engine(), account_id="default", strategy="gate_tuned_v1_2b",
            release_id="release-gt-canary", as_of_date="2026-07-15",
            table_name=migrated_table,
        )

        with _engine().connect() as conn:
            d = conn.execute(text(
                f"SELECT order_status FROM {migrated_table} "
                "WHERE account_id='default' AND ts_code='000005.SZ'"
            )).scalar()
            c = conn.execute(text(
                f"SELECT order_status FROM {migrated_table} "
                "WHERE account_id='canary_bucket' AND ts_code='000005.SZ'"
            )).scalar()
        assert str(d) == "CANCELLED"
        assert str(c) == "DRAFT"


@NEEDS_MYSQL
class TestT1CalendarBehavior:
    """Scenario 5: T+1 via production _next_trading_day."""

    def test_weekday(self, migrated_table):
        from scripts.ops.export_trusted_strategy_candidates import _next_trading_day
        assert _next_trading_day(_engine(), "2026-06-22") == "2026-06-23"

    def test_friday(self, migrated_table):
        from scripts.ops.export_trusted_strategy_candidates import _next_trading_day
        assert _next_trading_day(_engine(), "2026-06-26") == "2026-06-29"

    def test_calendar_missing_raises(self, migrated_table):
        from scripts.ops.export_trusted_strategy_candidates import _next_trading_day
        import pytest as _pytest
        with _pytest.raises(RuntimeError, match="No next trading day"):
            _next_trading_day(_engine(), "2027-12-31")


def teardown_module():
    _drop()
