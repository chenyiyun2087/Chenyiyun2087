from __future__ import annotations

import os
from urllib.parse import quote_plus, urlencode

import pymysql


def build_sqlalchemy_url(prefix: str = "CHENYIYUN_DB") -> str:
    explicit = os.getenv(f"{prefix}_URL")
    if explicit:
        return explicit

    user = os.getenv(f"{prefix}_USER", "root")
    password = os.getenv(f"{prefix}_PASSWORD", "")
    host = os.getenv(f"{prefix}_HOST", "localhost")
    port = os.getenv(f"{prefix}_PORT", "3306")
    database = os.getenv(f"{prefix}_NAME", "chenyiyun")
    charset = os.getenv(f"{prefix}_CHARSET", "utf8mb4")
    unix_socket = os.getenv(f"{prefix}_UNIX_SOCKET", "").strip()
    auth = quote_plus(user)
    if password:
        auth = f"{auth}:{quote_plus(password)}"
    query = {"charset": charset}
    if unix_socket:
        query["unix_socket"] = unix_socket
        return f"mysql+pymysql://{auth}@{host}/{database}?{urlencode(query)}"
    return f"mysql+pymysql://{auth}@{host}:{port}/{database}?{urlencode(query)}"


def build_pymysql_config(prefix: str = "CHENYIYUN_DB", dict_cursor: bool = True) -> dict:
    unix_socket = os.getenv(f"{prefix}_UNIX_SOCKET", "").strip()
    config = {
        "host": os.getenv(f"{prefix}_HOST", "localhost"),
        "port": int(os.getenv(f"{prefix}_PORT", "3306")),
        "user": os.getenv(f"{prefix}_USER", "root"),
        "password": os.getenv(f"{prefix}_PASSWORD", ""),
        "database": os.getenv(f"{prefix}_NAME", "chenyiyun"),
        "charset": os.getenv(f"{prefix}_CHARSET", "utf8mb4"),
    }
    if unix_socket:
        config["unix_socket"] = unix_socket
    if dict_cursor:
        config["cursorclass"] = pymysql.cursors.DictCursor
    return config


def validate_db_credentials(prefix: str = "CHENYIYUN_DB") -> bool:
    """Check that database credentials are properly configured via env vars.

    Returns False if using default empty password (unsafe for production),
    True if properly configured with a non-empty password or explicit URL.
    """
    import warnings

    explicit = os.getenv(f"{prefix}_URL")
    if explicit:
        # If an explicit URL is provided, trust it (caller's responsibility)
        return True

    user = os.getenv(f"{prefix}_USER", "root")
    password = os.getenv(f"{prefix}_PASSWORD", "")
    if user == "root" and not password:
        warnings.warn(
            "Using MySQL root with empty password. "
            f"Set {prefix}_PASSWORD or {prefix}_URL for production use.",
            RuntimeWarning,
            stacklevel=2,
        )
        return False
    return True


def symbol_to_ts_code(symbol: str) -> str:
    s = str(symbol or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())[-6:].zfill(6)
    if digits.startswith(("6", "9")):
        return f"{digits}.SH"
    if digits.startswith(("4", "8")):
        return f"{digits}.BJ"
    return f"{digits}.SZ"


def symbols_to_ts_codes(symbols: list[str] | tuple[str, ...]) -> list[str]:
    return [symbol_to_ts_code(s) for s in symbols]
