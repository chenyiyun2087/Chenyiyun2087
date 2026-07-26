"""Per-run table metadata cache used by candidate generation."""

from __future__ import annotations

import re
import threading

from sqlalchemy import text


_CACHE_ATTRIBUTE = "_chenyiyun_candidate_table_metadata"
_CACHE_LOCK = threading.Lock()
_TABLE_NAME = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?$")


def safe_table_name(table: str) -> str:
    value = str(table or "").strip()
    if not value:
        raise ValueError("empty table name")
    if not _TABLE_NAME.fullmatch(value):
        raise ValueError(f"invalid table name: {table}")
    return value


def _engine_cache(engine) -> dict[tuple[str, str], object] | None:
    cache = getattr(engine, _CACHE_ATTRIBUTE, None)
    if cache is not None:
        return cache
    with _CACHE_LOCK:
        cache = getattr(engine, _CACHE_ATTRIBUTE, None)
        if cache is None:
            cache = {}
            try:
                setattr(engine, _CACHE_ATTRIBUTE, cache)
            except (AttributeError, TypeError):
                return None
    return cache


def clear_metadata_cache(engine) -> None:
    cache = getattr(engine, _CACHE_ATTRIBUTE, None)
    if isinstance(cache, dict):
        cache.clear()


def table_exists(engine, full_table_name: str) -> bool:
    table = safe_table_name(full_table_name)
    cache = _engine_cache(engine)
    key = ("exists", table)
    if cache is not None and key in cache:
        return bool(cache[key])

    if "." in table:
        schema, name = table.split(".", 1)
        sql = text(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = :schema AND table_name = :name
            """
        )
        params = {"schema": schema, "name": name}
    else:
        sql = text("SHOW TABLES LIKE :name")
        params = {"name": table}
    with engine.connect() as connection:
        result = bool(connection.execute(sql, params).scalar())
    if cache is not None:
        cache[key] = result
    return result


def columns_for_table(engine, full_table_name: str) -> set[str]:
    table = safe_table_name(full_table_name)
    cache = _engine_cache(engine)
    key = ("columns", table)
    if cache is not None and key in cache:
        return set(cache[key])

    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = None, table
    sql = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = COALESCE(:schema, DATABASE())
          AND table_name = :name
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(
            sql,
            {"schema": schema, "name": name},
        ).fetchall()
    result = {str(row[0]) for row in rows}
    if cache is not None:
        cache[key] = frozenset(result)
    return result
