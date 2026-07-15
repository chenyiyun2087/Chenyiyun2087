import pytest

from scoreRank.core.db_config import (
    build_pymysql_config,
    build_sqlalchemy_url,
    require_sqlalchemy_url,
)
from scripts.ops.check_db_connection import mask_sqlalchemy_url


def test_sqlalchemy_url_preserves_tcp_default_without_socket(monkeypatch):
    monkeypatch.delenv("CHENYIYUN_DB_URL", raising=False)
    monkeypatch.delenv("CHENYIYUN_DB_UNIX_SOCKET", raising=False)
    monkeypatch.delenv("CHENYIYUN_DB_PASSWORD", raising=False)
    monkeypatch.setenv("CHENYIYUN_DB_USER", "root")
    monkeypatch.setenv("CHENYIYUN_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("CHENYIYUN_DB_PORT", "3307")
    monkeypatch.setenv("CHENYIYUN_DB_NAME", "chenyiyun")

    assert build_sqlalchemy_url().startswith("mysql+pymysql://root@127.0.0.1:3307/chenyiyun?")


def test_sqlalchemy_url_and_pymysql_config_support_unix_socket(monkeypatch):
    monkeypatch.delenv("CHENYIYUN_DB_URL", raising=False)
    monkeypatch.setenv("CHENYIYUN_DB_USER", "root")
    monkeypatch.setenv("CHENYIYUN_DB_HOST", "localhost")
    monkeypatch.setenv("CHENYIYUN_DB_NAME", "chenyiyun")
    monkeypatch.setenv("CHENYIYUN_DB_UNIX_SOCKET", "/tmp/mysql.sock")

    url = build_sqlalchemy_url()
    config = build_pymysql_config(dict_cursor=False)

    assert ":3306" not in url
    assert "unix_socket=%2Ftmp%2Fmysql.sock" in url
    assert config["unix_socket"] == "/tmp/mysql.sock"


def test_mask_sqlalchemy_url_hides_password():
    masked = mask_sqlalchemy_url("mysql+pymysql://user:secret@localhost:3306/chenyiyun?charset=utf8mb4")

    assert "secret" not in masked
    assert "user:***@" in masked


def test_explicit_url_can_select_logical_database(monkeypatch):
    monkeypatch.setenv(
        "CHENYIYUN_DB_URL",
        "mysql+pymysql://readonly:secret@db.internal:3306/chenyiyun?charset=utf8mb4",
    )
    url = build_sqlalchemy_url(database="tushare_stock")
    assert "readonly:secret@db.internal:3306/tushare_stock" in url

    config = build_pymysql_config(dict_cursor=False)
    assert config["user"] == "readonly"
    assert config["password"] == "secret"


def test_required_url_fails_closed_without_explicit_url(monkeypatch):
    monkeypatch.delenv("CHENYIYUN_DB_URL", raising=False)
    with pytest.raises(RuntimeError, match="CHENYIYUN_DB_URL"):
        require_sqlalchemy_url()
