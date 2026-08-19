import pandas as pd
import pytest

from integration.snapshot_cache import write_snapshot
from scripts.research import build_bs_training_dataset as training_dataset


def test_training_labels_use_one_exact_batch_and_retain_batch_name(monkeypatch):
    calls = {}

    def fake_read_sql(query, engine, params=None):
        calls["query"] = str(query)
        calls["params"] = params
        return pd.DataFrame(
            [
                {
                    "stock_code": "000001",
                    "batch_date": "20260601",
                    "has_buy_signal": 1,
                    "has_sell_signal": 0,
                    "label_batch_name": "config_1",
                },
                {
                    "stock_code": "000002",
                    "batch_date": "20260602",
                    "has_buy_signal": 0,
                    "has_sell_signal": 1,
                    "label_batch_name": "config_1",
                },
            ]
        )

    monkeypatch.setattr(training_dataset.pd, "read_sql", fake_read_sql)
    labels = training_dataset.load_labels(object())

    assert calls["params"] == {"batch_name": "config_1"}
    assert "batch_name = :batch_name" in calls["query"]
    assert set(labels["label_batch_name"]) == {"config_1"}


def test_training_labels_fail_on_same_stock_date_duplicate(monkeypatch):
    def fake_read_sql(*_args, **_kwargs):
        return pd.DataFrame(
            [
                {
                    "stock_code": "000001",
                    "batch_date": "20260601",
                    "has_buy_signal": 1,
                    "has_sell_signal": 0,
                    "label_batch_name": "config_1",
                },
                {
                    "stock_code": "000001",
                    "batch_date": "20260601",
                    "has_buy_signal": 0,
                    "has_sell_signal": 1,
                    "label_batch_name": "config_1",
                },
            ]
        )

    monkeypatch.setattr(training_dataset.pd, "read_sql", fake_read_sql)
    with pytest.raises(ValueError, match="Duplicate labels"):
        training_dataset.load_labels(object(), batch_name="config_1")


class _FakeConnection:
    def __init__(self, duplicate=False):
        self.duplicate = duplicate
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))

        class Result:
            def __init__(self, duplicate):
                self.duplicate = duplicate

            def scalar(self):
                return 1 if self.duplicate else None

        if self.calls[-1][0].startswith("SELECT 1"):
            return Result(self.duplicate)
        return Result(False)


def test_snapshot_write_uses_supplied_transaction_connection():
    connection = _FakeConnection()

    class EngineMustNotBeOpened:
        def begin(self):
            raise AssertionError("write_snapshot opened a second transaction")

    write_snapshot(
        EngineMustNotBeOpened(),
        "rs_20260815_test",
        pd.Timestamp("2026-08-15").date(),
        "factor_test",
        "bs:config_1",
        "commit-test",
        {"scored_count": 1},
        connection=connection,
    )

    assert any("INSERT INTO chenyiyun.ads_research_snapshots" in sql for sql, _ in connection.calls)


def test_duplicate_snapshot_id_is_rejected_before_insert():
    connection = _FakeConnection(duplicate=True)
    with pytest.raises(ValueError, match="immutable"):
        write_snapshot(
            object(),
            "rs_20260815_duplicate",
            pd.Timestamp("2026-08-15").date(),
            "factor_test",
            "bs:config_1",
            "commit-test",
            {"scored_count": 1},
            connection=connection,
        )
