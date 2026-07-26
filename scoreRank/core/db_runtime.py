"""Shared database runtime primitives for long-lived production processes.

Configuration remains owned by :mod:`scoreRank.core.db_config`.  This module
only centralizes connection construction, pooling, and transaction cleanup so
Web requests and scheduled jobs do not each invent their own lifecycle.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

import pymysql
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from scoreRank.core.db_config import build_pymysql_config, build_sqlalchemy_url


_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[tuple[str, int, int, int], Engine] = {}


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def connect_pymysql(
    config: Mapping[str, Any] | None = None,
    *,
    autocommit: bool | None = None,
) -> pymysql.connections.Connection:
    """Create a bounded PyMySQL connection with consistent timeout defaults."""

    resolved = dict(config or build_pymysql_config())
    resolved.setdefault(
        "connect_timeout",
        _positive_int_env("CHENYIYUN_DB_CONNECT_TIMEOUT_SECONDS", 10),
    )
    resolved.setdefault(
        "read_timeout",
        _positive_int_env("CHENYIYUN_DB_READ_TIMEOUT_SECONDS", 120),
    )
    resolved.setdefault(
        "write_timeout",
        _positive_int_env("CHENYIYUN_DB_WRITE_TIMEOUT_SECONDS", 120),
    )
    if autocommit is not None:
        resolved["autocommit"] = autocommit
    return pymysql.connect(**resolved)


@contextmanager
def pymysql_transaction(
    config: Mapping[str, Any] | None = None,
    *,
    read_only: bool = False,
) -> Iterator[pymysql.connections.Connection]:
    """Yield a connection and guarantee commit/rollback/close semantics."""

    connection = connect_pymysql(config)
    try:
        if read_only:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_sqlalchemy_engine(*, database: str | None = None) -> Engine:
    """Return one pre-pinged SQLAlchemy pool per effective URL and pool policy."""

    url = build_sqlalchemy_url(database=database)
    pool_size = _positive_int_env("CHENYIYUN_DB_POOL_SIZE", 5)
    max_overflow = _positive_int_env("CHENYIYUN_DB_MAX_OVERFLOW", 10)
    pool_recycle = _positive_int_env("CHENYIYUN_DB_POOL_RECYCLE_SECONDS", 1800)
    key = (url, pool_size, max_overflow, pool_recycle)
    engine = _ENGINES.get(key)
    if engine is not None:
        return engine
    with _ENGINE_LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = create_engine(
                url,
                pool_pre_ping=True,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_recycle=pool_recycle,
            )
            _ENGINES[key] = engine
    return engine


def dispose_sqlalchemy_engines() -> None:
    """Dispose cached pools, primarily for process shutdown and isolated tests."""

    with _ENGINE_LOCK:
        engines = list(_ENGINES.values())
        _ENGINES.clear()
    for engine in engines:
        engine.dispose()
