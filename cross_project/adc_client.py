"""ADCClient — Chenyiyun2087 消费 AShareDataCenter 数据的统一入口。

所有查询通过 `tushare_stock.` 跨库前缀访问 ADC 数据。
禁止直接拼接 PIT 条件；统一使用本 SDK。
严格限制只读权限，防范未来函数与数据污染。
"""

from __future__ import annotations

import configparser
import os
import re
from typing import Any

import pymysql

ADC_DATABASE = "tushare_stock"


# ===========================================================================
# Contract Exceptions
# ===========================================================================


class ADCContractError(Exception):
    """跨项目数据契约基础异常。"""


class ADCConnectionError(ADCContractError):
    """连接配置缺失、未授权或连接失败。"""


class ADCReadOnlyViolationError(ADCContractError):
    """试图执行非只读 SQL 语句违背跨项目契约。"""


class ADCPITIntegrityError(ADCContractError):
    """PIT 关键状态缺失或未知，触犯防未来函数门禁。"""


class ADCDataReleaseBlockedError(ADCContractError):
    """ASDC 正式发布数据被阻断或不可用，禁止正式策略消费。"""


class ADCDataUnavailableError(ADCContractError):
    """请求的数据集、快照或表不存在。"""


# ===========================================================================
# DB Connection
# ===========================================================================


