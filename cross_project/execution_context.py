"""跨项目统一运行与数据认证上下文 (ExecutionContext)。

保证数据版本、PIT 快照、Release 状态在整个选股、回测、影子账本和订单草案中端到端传递并强校验。
正式模式严格 Fail-Closed，严禁研究模式产物伪装或写入正式生产资产。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping


class ExecutionContextError(Exception):
    """执行上下文基础异常。"""


class ProductionAdmissionBlockedError(ExecutionContextError):
    """正式生产准入校验失败：未认证或降级未授权数据严禁写入生产目标。"""


class IncompatibleContextError(ExecutionContextError):
    """执行上下文与目标操作不兼容。"""


def _get_current_code_sha() -> str:
    """获取当前代码仓库 Git HEAD SHA。"""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN_GIT_SHA"


@dataclass(frozen=True)
class ExecutionContext:
    """不可篡改的策略与数据准入执行上下文。"""

    mode: str  # "PRODUCTION" | "RESEARCH"
    trade_date: int  # YYYYMMDD
    release_id: str | None
    data_version_id: str | None
    pit_snapshot_id: str | None
    certification_status: str  # "CERTIFIED_DAILY" | "DEGRADED_PERMITTED" | "UNVERIFIED_RESEARCH_ONLY" | "BLOCKED" | "RELEASE_MISSING"
    research_only: bool
    quality_gate_result: dict[str, Any] = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}")
    code_sha: str = field(default_factory=_get_current_code_sha)
    config_sha: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def create_production(
        cls,
        trade_date: int,
        release_gate_result: Mapping[str, Any],
        config_data: Mapping[str, Any] | None = None,
    ) -> "ExecutionContext":
        """创建正式生产执行上下文。强制要求 Release Gate 审核通过。"""
        eligible = release_gate_result.get("eligible", False)
        state = str(release_gate_result.get("state") or "UNKNOWN").upper()

        if not eligible or state not in ("READY", "DEGRADED"):
            raise ProductionAdmissionBlockedError(
                f"交易日 {trade_date} 数据未获得正式发布资格 (state={state})，禁止创建正式生产上下文！"
                f"原因: {release_gate_result.get('reason')}"
            )

        cert = "CERTIFIED_DAILY" if state == "READY" else "DEGRADED_PERMITTED"

        cfg_sha = ""
        if config_data:
            cfg_sha = hashlib.sha256(json.dumps(dict(config_data), sort_keys=True, default=str).encode("utf-8")).hexdigest()

        return cls(
            mode="PRODUCTION",
            trade_date=trade_date,
            release_id=release_gate_result.get("release_id"),
            data_version_id=release_gate_result.get("data_version_id"),
            pit_snapshot_id=release_gate_result.get("pit_snapshot_id"),
            certification_status=cert,
            research_only=False,
            quality_gate_result=dict(release_gate_result),
            config_sha=cfg_sha,
        )

    @classmethod
    def create_research(
        cls,
        trade_date: int,
        release_gate_result: Mapping[str, Any] | None = None,
        reason: str = "OFFLINE_STUDY",
    ) -> "ExecutionContext":
        """创建隔离的研究/诊断上下文。明确打上 research_only=True 标记。"""
        gate_res = dict(release_gate_result or {})
        state = str(gate_res.get("state") or "UNVERIFIED").upper()

        return cls(
            mode="RESEARCH",
            trade_date=trade_date,
            release_id=gate_res.get("release_id"),
            data_version_id=gate_res.get("data_version_id"),
            pit_snapshot_id=gate_res.get("pit_snapshot_id"),
            certification_status="UNVERIFIED_RESEARCH_ONLY",
            research_only=True,
            quality_gate_result={"research_reason": reason, **gate_res},
        )

    def validate_for_production_write(self, target_resource: str) -> None:
        """终审写入门禁：在正式候选、订单草案、影子账本写入前做最终闭环核验。"""
        if self.research_only:
            raise ProductionAdmissionBlockedError(
                f"安全拦截：当前处于 RESEARCH 模式 (research_only=True)，严禁向正式生产资源 [{target_resource}] 写入数据！"
            )

        if self.mode != "PRODUCTION":
            raise ProductionAdmissionBlockedError(
                f"安全拦截：当前模式为 [{self.mode}]，必须为 PRODUCTION 才能向正式资源 [{target_resource}] 写入！"
            )

        if self.certification_status not in ("CERTIFIED_DAILY", "DEGRADED_PERMITTED"):
            raise ProductionAdmissionBlockedError(
                f"安全拦截：数据认证状态 [{self.certification_status}] 不合规，严禁写入正式资源 [{target_resource}]！"
            )

        if not self.release_id or not self.data_version_id:
            raise ProductionAdmissionBlockedError(
                f"安全拦截：缺少明确的 release_id 或 data_version_id，禁止写入正式资源 [{target_resource}]！"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
