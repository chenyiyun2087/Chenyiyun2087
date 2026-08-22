from __future__ import annotations

from web import app as web_app


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.sql = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_obj = _Cursor(row)

    def cursor(self):
        return self.cursor_obj

    def close(self):
        return None


def test_candle_verifier_uses_target_day_distinct_pit_baseline(monkeypatch):
    conn = _Connection({
        "rows_cnt": 5538,
        "bj_rows": 335,
        "expected_rows": 5539,
        "expected_bj_rows": 336,
    })
    monkeypatch.setattr(web_app, "_connect_db", lambda: conn)

    ok, lines = web_app._verify_candle_diag_scan_result(
        None, None, {"datestr": "2026-08-17"}
    )

    assert ok is True
    assert "COUNT(DISTINCT c.symbol)" in conn.cursor_obj.sql
    assert conn.cursor_obj.params == ("20260817", "20260817", "2026-08-17")
    assert "coverage_ratio=0.999819" in lines[0]
    assert "bj_coverage_ratio=0.997024" in lines[0]


def test_candle_verifier_blocks_when_pit_baseline_is_missing(monkeypatch):
    conn = _Connection({
        "rows_cnt": 5539,
        "bj_rows": 335,
        "expected_rows": 0,
        "expected_bj_rows": 0,
    })
    monkeypatch.setattr(web_app, "_connect_db", lambda: conn)

    ok, lines = web_app._verify_candle_diag_scan_result(
        None, None, {"datestr": "20260817"}
    )

    assert ok is False
    assert lines[0].startswith("result=BLOCKED;")
    assert "reason=missing_pit_baseline" in lines[0]
    assert "verification_blocked=true" in lines[0]


def test_verification_block_is_not_automatically_retried():
    assert web_app._classify_task_failure(
        "Failed", 1, "[verify] result=BLOCKED; reason=missing_pit_baseline"
    ) == ("VERIFICATION", False)