class ADCClient:
    """AShareDataCenter 跨库查询客户端。

    Usage:
        client = ADCClient()
        # 门禁核验
        release_info = client.release.check_release_gate(20261106)
        # PIT 状态查询
        status = client.pit.stock_status("000001.SZ", 20260623)
        # 特征集查询
        features = client.features.query(feature_set_version="fs_v1", trade_date=20260623)
    """

    def __init__(
        self,
        config_path: str | None = None,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        readonly_only: bool = True,
    ):
        self._config_path = config_path
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._readonly_only = readonly_only
        self._conn = None

    def _resolve_config(self) -> dict[str, Any]:
        """按优先级解析连接参数：显式参数 > 环境变量 > 备选配置文件。"""
        # 1. 显式参数优先
        host = self._host or os.getenv("ADC_MYSQL_HOST") or os.getenv("ASDC_MYSQL_HOST")
        port_raw = self._port or os.getenv("ADC_MYSQL_PORT") or os.getenv("ASDC_MYSQL_PORT")
        user = self._user or os.getenv("ADC_MYSQL_USER") or os.getenv("ASDC_MYSQL_USER")
        password = self._password or os.getenv("ADC_MYSQL_PASSWORD") or os.getenv("ASDC_MYSQL_PASSWORD")
        database = self._database or os.getenv("ADC_MYSQL_DATABASE") or ADC_DATABASE

        port = int(port_raw) if port_raw else None

        if host and user:
            final_host = host
            final_port = port or 3306
            final_user = user
            final_pwd = password or ""
            final_db = database
            resolved_path = "<explicit_parameters>"
            active_sec = "<explicit>"
            is_dedicated = bool(user != "root")
        else:
            # 2. 查找配置文件
            candidate_paths = []
            if self._config_path:
                candidate_paths.append(self._config_path)
            for env_key in ("ADC_CONFIG_PATH", "ASDC_CONFIG_PATH"):
                val = os.getenv(env_key)
                if val:
                    candidate_paths.append(val)

            # 常见相对与绝对探测路径
            base_dir = os.path.dirname(os.path.abspath(__file__))
            candidate_paths.extend([
                "/Volumes/extension/projects/AShareDataCenter/config/etl.ini",
                "/Users/chenyiyun/PycharmProjects/AShareDataCenter/config/etl.ini",
                os.path.abspath(os.path.join(base_dir, "../../AShareDataCenter/config/etl.ini")),
                os.path.abspath(os.path.join(base_dir, "../../../AShareDataCenter/config/etl.ini")),
            ])

            resolved_path = None
            for path in candidate_paths:
                if path and os.path.exists(path):
                    resolved_path = path
                    break

            if not resolved_path:
                raise ADCConnectionError(
                    f"未能解析 AShareDataCenter 数据库配置。已探测候选路径: {candidate_paths}。"
                    "请设置环境变量 ASDC_CONFIG_PATH 或通过构造函数传入连接参数。"
                )

            cfg = configparser.ConfigParser()
            cfg.read(resolved_path)

            section = "mysql_ro" if (self._readonly_only and cfg.has_section("mysql_ro")) else "mysql"
            if not cfg.has_section(section) and not cfg.has_section("mysql"):
                raise ADCConnectionError(f"配置文件 {resolved_path} 中缺少 [mysql] 或 [mysql_ro] 配置节。")

            active_sec = section if cfg.has_section(section) else "mysql"
            
            # 优先读取只读专有账号环境变量
            ro_user = os.getenv("ADC_MYSQL_RO_USER") or os.getenv("ASDC_MYSQL_RO_USER")
            ro_pwd = os.getenv("ADC_MYSQL_RO_PASSWORD") or os.getenv("ASDC_MYSQL_RO_PASSWORD")
            
            cfg_user = cfg.get(active_sec, "user", fallback=None)
            final_user = user or (ro_user if self._readonly_only and ro_user else cfg_user)
            final_pwd = password if password is not None else (ro_pwd if self._readonly_only and ro_pwd else cfg.get(active_sec, "password", fallback=""))
            final_host = host or cfg.get(active_sec, "host", fallback="localhost")
            try:
                final_port = port or cfg.getint(active_sec, "port", fallback=3306)
            except Exception:
                final_port = 3306
            final_db = database or cfg.get(active_sec, "database", fallback=ADC_DATABASE)
            is_dedicated = bool(active_sec == "mysql_ro" or ro_user)

        if not final_user:
            raise ADCConnectionError(
                f"未能解析数据库用户名，严禁隐式回退 root 账号！"
                "请在 [mysql_ro] 配置专用只读账号或设置 ADC_MYSQL_RO_USER 环境变量。"
            )

        # 严格权限审计：只读客户端严禁在生产或常规环境下使用 root 超级管理员账号
        allow_root_override = bool(os.getenv("ADC_ALLOW_ROOT_RO", "0") == "1")
        if self._readonly_only and final_user.lower() == "root" and not allow_root_override:
            raise ADCReadOnlyViolationError(
                f"安全红线阻断：跨项目只读客户端禁止复用超级管理员账号 '{final_user}' 连接数据底座！"
                "必须配置专用只读账号 (在 [mysql_ro] 配置或设置 ADC_MYSQL_RO_USER)。"
            )

        return {
            "host": final_host,
            "port": final_port,
            "user": final_user,
            "password": final_pwd,
            "database": final_db,
            "_config_source": resolved_path,
            "_section_used": active_sec,
            "_is_dedicated_ro": is_dedicated,
        }

    def _get_conn(self):
        if self._conn is None or not self._conn.open:
            conf = self._resolve_config()
            self._conn = pymysql.connect(
                host=conf["host"],
                port=conf["port"],
                user=conf["user"],
                password=conf["password"],
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                database=conf["database"],
            )
        return self._conn

    def _validate_readonly_sql(self, sql: str) -> None:
        """只读守护：严格杜绝向 ASDC 数据库进行任何写操作。"""
        if not self._readonly_only:
            return
        cleaned = re.sub(r"--.*?\n|/\*.*?\*/", "", sql, flags=re.DOTALL).strip().upper()
        allowed_prefixes = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")
        if not any(cleaned.startswith(p) for p in allowed_prefixes):
            raise ADCReadOnlyViolationError(
                f"跨项目数据访问违反只读契约，禁止执行非查询语句: {sql[:60]}..."
            )

    def _query(self, sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
        self._validate_readonly_sql(sql)
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    @property
    def database_name(self) -> str:
        return self._database or os.getenv("ADC_MYSQL_DATABASE") or ADC_DATABASE

    def _t(self, name: str) -> str:
        return f"{self.database_name}.{name}"

    def _table_ready(self, table_name: str) -> bool:
        name = table_name.replace(f"{self.database_name}.", "")
        rows = self._query(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (self.database_name, name),
        )
        return len(rows) > 0

    @property
    def release(self) -> "ADCReleaseClient":
        return ADCReleaseClient(self)

    @property
    def pit(self) -> "ADCPitClient":
        return ADCPitClient(self)

    @property
    def features(self) -> "ADCFeatureClient":
        return ADCFeatureClient(self)

    @property
    def research(self) -> "ADCResearchClient":
        return ADCResearchClient(self)

    @property
    def cost(self) -> "ADCCostClient":
        return ADCCostClient(self)

    @property
    def health(self) -> "ADCHealthClient":
        return ADCHealthClient(self)

    @property
    def data(self) -> "ADCDataClient":
        return ADCDataClient(self)


# ===========================================================================
# Release Client (ASDC 发布与质量门禁契约)
# ===========================================================================


class ADCReleaseClient:
    """ASDC 认证日版与发布状态感知客户端。"""

    def __init__(self, client: ADCClient):
        self._c = client

    def get_certified_release(self, trade_date: int) -> dict[str, Any] | None:
        """从 ASDC meta_data_release 读取指定交易日的 CERTIFIED_DAILY 发布记录。"""
        if not self._c._table_ready("meta_data_release"):
            return None
        rows = self._c._query(f"""
            SELECT release_id, release_type, trade_date, state, data_version_id,
                   pit_snapshot_id, gate_evidence_sha256, critical_status_json,
                   optional_status_json, evidence_json, published_at
            FROM {self._c._t('meta_data_release')}
            WHERE release_type = 'CERTIFIED_DAILY' AND trade_date = %s
            ORDER BY published_at DESC, created_at DESC LIMIT 1
        """, (trade_date,))
        return rows[0] if rows else None

    def get_release_datasets(self, release_id: str) -> list[dict[str, Any]]:
        """从 meta_data_release_dataset 查询指定 Release 绑定的数据集明细及状态。"""
        if not self._c._table_ready("meta_data_release_dataset"):
            return []
        return self._c._query(f"""
            SELECT release_id, dataset_name, snapshot_id, criticality, state,
                   gate_evidence_sha256, visible_date_max
            FROM {self._c._t('meta_data_release_dataset')}
            WHERE release_id = %s
        """, (release_id,))

    def check_release_gate(
        self,
        trade_date: int,
        allow_degraded: bool = False,
        required_critical_datasets: list[str] | None = None,
    ) -> dict[str, Any]:
        """核验某交易日的数据是否具备生产消费资格（细粒度数据集认证）。

        规则：
        1. 必须存在发布记录，不存在返回 RELEASE_MISSING；
        2. BLOCKED 状态一票否决；
        3. SUPERSEDED 状态禁止作为当前生产日版使用；
        4. 逐个核验 Critical 数据集：只要任一 Critical 数据集非 PASS，严禁生产放行；
        5. DEGRADED 仅当所有 Critical 数据集 PASS 且仅 Optional 数据集降级，且调用方显式声明 allow_degraded=True 时放行。

        Returns:
            dict: {
                "eligible": bool,
                "is_production_eligible": bool,
                "is_research_eligible": bool,
                "state": "READY" | "DEGRADED" | "BLOCKED" | "RELEASE_MISSING" | "SUPERSEDED",
                "release_id": str | None,
                "data_version_id": str | None,
                "pit_snapshot_id": str | None,
                "critical_datasets_ok": bool,
                "degraded_optional_datasets": list[str],
                "blocked_datasets": list[str],
                "reason": str,
            }
        """
        release = self.get_certified_release(trade_date)
        if not release:
            return {
                "eligible": False,
                "is_production_eligible": False,
                "is_research_eligible": True,  # 允许显式标记的研究任务在无 release 下隔离进行
                "state": "RELEASE_MISSING",
                "release_id": None,
                "data_version_id": None,
                "pit_snapshot_id": None,
                "critical_datasets_ok": False,
                "degraded_optional_datasets": [],
                "blocked_datasets": [],
                "reason": f"ASDC 尚未对交易日 {trade_date} 发布 CERTIFIED_DAILY 聚合版本",
            }

        state = str(release.get("state") or "UNKNOWN").upper()
        rel_id = release.get("release_id")
        ver_id = release.get("data_version_id")
        pit_id = release.get("pit_snapshot_id")

        if state == "BLOCKED":
            return {
                "eligible": False,
                "is_production_eligible": False,
                "is_research_eligible": True,
                "state": "BLOCKED",
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": False,
                "degraded_optional_datasets": [],
                "blocked_datasets": ["ALL_BLOCKED_BY_GATE"],
                "reason": f"ASDC 发布已被质量门禁阻断 (BLOCKED)",
            }

        if state == "SUPERSEDED":
            return {
                "eligible": False,
                "is_production_eligible": False,
                "is_research_eligible": False,
                "state": "SUPERSEDED",
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": False,
                "degraded_optional_datasets": [],
                "blocked_datasets": [],
                "reason": f"该发布版本已被更新版本替代 (SUPERSEDED)，禁止作为当前生产日版使用",
            }

        # 深入查询数据集状态
        datasets = self.get_release_datasets(rel_id) if rel_id else []
        critical_failed = []
        optional_degraded = []
        blocked_datasets = []

        # 默认关键数据集
        target_critical = set(required_critical_datasets or ["dwd_daily", "dwd_stock_daily_standard", "dim_stock"])

        for ds in datasets:
            ds_name = ds.get("dataset_name", "")
            ds_crit = str(ds.get("criticality") or "").upper()
            ds_state = str(ds.get("state") or "").upper()

            if ds_state == "BLOCKED":
                blocked_datasets.append(ds_name)
                if ds_crit == "CRITICAL" or ds_name in target_critical:
                    critical_failed.append(f"{ds_name}(BLOCKED)")
            elif ds_state == "DEGRADED":
                if ds_crit == "CRITICAL" or ds_name in target_critical:
                    critical_failed.append(f"{ds_name}(DEGRADED)")
                else:
                    optional_degraded.append(ds_name)

        if critical_failed:
            return {
                "eligible": False,
                "is_production_eligible": False,
                "is_research_eligible": True,
                "state": "DEGRADED" if state == "DEGRADED" else "BLOCKED",
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": False,
                "degraded_optional_datasets": optional_degraded,
                "blocked_datasets": blocked_datasets,
                "reason": f"必需关键数据集未通过验证: {critical_failed}，禁止生产放行",
            }

        if state == "READY":
            return {
                "eligible": True,
                "is_production_eligible": True,
                "is_research_eligible": True,
                "state": "READY",
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": True,
                "degraded_optional_datasets": optional_degraded,
                "blocked_datasets": [],
                "reason": "正式日版认证通过",
            }

        if state == "DEGRADED":
            eligible = allow_degraded and (len(critical_failed) == 0)
            return {
                "eligible": eligible,
                "is_production_eligible": eligible,
                "is_research_eligible": True,
                "state": "DEGRADED",
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": True,
                "degraded_optional_datasets": optional_degraded,
                "blocked_datasets": blocked_datasets,
                "reason": (
                    "关键数据集已通过，但可选数据集降级"
                    if eligible
                    else "数据处于降级状态 (DEGRADED)，需显式配置 allow_degraded=True 消费"
                ),
            }

        return {
            "eligible": False,
            "is_production_eligible": False,
            "is_research_eligible": False,
            "state": state,
            "release_id": rel_id,
            "data_version_id": ver_id,
            "pit_snapshot_id": pit_id,
            "critical_datasets_ok": False,
            "degraded_optional_datasets": optional_degraded,
            "blocked_datasets": blocked_datasets,
            "reason": f"未知的 Release 状态 ({state})",
        }

    def require_certified_release(
        self,
        trade_date: int,
        allow_degraded: bool = False,
        required_critical_datasets: list[str] | None = None,
    ) -> dict[str, Any]:
        """强制要求正式发布就绪，否则阻断并抛出异常。"""
        gate = self.check_release_gate(
            trade_date,
            allow_degraded=allow_degraded,
            required_critical_datasets=required_critical_datasets,
        )
        if not gate["eligible"]:
            raise ADCDataReleaseBlockedError(
                f"交易日 {trade_date} 数据未通过 ASDC 质量门禁: {gate['reason']} (state={gate['state']})"
            )
        return gate


# ===========================================================================
# PIT Client
# ===========================================================================


class ADCPitClient:
    def __init__(self, client: ADCClient):
        self._c = client

    def _t(self, name: str) -> str:
        return self._c._t(name)

    def stock_status(
        self,
        ts_code: str,
        trade_date: int,
        strict: bool = False,
    ) -> dict[str, Any]:
        """查询 PIT 股票状态。回退链: dim_stock_status_scd → dim_stock。

        严格防未来函数与风险规则：
        1. 当 dim_stock_status_scd 可用时，返回精准状态。
        2. 当回退到 dim_stock 时，因缺少历史 ST 状态，严禁默认 is_st=False，必须返回 is_st=None 并标记 is_st_unknown=True。
        3. strict=True 时，若关键 ST 状态未知，直接抛出 ADCPITIntegrityError。
        """
        # SCD 表
        if self._c._table_ready("dim_stock_status_scd"):
            rows = self._c._query(f"""
                SELECT status FROM {self._t('dim_stock_status_scd')}
                WHERE ts_code = %s AND effective_date <= %s
                  AND (expire_date IS NULL OR %s < expire_date)
                ORDER BY effective_date DESC LIMIT 1
            """, (ts_code, trade_date, trade_date))
            if rows:
                status = rows[0]["status"]
                is_st = status in ("st", "st_star", "risk_warning")
                return {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "status": status,
                    "is_listed": status not in ("delisted", "pre_listed"),
                    "is_st": is_st,
                    "is_st_unknown": False,
                    "quality_status": "CERTIFIED_PIT",
                    "source": "dim_stock_status_scd",
                }

        # 回退: dim_stock (仅能确定上市/退市时间范围，不可推断 ST)
        rows = self._c._query(f"""
            SELECT list_date, delist_date FROM {self._t('dim_stock')}
            WHERE ts_code = %s LIMIT 1
        """, (ts_code,))
        if rows:
            r = rows[0]
            listed = r["list_date"] <= trade_date
            delisted = bool(r.get("delist_date") and r["delist_date"] < trade_date)
            status = "delisted" if delisted else "listed_st_unknown" if listed else "pre_listed"

            if strict:
                raise ADCPITIntegrityError(
                    f"股票 {ts_code} 在交易日 {trade_date} 缺少 dim_stock_status_scd，无法验证 ST 状态"
                )

            return {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "status": status,
                "is_listed": listed and not delisted,
                "is_st": None,  # 禁止默认 False！
                "is_st_unknown": True,
                "quality_status": "DEGRADED_UNKNOWN_ST",
                "source": "dim_stock_fallback",
            }

        if strict:
            raise ADCPITIntegrityError(
                f"股票 {ts_code} 在交易日 {trade_date} 无法匹配任何证券主数据记录"
            )

        return {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "status": "unknown",
            "is_listed": False,
            "is_st": None,
            "is_st_unknown": True,
            "quality_status": "BLOCKED_NOT_FOUND",
            "source": "none",
        }

    def industry(self, ts_code: str, trade_date: int, system: str = "SW2021") -> dict[str, Any]:
        """查询 PIT 行业归属。缺失返回 UNKNOWN。"""
        if not self._c._table_ready("dwd_stock_industry_scd"):
            return {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "industry_name": "UNKNOWN",
                "quality_status": "DEGRADED_TABLE_MISSING",
                "source": "fallback_unknown",
            }
        rows = self._c._query(f"""
            SELECT industry_name, industry_code FROM {self._t('dwd_stock_industry_scd')}
            WHERE ts_code = %s AND industry_system = %s
              AND effective_date <= %s AND (expire_date IS NULL OR %s < expire_date)
            ORDER BY effective_date DESC LIMIT 1
        """, (ts_code, system, trade_date, trade_date))
        if rows:
            return {
                "ts_code": ts_code,
                "trade_date": trade_date,
                **rows[0],
                "quality_status": "CERTIFIED_PIT",
                "source": "dwd_stock_industry_scd",
            }
        return {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "industry_name": "UNKNOWN",
            "quality_status": "DEGRADED_ROW_MISSING",
            "source": "fallback_unknown",
        }

    def corporate_actions(self, ts_code: str, trade_date: int) -> list[dict[str, Any]]:
        """查询 PIT 公司行为。"""
        if not self._c._table_ready("dwd_corporate_action_event"):
            return []
        return self._c._query(f"""
            SELECT event_type, ann_date, ex_date, cash_per_share, stock_per_share, split_ratio
            FROM {self._t('dwd_corporate_action_event')}
            WHERE ts_code = %s AND effective_date <= %s
            ORDER BY effective_date DESC
        """, (ts_code, trade_date))


# ===========================================================================
# Feature Client
# ===========================================================================


class ADCFeatureClient:
    def __init__(self, client: ADCClient):
        self._c = client

    def _t(self, name: str) -> str:
        return f"{ADC_DATABASE}.{name}"

    def query(self, feature_set_version: str, trade_date: int,
              ts_codes: list[str] | None = None) -> list[dict[str, Any]]:
        """查询指定 Feature Set 在某交易日的特征快照。"""
        if not self._c._table_ready("dws_feature_snapshot_di"):
            return []
        sql = f"""
            SELECT ts_code, feature_id, feature_value, missing_flag
            FROM {self._t('dws_feature_snapshot_di')}
            WHERE feature_set_version = %s AND trade_date = %s
        """
        params: list[Any] = [feature_set_version, trade_date]
        if ts_codes:
            placeholders = ",".join(["%s"] * len(ts_codes))
            sql += f" AND ts_code IN ({placeholders})"
            params.extend(ts_codes)
        return self._c._query(sql, tuple(params))

    def get_feature_definition(self, feature_id: str) -> dict[str, Any] | None:
        """获取特征定义。"""
        rows = self._c._query(f"""
            SELECT * FROM {self._t('meta_feature_registry')} WHERE feature_id = %s
        """, (feature_id,))
        return rows[0] if rows else None


# ===========================================================================
# Research Client
# ===========================================================================


class ADCResearchClient:
    def __init__(self, client: ADCClient):
        self._c = client

    def _t(self, name: str) -> str:
        return f"{ADC_DATABASE}.{name}"

    def get_selection_digest(self, trade_date: int) -> list[dict[str, Any]]:
        """获取 ADC 某日选股摘要。"""
        if not self._c._table_ready("ads_selection_digest_history_di"):
            return []
        return self._c._query(f"""
            SELECT ts_code, stock_name, industry, trend_label, main_score,
                   predicted_return_5d, risk_level, source_tags
            FROM {self._t('ads_selection_digest_history_di')}
            WHERE trade_date = %s
        """, (trade_date,))

    def get_backtest_summary(self, run_id: str) -> dict[str, Any] | None:
        """获取回测摘要。"""
        rows = self._c._query(f"""
            SELECT * FROM {self._t('ads_strategy_backtest_summary_di')}
            WHERE run_id = %s LIMIT 1
        """, (run_id,))
        return rows[0] if rows else None


# ===========================================================================
# Cost Client
# ===========================================================================


class ADCCostClient:
    def __init__(self, client: ADCClient):
        self._c = client

    def _t(self, name: str) -> str:
        return f"{ADC_DATABASE}.{name}"

    def estimate(self, ts_code: str, trade_date: int, notional: float) -> dict[str, Any]:
        """估算执行成本。表不可用时回退到公式。"""
        if self._c._table_ready("dws_execution_cost_curve_di"):
            rows = self._c._query(f"""
                SELECT estimated_spread_bps, estimated_impact_bps,
                       estimated_slippage_bps, estimated_fill_ratio
                FROM {self._t('dws_execution_cost_curve_di')}
                WHERE ts_code = %s AND trade_date = %s
                ORDER BY adv_20d DESC LIMIT 1
            """, (ts_code, trade_date))
            if rows:
                r = rows[0]
                total = (float(r.get("estimated_spread_bps", 0) or 0) +
                         float(r.get("estimated_impact_bps", 0) or 0) +
                         float(r.get("estimated_slippage_bps", 0) or 5))
                return {"total_cost_bps": round(total, 2), "source": "dws_execution_cost_curve_di", **r}
        return {"total_cost_bps": 6.5, "estimated_impact_bps": 0.5,
                "estimated_slippage_bps": 5.0, "estimated_spread_bps": 1.0,
                "estimated_fill_ratio": 1.0, "source": "fallback"}


# ===========================================================================
# Health Client
# ===========================================================================


class ADCHealthClient:
    def __init__(self, client: ADCClient):
        self._c = client

    def _t(self, name: str) -> str:
        return f"{ADC_DATABASE}.{name}"

    def check(self, strategy_id: str, trade_date: int | None = None) -> dict[str, Any]:
        """检查策略健康度。"""
        from datetime import date
        td = trade_date or int(date.today().strftime("%Y%m%d"))
        layers = {}
        for layer, table in [("data", "meta_data_health_metric_di"),
                              ("model", "meta_model_health_metric_di")]:
            if self._c._table_ready(table):
                rows = self._c._query(f"""
                    SELECT severity, COUNT(*) as cnt FROM {self._t(table)}
                    WHERE trade_date = %s AND strategy_id = %s
                    GROUP BY severity
                """, (td, strategy_id))
                layers[layer] = {r["severity"]: r["cnt"] for r in rows}
            else:
                layers[layer] = {}
        blocked = any(layers.get(l, {}).get("critical", 0) > 0 for l in layers)
        return {"blocked": blocked, "layers": layers, "trade_date": td}


# ===========================================================================
# Data Client (行情/基本面)
# ===========================================================================


class ADCDataClient:
    def __init__(self, client: ADCClient):
        self._c = client

    def _t(self, name: str) -> str:
        return self._c._t(name)

    def get_daily_bars(self, ts_code: str, start_date: int, end_date: int) -> list[dict[str, Any]]:
        """获取前复权日线（单股向后兼容接口）。"""
        return self._c._query(f"""
            SELECT trade_date, ts_code, qfq_close, qfq_ret_1, qfq_ret_5, qfq_ret_20, qfq_ret_60
            FROM {self._t('dws_price_adj_daily')}
            WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (ts_code, start_date, end_date))

    def get_daily_bars_batch(
        self,
        symbols: list[str],
        start_date: int,
        end_date: int | None = None,
        adj_type: str = "qfq",
        chunk_size: int = 500,
    ) -> Any:
        """批量获取股票日线行情，标准输出列完全对齐 ScoreRank 和回测引擎。

        返回 DataFrame 包含：
        symbol, ts_code, trade_date, open, high, low, close, volume, amount
        """
        import pandas as pd
        if not symbols:
            return pd.DataFrame(columns=[
                "symbol", "ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"
            ])

        adj_lower = (adj_type or "").strip().lower()
        if adj_lower == "raw":
            table_name = "dwd_daily"
            col_map = "open AS open, high AS high, low AS low, close AS close, vol AS volume, amount"
        else:
            table_name = "dwd_stock_daily_standard"
            col_map = "adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close, vol AS volume, amount"

        def to_ts_code(s: str) -> str:
            s = str(s).strip()
            if "." in s:
                return s
            if s.startswith("6") or s.startswith("9"):
                return f"{s}.SH"
            elif s.startswith("0") or s.startswith("3"):
                return f"{s}.SZ"
            elif s.startswith("4") or s.startswith("8"):
                return f"{s}.BJ"
            return s

        ts_codes = [to_ts_code(s) for s in symbols]
        ts_to_sym = {tc: tc.split(".")[0] for tc in ts_codes}

        end_clause = "AND trade_date <= %s" if end_date else ""
        results: list[dict[str, Any]] = []

        for i in range(0, len(ts_codes), chunk_size):
            chunk = ts_codes[i : i + chunk_size]
            placeholders = ",".join(["%s"] * len(chunk))
            sql = f"""
                SELECT ts_code, trade_date, {col_map}
                FROM {self._t(table_name)}
                WHERE ts_code IN ({placeholders}) AND trade_date >= %s {end_clause}
                ORDER BY trade_date, ts_code
            """
            params: list[Any] = list(chunk) + [start_date]
            if end_date:
                params.append(end_date)
            rows = self._c._query(sql, tuple(params))
            results.extend(rows)

        if not results:
            return pd.DataFrame(columns=[
                "symbol", "ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"
            ])

        df = pd.DataFrame(results)
        df["symbol"] = df["ts_code"].map(ts_to_sym)
        cols = ["symbol", "ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
        return df[[c for c in cols if c in df.columns]]

    def get_latest_data_date(self, symbols: list[str] | None = None) -> int | None:
        """获取行情数据的最新日期。"""
        if symbols:
            def to_ts_code(s: str) -> str:
                s = str(s).strip()
                if "." in s:
                    return s
                if s.startswith("6") or s.startswith("9"):
                    return f"{s}.SH"
                elif s.startswith("0") or s.startswith("3"):
                    return f"{s}.SZ"
                elif s.startswith("4") or s.startswith("8"):
                    return f"{s}.BJ"
                return s

            ts_codes = [to_ts_code(s) for s in symbols]
            placeholders = ",".join(["%s"] * len(ts_codes))
            sql = f"SELECT MAX(trade_date) AS max_d FROM {self._t('dwd_stock_daily_standard')} WHERE ts_code IN ({placeholders})"
            rows = self._c._query(sql, tuple(ts_codes))
        else:
            sql = f"SELECT MAX(trade_date) AS max_d FROM {self._t('dwd_stock_daily_standard')}"
            rows = self._c._query(sql)

        if rows and rows[0].get("max_d"):
            return int(rows[0]["max_d"])
        return None

    def get_index_daily(self, ts_code: str, start_date: int, end_date: int) -> list[dict[str, Any]]:
        """获取指数日线。"""
        return self._c._query(f"""
            SELECT trade_date, close, pct_chg
            FROM {self._t('ods_index_daily')}
            WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (ts_code, start_date, end_date))

    def get_trade_calendar(self, start_date: int, end_date: int) -> list[int]:
        """获取交易日历。"""
        rows = self._c._query(f"""
            SELECT cal_date FROM {self._t('dim_trade_cal')}
            WHERE exchange = 'SSE' AND is_open = 1
              AND cal_date BETWEEN %s AND %s
            ORDER BY cal_date
        """, (start_date, end_date))
        return [r["cal_date"] for r in rows]
