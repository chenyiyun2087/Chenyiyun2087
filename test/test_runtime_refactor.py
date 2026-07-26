from __future__ import annotations

import hashlib

from scoreRank.core import db_runtime
from scripts.ops.candidate_export.metadata import (
    clear_metadata_cache,
    columns_for_table,
    table_exists,
)
from web.runtime_lifecycle import start_daemon_loops


class _Result:
    def __init__(self, *, scalar_value=None, rows=()):
        self.scalar_value = scalar_value
        self.rows = list(rows)

    def scalar(self):
        return self.scalar_value

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params):
        self.engine.calls.append((str(statement), params))
        if "information_schema.tables" in str(statement):
            return _Result(scalar_value=1)
        return _Result(rows=[("symbol",), ("trade_date",)])


class _MetadataEngine:
    def __init__(self):
        self.calls = []

    def connect(self):
        return _Connection(self)


def test_candidate_metadata_queries_are_cached_per_engine():
    engine = _MetadataEngine()

    assert table_exists(engine, "chenyiyun.score_rank_daily") is True
    assert table_exists(engine, "chenyiyun.score_rank_daily") is True
    assert columns_for_table(engine, "chenyiyun.score_rank_daily") == {
        "symbol",
        "trade_date",
    }
    assert columns_for_table(engine, "chenyiyun.score_rank_daily") == {
        "symbol",
        "trade_date",
    }
    assert len(engine.calls) == 2

    clear_metadata_cache(engine)
    columns_for_table(engine, "chenyiyun.score_rank_daily")
    assert len(engine.calls) == 3


def test_connect_pymysql_applies_bounded_timeout_defaults(monkeypatch):
    captured = {}
    sentinel = object()

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(db_runtime.pymysql, "connect", fake_connect)
    connection = db_runtime.connect_pymysql(
        {
            "host": "localhost",
            "user": "reader",
            "password": "secret",
            "database": "chenyiyun",
        }
    )

    assert connection is sentinel
    assert captured["connect_timeout"] == 10
    assert captured["read_timeout"] == 120
    assert captured["write_timeout"] == 120


def test_sqlalchemy_engine_is_reused_and_disposable(monkeypatch):
    created = []

    class FakeEngine:
        def __init__(self):
            self.disposed = False

        def dispose(self):
            self.disposed = True

    def fake_create_engine(url, **kwargs):
        engine = FakeEngine()
        created.append((url, kwargs, engine))
        return engine

    db_runtime.dispose_sqlalchemy_engines()
    monkeypatch.setattr(db_runtime, "build_sqlalchemy_url", lambda **_: "mysql+pymysql://example/db")
    monkeypatch.setattr(db_runtime, "create_engine", fake_create_engine)

    first = db_runtime.get_sqlalchemy_engine()
    second = db_runtime.get_sqlalchemy_engine()

    assert first is second
    assert len(created) == 1
    assert created[0][1]["pool_pre_ping"] is True
    db_runtime.dispose_sqlalchemy_engines()
    assert first.disposed is True


def test_runtime_lifecycle_starts_named_daemon_threads(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started.append(self.name)

    monkeypatch.setattr(
        "web.runtime_lifecycle.threading.Thread",
        FakeThread,
    )
    targets = (("scheduler", lambda: None), ("worker", lambda: None))

    threads = start_daemon_loops(targets)

    assert [thread.name for thread in threads] == ["scheduler", "worker"]
    assert all(thread.daemon for thread in threads)
    assert started == ["scheduler", "worker"]


def test_web_route_and_endpoint_contract_is_unchanged(monkeypatch):
    monkeypatch.setenv("DISABLE_APP_SCHEDULER_LOOP", "1")
    from web.app import app

    rows = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
        rows.append(f"{methods} {rule.rule} -> {rule.endpoint}")
    payload = "\n".join(sorted(rows))

    assert len(rows) == 45
    assert hashlib.sha256(payload.encode()).hexdigest() == (
        "e7242a204604c459afcca6af363e113f81fa419b06426ae1a947ede6ade3bff4"
    )


def test_legacy_order_transition_remains_compatible_without_terminal_regression():
    from runtime.order_state_machine import is_valid_transition

    assert is_valid_transition("planned", "submitted") is True
    assert is_valid_transition("submitted_manually", "submitted") is True
    assert is_valid_transition("filled", "planned") is False
