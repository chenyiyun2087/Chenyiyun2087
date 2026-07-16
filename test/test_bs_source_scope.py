import os

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from web import app as web_app


class SourceScopedCursor:
    def __init__(self):
        self.calls = []
        self._one = None
        self._all = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        compact = " ".join(sql.split()).lower()
        self._all = []
        if "count(*) as total from" in compact:
            self._one = {"total": 0}
        elif "count(*) as total" in compact:
            self._one = {
                "total": 435,
                "buy_count": 24,
                "sell_count": 25,
                "last_update": None,
            }
        elif "max(batch_date) as last_date" in compact:
            self._one = {"last_date": "20260714"}
        else:
            self._one = None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class SourceScopedConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_bs_source_defaults_to_ocr_and_rejects_unknown_values():
    assert web_app._resolve_bs_source(None) == "config_1"
    assert web_app._resolve_bs_source("ml_detect_v3") == "ml_detect_v3"
    assert web_app._resolve_bs_source("all") == "config_1"


def test_sina_summary_scopes_every_query_to_selected_batch(monkeypatch):
    cursor = SourceScopedCursor()
    monkeypatch.setattr(web_app, "get_db", lambda: SourceScopedConnection(cursor))

    response = web_app.app.test_client().get(
        "/sina/monitor?tab=summary&date=2026-07-14&source=ml_detect_v3"
    )

    assert response.status_code == 200
    assert "ML全量检测" in response.get_data(as_text=True)
    bs_calls = [(sql, params) for sql, params in cursor.calls if "bs_detection_results" in sql]
    assert bs_calls
    assert all("batch_name" in sql for sql, _ in bs_calls)
    assert all("ml_detect_v3" in params for _, params in bs_calls)


def test_signal_stats_api_defaults_to_ocr_batch(monkeypatch):
    cursor = SourceScopedCursor()
    monkeypatch.setattr(web_app, "get_db", lambda: SourceScopedConnection(cursor))

    response = web_app.app.test_client().get("/api/signal_stats")

    assert response.status_code == 200
    assert cursor.calls[-1][1] == ("config_1",)
    assert "where batch_name = %s" in " ".join(cursor.calls[-1][0].split()).lower()
