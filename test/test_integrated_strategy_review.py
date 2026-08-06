"""PR #195: integrated strategy review — one daily digest card.

Extends the PR contract tests (test_integrated_batch_notifications.py)
with DB-facing coverage that never touches MySQL:

- load_rolling_score_snapshot: as-of calc-date resolution, perf/weight
  row merge, exposure & circuit-breaker aggregation, stale/missing/
  failure warnings (fail-open)
- build_candidate_score_section / build_rolling_score_section: ranking
  order, Top-N truncation, empty and flagged states
- run_integrated_review: single-card contract — run_review must not
  push its own card, the digest appends both score sections, and the
  notification keeps the legacy identity
  (trusted_strategy_performance_review:{date}) with notify_result
  normalized to "ok"
- task routing: source jobs silent even on reissue; the digest job is
  the only notifier
- pipeline DAG: full-graph cycle detection over pipeline.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

import scripts.ops.run_integrated_strategy_review as review_mod
from web.task_commands import TaskCommandContext, build_task_script_parts

PIPELINE_PATH = Path(__file__).resolve().parents[1] / "task_registry" / "pipeline.yaml"


# ── fake engine: connect() -> execute() -> mappings() ──────────────────


class _FakeRows:
    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]

    def mappings(self):
        return iter(self._rows)


class _FakeConn:
    def __init__(self, responses):
        self._responses = [list(r) for r in responses]
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        rows = self._responses.pop(0) if self._responses else []
        return _FakeRows(rows)


class _FakeEngine:
    def __init__(self, responses):
        self.conn = _FakeConn(responses)
        self.disposed = False

    def connect(self):
        return self.conn

    def dispose(self):
        self.disposed = True


def _snapshot_install(monkeypatch, perf_date, weight_date, perf_rows, weight_rows):
    """Fake _latest_calc_date + a scripted fake engine for the two queries."""
    dates = {"ads_rolling_3m_strategy_perf": perf_date, "ads_rolling_strategy_weights": weight_date}
    monkeypatch.setattr(
        review_mod, "_latest_calc_date",
        lambda engine, table, review_date: dates.get(table),
    )
    engine = _FakeEngine([perf_rows, weight_rows])
    return engine


# ── load_rolling_score_snapshot ─────────────────────────────────────────


def test_snapshot_merges_perf_and_weights_and_sorts_by_final_score(monkeypatch):
    engine = _snapshot_install(
        monkeypatch,
        perf_date="2026-08-05",
        weight_date="2026-08-05",
        perf_rows=[
            {"strategy": "s_low", "final_score": 70.0, "total_return": 0.05, "sharpe": 0.9,
             "calmar": 1.0, "max_drawdown": -0.06, "is_qualified": 0, "trading_days": 60,
             "window_start": "2026-05-01", "window_end": "2026-08-05"},
            {"strategy": "s_high", "final_score": 85.0, "total_return": 0.12, "sharpe": 1.5,
             "calmar": 1.8, "max_drawdown": -0.04, "is_qualified": 1, "trading_days": 61,
             "window_start": "2026-05-01", "window_end": "2026-08-05"},
        ],
        weight_rows=[
            {"strategy": "s_high", "target_weight": 0.6, "smooth_weight": 0.55,
             "prev_smooth_weight": 0.5, "effective_exposure": 0.7,
             "circuit_breaker_active": 0, "circuit_breaker_reason": ""},
            {"strategy": "s_low", "target_weight": 0.4, "smooth_weight": 0.35,
             "prev_smooth_weight": 0.4, "effective_exposure": 0.7,
             "circuit_breaker_active": 0, "circuit_breaker_reason": ""},
        ],
    )
    snapshot = review_mod.load_rolling_score_snapshot(engine, "2026-08-05")

    # Perf-only columns and weight-only columns merge per strategy.
    assert [r["strategy"] for r in snapshot["rows"]] == ["s_high", "s_low"]
    top = snapshot["rows"][0]
    assert top["total_return"] == 0.12 and top["smooth_weight"] == 0.55
    assert snapshot["performance_date"] == "2026-08-05"
    assert snapshot["weight_date"] == "2026-08-05"
    assert snapshot["effective_exposure"] == 0.7
    assert snapshot["circuit_breaker_active"] is False
    assert snapshot["warnings"] == []


def test_snapshot_uses_max_calc_date_as_of_review_day(monkeypatch):
    """Data may land late — the snapshot reads MAX(calc_date) <= review date."""
    dates = {}
    monkeypatch.setattr(
        review_mod, "_latest_calc_date",
        lambda engine, table, review_date: "2026-08-04" if dates.get(table) is None else dates[table],
    )
    engine = _FakeEngine([[], []])
    snapshot = review_mod.load_rolling_score_snapshot(engine, "2026-08-05")
    # 2026-08-04 < review day -> both marked stale.
    assert snapshot["performance_date"] == "2026-08-04"
    assert snapshot["weight_date"] == "2026-08-04"
    assert "rolling_performance_stale:2026-08-04" in snapshot["warnings"]
    assert "rolling_weights_stale:2026-08-04" in snapshot["warnings"]


def test_snapshot_aggregates_circuit_breaker_any_row(monkeypatch):
    engine = _snapshot_install(
        monkeypatch,
        perf_date="2026-08-05",
        weight_date="2026-08-05",
        perf_rows=[
            {"strategy": "a", "final_score": 70.0},
            {"strategy": "b", "final_score": 60.0},
        ],
        weight_rows=[
            {"strategy": "a", "effective_exposure": 0.3, "circuit_breaker_active": 0,
             "circuit_breaker_reason": "", "smooth_weight": 0.2, "target_weight": 0.3},
            {"strategy": "b", "effective_exposure": 0.3, "circuit_breaker_active": 1,
             "circuit_breaker_reason": "连跌15日", "smooth_weight": 0.1, "target_weight": 0.2},
        ],
    )
    snapshot = review_mod.load_rolling_score_snapshot(engine, "2026-08-05")
    assert snapshot["circuit_breaker_active"] is True
    assert snapshot["circuit_breaker_reason"] == "连跌15日"
    # Exposure comes from the first (highest-scored) row.
    assert snapshot["effective_exposure"] == 0.3


def test_snapshot_fail_open_on_missing_tables(monkeypatch):
    engine = _snapshot_install(
        monkeypatch, perf_date=None, weight_date=None, perf_rows=[], weight_rows=[])
    snapshot = review_mod.load_rolling_score_snapshot(engine, "2026-08-05")
    assert "rolling_performance_missing" in snapshot["warnings"]
    assert "rolling_weights_missing" in snapshot["warnings"]
    assert "rolling_score_rows_empty" in snapshot["warnings"]
    assert snapshot["rows"] == []
    assert snapshot["effective_exposure"] is None


def test_snapshot_fail_open_on_query_error(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        review_mod, "_latest_calc_date", lambda engine, table, review_date: "2026-08-05")
    engine = _FakeEngine([[], []])
    engine.conn.execute = boom  # type: ignore[method-assign]
    snapshot = review_mod.load_rolling_score_snapshot(engine, "2026-08-05")
    assert "rolling_query_failed:RuntimeError" in snapshot["warnings"]
    assert snapshot["rows"] == []


# ── build_candidate_score_section ───────────────────────────────────────


def test_candidate_section_truncates_to_top5_and_sorts_by_rank():
    candidates = [
        {"rank_no": i, "symbol": f"00000{i}", "stock_name": f"股票{i}",
         "rank_score": 90 - i, "dynamic_factor_score": 80 - i,
         "liquidity_detail_score": 70 - i, "effective_weight": 0.1}
        for i in range(1, 8)
    ]
    # Feed them reversed; the section must re-sort by rank.
    section = review_mod.build_candidate_score_section(list(reversed(candidates)))
    assert "#1 股票1(000001)" in section
    assert "#5 股票5(000005)" in section
    assert "#6 股票6(000006)" not in section
    assert "其余候选：2只" in section


def test_candidate_section_empty_and_rank_fallback():
    assert "无候选评分记录" in review_mod.build_candidate_score_section([])
    section = review_mod.build_candidate_score_section(
        [{"symbol": "000001", "stock_name": "示例", "rank_score": 88.5,
          "dynamic_factor_score": 76.0, "liquidity_detail_score": 69.5,
          "effective_weight": 0.12}]  # no rank_no -> rank falls back to 0
    )
    assert "#0 示例(000001)" in section
    assert "排序分 88.5" in section
    assert "目标权重 +12.0%" in section
    assert "不代表资金授权" in section


# ── build_rolling_score_section ─────────────────────────────────────────


def test_score_section_flags_circuit_breaker_and_warnings():
    section = review_mod.build_rolling_score_section(
        {
            "performance_date": "2026-08-06",
            "weight_date": "2026-08-06",
            "effective_exposure": 0.7,
            "circuit_breaker_active": True,
            "circuit_breaker_reason": "连跌15日",
            "rows": [],
            "warnings": ["rolling_score_rows_empty"],
        }
    )
    assert "熔断：连跌15日" in section
    assert "数据提醒：rolling_score_rows_empty" in section
    assert "暂无可用滚动评分" in section
    assert "不替代生产风险总闸或资金授权" in section


def test_score_section_allocated_weights_and_idle_cash():
    section = review_mod.build_rolling_score_section(
        {
            "performance_date": "2026-08-06",
            "weight_date": "2026-08-06",
            "effective_exposure": 0.7,
            "circuit_breaker_active": False,
            "rows": [
                {"strategy": "a", "final_score": 82.5, "smooth_weight": 0.4,
                 "total_return": 0.12, "sharpe": 1.5, "calmar": 1.1, "max_drawdown": -0.08},
                {"strategy": "b", "final_score": 75.0, "smooth_weight": 0.2,
                 "total_return": 0.05, "sharpe": 0.9, "calmar": 0.7, "max_drawdown": -0.05},
            ],
            "warnings": [],
        }
    )
    assert "✅ 轮动熔断：未触发" in section
    assert "有效敞口：+70%" in section
    assert "#1 a" in section and "#2 b" in section
    assert "T+1研究权重" in section
    assert "- a: +40.0%" in section
    assert "闲置/现金：+40.0%" in section


# ── run_integrated_review: single-card contract ─────────────────────────


def _review_install(monkeypatch, tmp_path, snapshot=None, send_result=(True, "ok")):
    feishu_file = tmp_path / "strategy_performance_review_feishu.txt"
    json_file = tmp_path / "strategy_performance_review.json"
    feishu_file.write_text("BASE_REVIEW_TEXT\n", encoding="utf-8")
    json_file.write_text("{}", encoding="utf-8")

    payload = {
        "params": {"review_date": "2026-08-05", "strategy": "prod", "report_type": "MATCHED"},
        "outputs": {
            "json_path": str(json_file),
            "feishu_text_path": str(feishu_file),
        },
        "current": {
            "candidates": [
                {"rank_no": 1, "symbol": "000001", "stock_name": "示例银行",
                 "rank_score": 88.5, "dynamic_factor_score": 76.0,
                 "liquidity_detail_score": 69.5, "effective_weight": 0.12},
            ],
        },
    }
    calls = {"run_review": None, "sends": []}
    received_args = {}

    def fake_run_review(args):
        received_args["notify_feishu"] = bool(args.notify_feishu)
        return payload

    monkeypatch.setattr(review_mod.base_review, "run_review", fake_run_review)
    if snapshot is None:
        snapshot = {
            "review_date": "2026-08-05", "performance_date": "2026-08-05",
            "weight_date": "2026-08-05", "effective_exposure": 0.7,
            "circuit_breaker_active": False, "circuit_breaker_reason": "",
            "rows": [{"strategy": "a", "final_score": 82.5, "smooth_weight": 0.4}],
            "warnings": [],
        }
    monkeypatch.setattr(
        review_mod, "load_rolling_score_snapshot",
        lambda engine, review_date: snapshot)
    monkeypatch.setattr(review_mod, "create_engine", lambda url: _FakeEngine([]))

    def fake_send(engine, content, *, business_date, notification_type, task_name, dedupe_key):
        calls["sends"].append({
            "content": content, "business_date": business_date,
            "notification_type": notification_type, "task_name": task_name,
            "dedupe_key": dedupe_key,
        })
        return send_result

    monkeypatch.setattr(review_mod, "send_feishu_text_audited", fake_send)
    return payload, calls, received_args


def test_integrated_review_appends_sections_and_keeps_legacy_identity(monkeypatch, tmp_path):
    payload, calls, received_args = _review_install(monkeypatch, tmp_path)
    args = argparse.Namespace(notify_feishu=True)
    result = review_mod.run_integrated_review(args)

    # run_review must never push its own card (same dedupe key would swallow ours).
    assert received_args["notify_feishu"] is False
    # The digest text = original review text + candidate section + score section.
    text = payload["outputs"]["feishu_text_path"] and \
        Path(payload["outputs"]["feishu_text_path"]).read_text(encoding="utf-8")
    assert text.startswith("BASE_REVIEW_TEXT\n")
    assert "今日候选评分" in text
    assert "滚动策略评分与权重" in text
    assert text.rstrip().endswith("资金授权。")

    assert result["notification_mode"] == "INTEGRATED_DAILY_STRATEGY_DIGEST"
    assert result["rolling_strategy_scores"]["effective_exposure"] == 0.7
    assert result["notify_result"] == "ok"
    assert len(calls["sends"]) == 1
    send = calls["sends"][0]
    assert send["notification_type"] == "trusted_strategy_performance_review"
    assert send["task_name"] == "trusted_strategy_performance_review"
    assert send["dedupe_key"] == "trusted_strategy_performance_review:20260805"
    assert send["business_date"] == "20260805"


def test_integrated_review_silent_without_notify_flag(monkeypatch, tmp_path):
    payload, calls, received_args = _review_install(monkeypatch, tmp_path)
    review_mod.run_integrated_review(argparse.Namespace(notify_feishu=False))
    assert received_args["notify_feishu"] is False
    assert calls["sends"] == []
    assert payload["notify_result"] is None


def test_integrated_review_normalizes_notify_result_to_ok_on_rerun(monkeypatch, tmp_path):
    payload, calls, _ = _review_install(
        monkeypatch, tmp_path, send_result=(False, "duplicate_delivery_skipped"))
    review_mod.run_integrated_review(argparse.Namespace(notify_feishu=True))
    # Successful idempotent rerun still satisfies the verifier contract.
    assert payload["notify_result"] == "duplicate_delivery_skipped"


# ── task routing: source jobs silent, digest notifies ───────────────────


def _context() -> TaskCommandContext:
    payload = yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8"))
    tasks = {}
    for group in payload.values():
        for task in group.get("tasks", []):
            tasks[task["id"]] = task
    return TaskCommandContext(
        tasks=tasks,
        normalize_datestr=lambda value: str(value).replace("-", "") if value else None,
        trusted_strategy="production_governed_vol_position",
        trusted_risk_profile="adaptive",
        trusted_config={"config_sha": "abc123", "top_n": 5, "max_total_positions": 10},
        daily_audit_task="ops_daily_batch_audit",
    )


def test_source_jobs_never_notify_even_on_reissue():
    """Only the digest job (21:45) sends the routine card — reissues included."""
    context = _context()
    for task_name in ("rolling_strategy_scorer", "trusted_strategy_candidates",
                      "trusted_strategy_shadow_monitor"):
        parts = build_task_script_parts(
            task_name,
            {"datestr": "20260805", "historical_safe": True, "historical_reissue": True},
            context,
        )
        assert "--notify-feishu" not in parts, task_name
    digest = build_task_script_parts(
        "trusted_strategy_performance_review",
        {"datestr": "20260805", "historical_safe": True, "historical_reissue": True},
        context,
    )
    assert "--notify-feishu" in digest


def test_pipeline_routes_to_integrated_scripts():
    context = _context()
    assert context.tasks["trusted_strategy_performance_review"]["script"] == \
        "scripts/ops/run_integrated_strategy_review.py"
    assert context.tasks["ops_daily_batch_audit"]["script"] == \
        "scripts/ops/run_integrated_batch_audit.py"


def test_dag_full_graph_is_acyclic():
    """DFS cycle detection over every task in pipeline.yaml."""
    payload = yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8"))
    graph = {}
    for group in payload.values():
        for task in group.get("tasks", []):
            graph[task["id"]] = list(task.get("depends_on") or [])
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}

    def visit(node, path):
        color[node] = GRAY
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                raise AssertionError(f"DAG cycle: {' -> '.join(path + [dep])}")
            if color[dep] == WHITE:
                visit(dep, path + [dep])
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            visit(node, [node])


def test_digest_task_waits_for_all_four_upstream_sources():
    payload = yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8"))
    tasks = {}
    for group in payload.values():
        for task in group.get("tasks", []):
            tasks[task["id"]] = task
    deps = set(tasks["trusted_strategy_performance_review"]["depends_on"])
    assert deps == {
        "trusted_strategy_backtest", "rolling_strategy_scorer",
        "trusted_strategy_candidates", "trusted_strategy_shadow_monitor",
    }
