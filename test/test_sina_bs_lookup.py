import os

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from web import app as web_app


def _signal_row(code, *, buy=False, sell=False):
    return {
        "batch_date": "20260927",
        "stock_code": code,
        "has_buy_signal": buy,
        "has_sell_signal": sell,
        "buy_signal_description": "B signal" if buy else "",
        "sell_signal_description": "S signal" if sell else "",
        "total_b_points": int(buy),
        "total_s_points": int(sell),
        "buy_points_count": int(buy),
        "sell_points_count": int(sell),
    }


def test_lookup_scans_only_missing_codes_and_summarizes_results(monkeypatch):
    fetch_calls = []
    scan_calls = []

    def fetch(codes, batch_date):
        fetch_calls.append((list(codes), batch_date))
        if len(fetch_calls) == 1:
            return {"000001": _signal_row("000001", buy=True)}
        if len(fetch_calls) == 2:
            return {}
        return {"600519": _signal_row("600519", sell=True)}

    def scan(codes, batch_date):
        scan_calls.append((list(codes), batch_date))
        return 0

    monkeypatch.setattr(web_app, "_fetch_today_sina_bs_lookup", fetch)
    monkeypatch.setattr(web_app, "_run_sina_bs_lookup_scan", scan)

    response = web_app.app.test_client().post(
        "/api/sina/bs-lookup",
        json={"codes": "000001,sh600519,000001", "source": "config_1"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert [row["stock_code"] for row in payload["results"]] == ["000001", "600519"]
    assert [row["origin"] for row in payload["results"]] == ["今日数据库", "本次补扫"]
    assert scan_calls[0][0] == ["600519"]
    assert payload["summary"] == {
        "requested_count": 2,
        "found_count": 2,
        "database_count": 1,
        "scanned_count": 1,
        "not_found_count": 0,
        "buy_count": 1,
        "sell_count": 1,
        "buy_codes": ["000001"],
        "sell_codes": ["600519"],
    }
    assert len(fetch_calls) == 3


def test_lookup_does_not_scan_when_all_results_are_cached(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_fetch_today_sina_bs_lookup",
        lambda codes, _: {code: _signal_row(code) for code in codes},
    )
    monkeypatch.setattr(
        web_app,
        "_run_sina_bs_lookup_scan",
        lambda *_: (_ for _ in ()).throw(AssertionError("scan should not run")),
    )

    response = web_app.app.test_client().post(
        "/api/sina/bs-lookup",
        json={"codes": "600519,000001", "source": "config_1"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["database_count"] == 2
    assert payload["summary"]["scanned_count"] == 0


def test_lookup_rejects_invalid_codes_before_database_access(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_fetch_today_sina_bs_lookup",
        lambda *_: (_ for _ in ()).throw(AssertionError("database should not be queried")),
    )

    response = web_app.app.test_client().post(
        "/api/sina/bs-lookup",
        json={"codes": "600519,invalid", "source": "config_1"},
    )

    assert response.status_code == 400
    assert "无法识别" in response.get_json()["error"]
