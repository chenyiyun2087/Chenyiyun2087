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


@dataclass(frozen=True)
class DataProvenanceProof:
    """不可篡改的数据源版本、快照与防未来函数证明。"""

    release_id: str
    dataset_name: str
    snapshot_id: str
    pit_snapshot_id: str | None
    visible_date_max: int
    gate_evidence_sha256: str
    row_count: int
    data_hash: str
    verified_at: str
    certified: bool
    status: str  # "SNAPSHOT_BOUND_CERTIFIED" | "RELEASE_DECLARED_TABLE_UNPARTITIONED" | "HISTORICAL_UNVERIFIED_STUDY"
    source_table: str = ""
    snapshot_bound: bool = False
    available_at_max: str | None = None
    adjustment_anchor_date_max: int | None = None
    certification_mode: str = "CERTIFIED_LIVE_POINTER"  # "CERTIFIED_LIVE_POINTER" | "CERTIFIED_HISTORICAL_ARCHIVE" | "UNVERIFIED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    allow_degraded: bool = False
    is_historical: bool = False
    decision_time: str | None = None

    @classmethod
    def create_production(
        cls,
        trade_date: int,
        release_gate_result: Mapping[str, Any],
        config_data: Mapping[str, Any] | None = None,
        allow_degraded: bool = False,
        is_historical: bool = False,
        decision_time: str | None = None,
    ) -> "ExecutionContext":
        """创建正式生产执行上下文。强制要求 Release Gate 审核通过且所有凭证完整。"""
        eligible = bool(release_gate_result.get("eligible", release_gate_result.get("passed", False)))
        state = str(
            release_gate_result.get("state")
            or release_gate_result.get("release_state")
            or "UNKNOWN"
        ).upper()
        reason = release_gate_result.get("reason") or release_gate_result.get("detail") or "未知原因"

        # 严格区分实时生产与历史受控模式：仅当显式声明 is_historical=True 时才允许消费 SUPERSEDED 历史归档版本
        allowed_states = ("READY", "DEGRADED", "CERTIFIED_DAILY", "SUPERSEDED") if is_historical else ("READY", "DEGRADED", "CERTIFIED_DAILY")

        if not eligible or state not in allowed_states:
            raise ProductionAdmissionBlockedError(
                f"交易日 {trade_date} 数据未获得正式发布资格 (state={state}, eligible={eligible})，禁止创建正式生产上下文！"
                f"原因: {reason}"
            )

        superseded_from = str(release_gate_result.get("superseded_from_state") or "").upper()
        if state == "SUPERSEDED":
            if not is_historical:
                raise ProductionAdmissionBlockedError(
                    f"交易日 {trade_date} 发布状态为 SUPERSEDED，仅在历史归档模式 (is_historical=True) 下允许消费，禁止生产放行！"
                )
            if superseded_from == "READY":
                cert = "CERTIFIED_DAILY"
            elif superseded_from == "DEGRADED":
                if not allow_degraded:
                    raise ProductionAdmissionBlockedError(
                        f"交易日 {trade_date} 历史版本原状态为 DEGRADED 但未被显式授权 allow_degraded=True，禁止创建历史生产上下文！"
                    )
                cert = "DEGRADED_PERMITTED"
            else:
                if "READY" in reason.upper():
                    cert = "CERTIFIED_DAILY"
                elif "DEGRADED" in reason.upper():
                    if not allow_degraded:
                        raise ProductionAdmissionBlockedError(
                            f"交易日 {trade_date} 历史版本原状态为 DEGRADED 但未被显式授权 allow_degraded=True，禁止创建历史生产上下文！"
                        )
                    cert = "DEGRADED_PERMITTED"
                else:
                    raise ProductionAdmissionBlockedError(
                        f"交易日 {trade_date} 历史版本 SUPERSEDED 缺少合法的原状态继承凭据 (superseded_from_state={superseded_from})，禁止创建历史生产上下文！"
                    )
        elif state == "DEGRADED":
            if not allow_degraded:
                raise ProductionAdmissionBlockedError(
                    f"交易日 {trade_date} 发布状态为 DEGRADED 但未被显式授权 allow_degraded=True，禁止创建正式生产上下文！"
                )
            cert = "DEGRADED_PERMITTED"
        else:
            cert = "CERTIFIED_DAILY"

        rel_id = release_gate_result.get("release_id")
        ver_id = release_gate_result.get("data_version_id")
        pit_id = release_gate_result.get("pit_snapshot_id")

        # 凭据真实性严格防伪校验：禁止使用空串或占位符充当有效版本
        for field_name, field_val in (("release_id", rel_id), ("data_version_id", ver_id), ("pit_snapshot_id", pit_id)):
            if not field_val or str(field_val).strip() in ("", "None", "null", "UNKNOWN", "PLACEHOLDER", "UNVERIFIED", "missing"):
                raise ProductionAdmissionBlockedError(
                    f"交易日 {trade_date} 正式发布凭据不完整或为占位符 ({field_name}={field_val})，禁止创建正式生产上下文！"
                )

        cfg_sha = ""
        if config_data:
            cfg_sha = hashlib.sha256(json.dumps(dict(config_data), sort_keys=True, default=str).encode("utf-8")).hexdigest()

        return cls(
            mode="PRODUCTION",
            trade_date=trade_date,
            release_id=str(rel_id),
            data_version_id=str(ver_id),
            pit_snapshot_id=str(pit_id),
            certification_status=cert,
            research_only=False,
            quality_gate_result=dict(release_gate_result),
            config_sha=cfg_sha,
            allow_degraded=allow_degraded,
            is_historical=bool(is_historical),
            decision_time=decision_time,
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

        if not self.release_id or not self.data_version_id or not self.pit_snapshot_id:
            raise ProductionAdmissionBlockedError(
                f"安全拦截：缺少明确的发布凭据 (release_id={self.release_id}, data_version_id={self.data_version_id}, "
                f"pit_snapshot_id={self.pit_snapshot_id})，禁止写入正式资源 [{target_resource}]！"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
