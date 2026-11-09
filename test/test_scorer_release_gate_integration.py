"""ScoreRank 接入 ASDC Release Gate 与 ExecutionContext 的阻断与安全测试。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from cross_project.execution_context import ExecutionContext, ProductionAdmissionBlockedError
from scoreRank.cli.run_daily import main, save_scores_to_db


def test_scorer_production_mode_blocks_skip_release_gate(monkeypatch):
    """验证生产模式下传入 --skip-release-gate 报 FATAL 并以退出码 2 退出。"""
    test_args = ["run_daily.py", "--date", "20261106", "--force", "--mode", "production", "--skip-release-gate"]
    monkeypatch.setattr(sys, "argv", test_args)

    announced_msgs = []
    with patch("scoreRank.cli.run_daily._announce", side_effect=lambda msg, *args: announced_msgs.append(msg % args if args else msg)):
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 2
    assert any("正式生产模式 (--mode production) 严禁使用 --skip-release-gate" in m for m in announced_msgs)


def test_scorer_exits_when_asdc_release_blocked(monkeypatch):
    """验证门禁失败时，程序阻断并以退出码 2 退出。"""
    test_args = ["run_daily.py", "--date", "20261106", "--force"]
    monkeypatch.setattr(sys, "argv", test_args)

    mock_engine = MagicMock()
    mock_gate_inst = MagicMock()
    mock_gate_inst.check_asdc_release_gate.return_value = {
        "passed": False,
        "release_state": "BLOCKED",
        "detail": "ASDC 正式发布处于 BLOCKED",
        "data_certification": "BLOCKED",
        "release_id": "rel_blocked_01",
    }

    with patch("scoreRank.cli.run_daily.get_engine", return_value=mock_engine), \
         patch("scripts.ops.data_readiness_gate.PreScoreGate", return_value=mock_gate_inst):

        announced_msgs = []
        with patch("scoreRank.cli.run_daily._announce", side_effect=lambda msg, *args: announced_msgs.append(msg % args if args else msg)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        assert any("未通过 ASDC 质量发布门禁" in m for m in announced_msgs)
        # 确保后续选股 SQL 未被调用
        assert not mock_engine.connect.called


def test_scorer_allows_research_bypass_with_warning(monkeypatch):
    """验证在明确的 research 模式下允许 --skip-release-gate 并发出告警。"""
    test_args = ["run_daily.py", "--date", "20261106", "--force", "--mode", "research", "--skip-release-gate"]
    monkeypatch.setattr(sys, "argv", test_args)

    mock_engine = MagicMock()
    mock_gate_inst = MagicMock()
    mock_gate_inst.check_asdc_release_gate.return_value = {
        "passed": True,
        "release_state": "RELEASE_MISSING",
        "detail": "SKIPPED_FOR_RESEARCH_ONLY",
        "data_certification": "UNVERIFIED_RESEARCH_ONLY",
        "release_id": None,
    }

    with patch("scoreRank.cli.run_daily.get_engine", return_value=mock_engine), \
         patch("scripts.ops.data_readiness_gate.PreScoreGate", return_value=mock_gate_inst), \
         patch("scoreRank.cli.run_daily._query_scalar", side_effect=KeyboardInterrupt("early_exit")):

        announced_msgs = []
        try:
            with patch("scoreRank.cli.run_daily._announce", side_effect=lambda msg, *args: announced_msgs.append(msg % args if args else msg)):
                main()
        except KeyboardInterrupt:
            pass

        assert any("WARNING: 当前处于 --skip-release-gate 模式" in m for m in announced_msgs)


def test_save_scores_blocks_research_context_writing_production_table():
    """验证使用 research 执行上下文调用 save_scores_to_db 写入 score_rank_daily 会被强制拦截。"""
    df_fake = pd.DataFrame([{"stock_code": "000001.SZ", "score": 90.0, "pool_type": "TRADE"}])
    research_ctx = ExecutionContext.create_research(
        trade_date=20261106,
        release_gate_result={"release_state": "RELEASE_MISSING", "data_certification": "UNVERIFIED_RESEARCH_ONLY"},
        reason="RESEARCH_UNIT_TEST",
    )
    mock_conn = MagicMock()

    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        save_scores_to_db(
            df_fake,
            asof_date=pd.to_datetime("2026-11-06"),
            connection=mock_conn,
            execution_context=research_ctx,
        )

    assert "score_rank_daily" in str(exc_info.value)
    assert not mock_conn.execute.called


def test_save_scores_blocks_none_execution_context():
    """验证不传 ExecutionContext (None) 调用 save_scores_to_db 强制抛出异常阻断。"""
    df_fake = pd.DataFrame([{"stock_code": "000001.SZ", "score": 90.0, "pool_type": "TRADE"}])
    mock_conn = MagicMock()

    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        save_scores_to_db(
            df_fake,
            asof_date=pd.to_datetime("2026-11-06"),
            connection=mock_conn,
            execution_context=None,
        )

    assert "禁止传入 None 绕过数据准入校验" in str(exc_info.value)
    assert not mock_conn.execute.called
