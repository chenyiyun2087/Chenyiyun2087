"""ADCClient — Chenyiyun2087 消费 AShareDataCenter 数据的统一入口。

所有查询通过 `tushare_stock.` 跨库前缀访问 ADC 数据。
禁止直接拼接 PIT 条件；统一使用本 SDK。
严格限制只读权限，防范未来函数与数据污染。
"""

from __future__ import annotations

import configparser
import logging
import os
import re
from typing import Any

import pymysql

logger = logging.getLogger(__name__)

ADC_DATABASE = "tushare_stock"
CANONICAL_RELEASE_POINTER_NAME = "CERTIFIED_DAILY"
CANONICAL_CRITICAL_PRODUCTS = (
    "trade_calendar",
    "security_lifecycle_daily",
    "equity_daily_bar_raw",
    "equity_adjustment_factor",
    "corporate_action_event",
    "daily_basic",
)


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

    def verify_server_side_readonly(self) -> dict[str, Any]:
        """检查数据库服务端实际权限与拒写行为。

        合规准则：
        1. 必须使用非 root 只读账号；
        2. SHOW GRANTS 或权限元数据不得包含全局写权限（ALL PRIVILEGES, GRANT OPTION, SUPER）或针对目标库的写操作（INSERT, UPDATE, DELETE, CREATE, DROP, ALTER）；
        3. 执行严格探测写操作，若无异常顺利执行，证明账号具有物理写权限，必须判定为失败抛出 ADCReadOnlyViolationError；
        4. 仅明确的服务端权限拒绝错误码（1142: ER_TABLEACCESS_DENIED_ERROR, 1044: ER_DBACCESS_DENIED_ERROR, 1227: ER_SPECIFIC_ACCESS_DENIED_ERROR, 1290: ER_OPTION_PREVENTS_STATEMENT）可作为拒写合规证据；
        5. 任何语法错误 (1064)、表不存在 (1146)、连接异常 (2002/2003/2006/2013) 或未知异常，一律判定为失败并抛出异常，绝不放行！
        """
        if not self._readonly_only:
            return {"readonly_verified": False, "reason": "READONLY_MODE_DISABLED"}

        config = self._resolve_config()
        user = config.get("user", "")
        if user.lower() == "root":
            raise ADCReadOnlyViolationError(
                "安全红线阻断：跨项目只读客户端禁止复用超级管理员账号 'root'！"
            )

        conn = self._get_conn()
        cur = conn.cursor()

        # 1. 检查授权权限 (SHOW GRANTS)
        try:
            cur.execute("SHOW GRANTS")
            rows = cur.fetchall()
            for r in rows:
                val = str(r.get(list(r.keys())[0], "") if isinstance(r, dict) else r[0]).upper()
                # 检查全局超级权限
                if "ALL PRIVILEGES ON *.*" in val or "WITH GRANT OPTION" in val or "SUPER" in val:
                    raise ADCReadOnlyViolationError(
                        f"安全红线阻断：当前连接拥有全局超级管理员权限 (ALL PRIVILEGES)，账号 [{user}] 违反只读隔离！"
                    )
                # 检查针对 *.* 或目标数据库的写权限
                for write_perm in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"):
                    if f"{write_perm} ON *.*" in val or f"{write_perm} ON `{self.database_name}`.*" in val or f"{write_perm} ON {self.database_name}.*" in val:
                        raise ADCReadOnlyViolationError(
                            f"安全红线阻断：只读账号 [{user}] 被授予了写操作权限 [{write_perm}]，违反只读隔离！"
                        )
        except ADCReadOnlyViolationError:
            raise
        except pymysql.MySQLError as e:
            err_code = e.args[0] if e.args else None
            if err_code in (2002, 2003, 2006, 2013):
                raise ADCConnectionError(f"数据库连接异常，无法完成只读权限审计: {e}") from e
            logger.info("SHOW GRANTS 执行受限 (%s)，通过服务端主动拒写探测继续核验证明", e)

        # 2. 真实服务端拒写探测 (Write probe)
        # 尝试执行无害写探测：由于是只读账号，预期必须被服务端权限直接拒绝 (Access denied)
        probe_sql = f"UPDATE {self._t('meta_data_release')} SET state = state WHERE 1=0"
        try:
            cur.execute(probe_sql)
            # 若执行到这里未抛出异常，说明写语句被服务端执行了，证明具有写权限！
            raise ADCReadOnlyViolationError(
                f"安全红线阻断：服务端成功执行了写探测语句 [{probe_sql}]，证明账号 [{user}] 具有物理写权限，只读验证失败！"
            )
        except ADCReadOnlyViolationError:
            raise
        except pymysql.MySQLError as err:
            err_code = err.args[0] if err.args else None
            # 仅允许权威的账号级权限拒绝错误码 (1142: 表级拒绝, 1044: 库级拒绝, 1227: 特定权限缺失)
            # 明确排除 1290 (ER_OPTION_PREVENTS_STATEMENT: 服务器处于 --read-only 模式)，因为全局只读不能证明账号受最小权限约束
            PERMITTED_RO_ERROR_CODES = (1142, 1044, 1227)
            if err_code in PERMITTED_RO_ERROR_CODES:
                logger.info("服务端拒写行为校验通过: 捕获预期权限拒绝错误 (code=%s, msg=%s)", err_code, err)
                return {
                    "readonly_verified": True,
                    "user": user,
                    "target_database": self.database_name,
                    "rejection_code": err_code,
                    "verified_method": "SERVER_SIDE_ACCESS_DENIED_PROBE",
                }
            elif err_code == 1290:
                raise ADCReadOnlyViolationError(
                    f"安全红线阻断：数据库实例处于全局只读模式 (code=1290: {err})，不能单独证明账号 [{user}] 具有账号级最小只读权限！"
                )
            elif err_code in (2002, 2003, 2006, 2013):
                raise ADCConnectionError(f"只读验证期间数据库连接中断: {err}") from err
            elif err_code == 1146:
                raise ADCReadOnlyViolationError(f"只读探测目标表不存在 (code=1146): {err}，无法证明服务端权限") from err
            elif err_code == 1064:
                raise ADCReadOnlyViolationError(f"只读探测 SQL 语法错误 (code=1064): {err}，无法证明服务端权限") from err
            else:
                raise ADCReadOnlyViolationError(
                    f"安全红线阻断：服务端返回非预期的异常错误 (code={err_code}: {err})，不能证明账号无写权限！"
                ) from err
        except Exception as e:
            raise ADCReadOnlyViolationError(f"服务端只读验证失败，发生未知异常: {e}") from e

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

    def get_active_release_pointer(self, pointer_name: str = CANONICAL_RELEASE_POINTER_NAME) -> dict[str, Any] | None:
        """从 meta_data_release_pointer 获取正式发布的活动指针。严格精确匹配规范指针名称。"""
        if not self._c._table_ready("meta_data_release_pointer"):
            return None
        p_upper = pointer_name.strip().upper()
        p_lower = pointer_name.strip().lower()
        rows = self._c._query(f"""
            SELECT pointer_name, release_id, pointer_version, updated_at
            FROM {self._c._t('meta_data_release_pointer')}
            WHERE pointer_name = %s OR pointer_name = %s
            ORDER BY updated_at DESC LIMIT 1
        """, (p_upper, p_lower))
        return rows[0] if rows else None

    def get_certified_release(self, trade_date: int, pointer_name: str = CANONICAL_RELEASE_POINTER_NAME) -> dict[str, Any] | None:
        """从 ASDC meta_data_release 读取指定交易日的 CERTIFIED_DAILY 发布记录，并结合发布指针验证。"""
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
        if not rows:
            return None
        release = dict(rows[0])
        pointer = self.get_active_release_pointer(pointer_name)
        if pointer:
            release["active_pointer"] = pointer
            release["pointer_matched"] = (pointer.get("release_id") == release.get("release_id"))
            release["pointer_status"] = "MATCHED" if release["pointer_matched"] else "POINTER_MISMATCH"
        else:
            release["active_pointer"] = None
            release["pointer_matched"] = False
            release["pointer_status"] = "POINTER_MISSING"
        return release

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
        """核验某交易日的数据是否具备生产消费资格（细粒度数据集认证与指针闭环）。

        规则：
        1. 必须存在发布记录，不存在返回 RELEASE_MISSING；
        2. BLOCKED 状态一票否决；
        3. SUPERSEDED 状态禁止作为当前生产日版使用；
        4. 验证发布指针一致性：若存在活动指针但与当前 release_id 冲突，Fail-Closed 阻断；
        5. 验证 Critical 数据集完整性：若数据集列表为空，或缺少任一必需 Critical 数据集，Fail-Closed 阻断；
        6. 逐个核验 Critical 数据集：只要任一 Critical 数据集非 PASS/READY，严禁生产放行；
        7. DEGRADED 仅当所有 Critical 数据集完整且 PASS，仅 Optional 数据集降级，且调用方显式声明 allow_degraded=True 时放行。
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

        # 顶层状态严格白名单校验：仅放行 READY 或受控的 DEGRADED；
        # 其余所有状态（BLOCKED、SUPERSEDED、BUILDING、PENDING、空状态或未知状态）一律 Fail-Closed 严格阻断
        if state not in ("READY", "DEGRADED"):
            return {
                "eligible": False,
                "is_production_eligible": False,
                "is_research_eligible": (state in ("BLOCKED", "BUILDING", "PENDING")),
                "state": state,
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": False,
                "degraded_optional_datasets": [],
                "blocked_datasets": ["TOP_LEVEL_STATE_NOT_PERMITTED"],
                "reason": f"ASDC 发布版本顶层状态 [{state}] 未通过白名单校验（仅放行 READY 或 DEGRADED），Fail-Closed 严格阻断！",
            }

        # 验证指针存在性与一致性
        pointer = release.get("active_pointer")
        if not pointer:
            return {
                "eligible": False,
                "is_production_eligible": False,
                "is_research_eligible": False,
                "state": "BLOCKED",
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": False,
                "degraded_optional_datasets": [],
                "blocked_datasets": ["POINTER_MISSING"],
                "reason": (
                    f"正式发布缺少有效的活动指针 (POINTER_MISSING)，未绑定规范指针 "
                    f"{CANONICAL_RELEASE_POINTER_NAME}，Fail-Closed 阻断！"
                ),
            }

        if not release.get("pointer_matched"):
            return {
                "eligible": False,
                "is_production_eligible": False,
                "is_research_eligible": False,
                "state": "BLOCKED",
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": False,
                "degraded_optional_datasets": [],
                "blocked_datasets": ["POINTER_MISMATCH"],
                "reason": (
                    f"发布指针冲突 (POINTER_MISMATCH): active_pointer 指向 [{pointer.get('release_id')}]，"
                    f"与交易日 {trade_date} 当前发布 [{rel_id}] 不一致，Fail-Closed 阻断！"
                ),
            }

        # 深入查询数据集状态
        datasets = self.get_release_datasets(rel_id) if rel_id else []
        critical_failed = []
        optional_degraded = []
        blocked_datasets = []

        found_datasets = {ds.get("dataset_name", ""): ds for ds in datasets}

        # 核心防空检查：若数据集列表为空，绝不允许放行
        if not datasets:
            return {
                "eligible": False,
                "is_production_eligible": False,
                "is_research_eligible": False,
                "state": "BLOCKED",
                "release_id": rel_id,
                "data_version_id": ver_id,
                "pit_snapshot_id": pit_id,
                "critical_datasets_ok": False,
                "degraded_optional_datasets": [],
                "blocked_datasets": ["ALL_DATASETS_MISSING"],
                "reason": f"发布版本 [{rel_id}] 未绑定任何有效数据集 (datasets_count=0)，Critical 数据集缺失，Fail-Closed 阻断！",
            }

        # 正式认证强制依据 ASDC 六大权威数据产品目录验证，外部参数只能追加要求，绝不能缩小必需底线
        target_critical = set(CANONICAL_CRITICAL_PRODUCTS)
        if required_critical_datasets:
            target_critical.update(required_critical_datasets)

        missing_critical = target_critical - set(found_datasets.keys())
        for m in sorted(missing_critical):
            critical_failed.append(f"{m}(MISSING)")

        for ds in datasets:
            ds_name = ds.get("dataset_name", "")
            ds_crit = str(ds.get("criticality") or "").upper()
            ds_state = str(ds.get("state") or "").upper()
            ds_snap = ds.get("snapshot_id")

            is_critical = (ds_crit == "CRITICAL" or ds_name in target_critical)
            if is_critical:
                if "snapshot_id" in ds and (not ds_snap or str(ds_snap).strip() in ("", "None", "null", "missing", "MISSING")):
                    critical_failed.append(f"{ds_name}(SNAPSHOT_MISSING)")
                elif ds_state not in ("PASS", "READY", "OK"):
                    critical_failed.append(f"{ds_name}({ds_state or 'NOT_PASS'})")
            else:
                if ds_state == "DEGRADED":
                    optional_degraded.append(ds_name)
                elif ds_state in ("BLOCKED", "FAIL", "FAILED"):
                    blocked_datasets.append(ds_name)

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
        release_id: str | None = None,
        pit_snapshot_id: str | None = None,
        execution_context: Any = None,
    ) -> Any:
        """批量获取股票日线行情，标准输出列完全对齐 ScoreRank 和回测引擎。

        当传入 release_id 时，严格校验版本可见日期上限 visible_date_max（防未来函数），
        并将不可篡改的数据证明 DataProvenanceProof 持久绑定至 DataFrame.attrs['provenance_proof']。
        生产模式下未绑定认证 release_id 严禁放行。
        """
        import hashlib
        from datetime import datetime
        import pandas as pd
        from cross_project.execution_context import DataProvenanceProof, ProductionAdmissionBlockedError

        # 1. 严格判定执行模式与上下文核查
        ctx_mode = getattr(execution_context, "mode", None)
        is_explicit_research = bool(
            execution_context
            and str(ctx_mode).upper() == "RESEARCH"
            and getattr(execution_context, "research_only", False)
        )
        is_production = bool(
            (execution_context and str(ctx_mode).upper() in ("PRODUCTION", "SHADOW"))
            or (release_id and not is_explicit_research)
        )

        if is_production:
            if execution_context is None:
                raise ProductionAdmissionBlockedError(
                    "安全红线阻断：生产/可信模式下读取日线行情必须提供有效经过认证的 ExecutionContext，禁止无认证上下文取数！"
                )
            ctx_rel_id = getattr(execution_context, "release_id", None)
            if release_id and ctx_rel_id and str(release_id).strip() != str(ctx_rel_id).strip():
                raise ProductionAdmissionBlockedError(
                    f"安全红线阻断：请求传入的 Release ID [{release_id}] 与 ExecutionContext 绑定的发布上下文 [{ctx_rel_id}] 不一致！"
                )
            if not release_id and ctx_rel_id:
                release_id = ctx_rel_id
            if not pit_snapshot_id and getattr(execution_context, "pit_snapshot_id", None):
                pit_snapshot_id = execution_context.pit_snapshot_id
            if not release_id or str(release_id).strip() in ("", "None", "null", "PLACEHOLDER", "UNVERIFIED"):
                raise ProductionAdmissionBlockedError(
                    "安全红线阻断：生产执行模式下读取日线行情必须绑定有效经过认证的 release_id！"
                )

        # 北交所 (BJ) 标的覆盖审计：
        # ASDC 物理快照 dwd_equity_daily_bar_v2 仅覆盖沪深市场 (SH/SZ)。
        # 若生产请求包含北交所标的，严格 Fail-Closed 阻断，禁止未认证执行或部分漏数。
        bj_symbols = [
            s for s in symbols
            if str(s).strip().endswith(".BJ") or (str(s).strip().startswith(("4", "8", "92")) and "." not in str(s))
        ]
        if bj_symbols and is_production:
            raise ProductionAdmissionBlockedError(
                f"标的未覆盖阻断：ASDC 正式物理快照 [{release_id}] 仅覆盖沪深市场 (SH/SZ)，请求标的包含北交所标的: {bj_symbols}，严禁未认证执行或部分漏数！"
            )

        adj_lower = (adj_type or "").strip().lower()

        # 版本可见性严格审计与物理日线快照匹配：
        v_max = None
        matched_snapshot_id = None
        gate_evidence_sha256 = ""
        adj_factor_snapshot_id = None

        if release_id:
            datasets = self._c.release.get_release_datasets(release_id)
            # 严格日线行情产品标识：必须来自于 equity_daily_bar_raw (或 dwd_equity_daily_bar_v2 / dwd_daily)
            # 严禁将复权因子 (equity_adjustment_factor) 的 Snapshot 混用为日线 Snapshot！
            daily_bar_ds_names = ("equity_daily_bar_raw", "dwd_equity_daily_bar_v2", "dwd_daily")
            for ds in datasets:
                d_name = ds.get("dataset_name")
                if d_name in daily_bar_ds_names and matched_snapshot_id is None:
                    matched_snapshot_id = ds.get("snapshot_id")
                    v_max = ds.get("visible_date_max")
                    gate_evidence_sha256 = ds.get("gate_evidence_sha256") or ""
                elif d_name in ("equity_adjustment_factor", "dws_price_adj_daily"):
                    adj_factor_snapshot_id = ds.get("snapshot_id")

            if v_max is None:
                v_maxes = [ds.get("visible_date_max") for ds in datasets if ds.get("visible_date_max")]
                if v_maxes:
                    v_max = min(v_maxes)

        # end_date 防未来函数与确定性上界约束：
        effective_end_date = end_date
        if effective_end_date is None:
            if execution_context and getattr(execution_context, "trade_date", None):
                effective_end_date = int(execution_context.trade_date)
            elif v_max is not None:
                effective_end_date = int(v_max)
            elif is_production:
                raise ProductionAdmissionBlockedError(
                    "防未来函数阻断：生产模式下必须由 ExecutionContext 或显式传入 end_date 确定查询截止日期！"
                )

        if v_max is not None:
            if effective_end_date and int(effective_end_date) > int(v_max):
                raise ValueError(
                    f"未来函数拦截：请求截止日期 {effective_end_date} 超过了该 Release [{release_id}] 的最大可见日期 {v_max}！"
                )
            if start_date and int(start_date) > int(v_max):
                raise ValueError(
                    f"未来函数拦截：请求起始日期 {start_date} 超过了该 Release [{release_id}] 的最大可见日期 {v_max}！"
                )

        # 核心物理快照绑定：若数据底座存在不可变分区物理表 dwd_equity_daily_bar_v2 且绑定了合法 snapshot_id，
        # 则强制在 SQL 查询中绑定 WHERE snapshot_id = %s，物理锁定不可伪造快照来源。
        use_snapshot_partition = bool(
            matched_snapshot_id
            and str(matched_snapshot_id).strip() not in ("", "None", "null", "missing", "UNVERIFIED", "PLACEHOLDER")
            and self._c._table_ready("dwd_equity_daily_bar_v2")
        )

        if is_production and not use_snapshot_partition:
            if not self._c._table_ready("dwd_equity_daily_bar_v2"):
                raise ProductionAdmissionBlockedError(
                    "物理快照表缺失阻断：ASDC 正式物理快照表 dwd_equity_daily_bar_v2 不可用，生产模式严禁降级未分区旧表！"
                )
            if not matched_snapshot_id:
                raise ProductionAdmissionBlockedError(
                    f"日线快照未绑定阻断：Release [{release_id}] 中未找到合法日线行情数据集 (equity_daily_bar_raw) 快照标识，生产模式严禁放行！"
                )
            raise ProductionAdmissionBlockedError(
                f"生产准入阻断：Release [{release_id}] 无法物理绑定经认证的日线物理快照！"
            )

        if use_snapshot_partition:
            table_name = "dwd_equity_daily_bar_v2"
            snapshot_bound = True
            # 注意单位换算：dwd_equity_daily_bar_v2 的 amount_cny 单位为元 (CNY)，
            # 而 ScoreRank / 回测引擎标准日线接口的 amount 口径为千元，需强制除以 1000.0 对齐口径。
            if adj_lower == "raw":
                col_map = "open AS open, high AS high, low AS low, close AS close, volume_hands AS volume, (amount_cny / 1000.0) AS amount"
            else:
                col_map = "adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close, volume_hands AS volume, (amount_cny / 1000.0) AS amount"
            snap_clause = "snapshot_id = %s AND "
            snap_params = [matched_snapshot_id]
        else:
            snapshot_bound = False
            snap_clause = ""
            snap_params = []
            if adj_lower == "raw":
                table_name = "dwd_daily"
                col_map = "open AS open, high AS high, low AS low, close AS close, vol AS volume, amount"
            else:
                table_name = "dwd_stock_daily_standard"
                col_map = "adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close, vol AS volume, amount"

        # 认证状态安全约束：只有生产模式且真正绑定物理分区快照时才允许标记 certified=True
        is_certified = bool(is_production and snapshot_bound and release_id and str(release_id).strip() not in ("", "None", "null", "PLACEHOLDER", "UNVERIFIED"))

        def _make_proof(row_count: int, d_hash: str) -> DataProvenanceProof:
            if is_certified and snapshot_bound:
                status_str = "SNAPSHOT_BOUND_CERTIFIED"
            elif is_explicit_research:
                status_str = "RESEARCH_UNPARTITIONED_FALLBACK"
            else:
                status_str = "HISTORICAL_UNVERIFIED_STUDY"

            return DataProvenanceProof(
                release_id=str(release_id or "UNVERIFIED"),
                dataset_name=table_name,
                snapshot_id=str(matched_snapshot_id or ("UNVERIFIED_DIRECT_TABLE" if not is_certified else f"snap_{release_id}")),
                pit_snapshot_id=str(pit_snapshot_id) if pit_snapshot_id else None,
                visible_date_max=int(v_max or (effective_end_date or start_date)),
                gate_evidence_sha256=str(gate_evidence_sha256 or ""),
                row_count=row_count,
                data_hash=d_hash,
                verified_at=datetime.now().isoformat(),
                certified=is_certified,
                status=status_str,
                source_table=table_name,
                snapshot_bound=snapshot_bound,
            )

        if not symbols:
            empty_df = pd.DataFrame(columns=[
                "symbol", "ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"
            ])
            empty_df.attrs["release_id"] = release_id
            empty_df.attrs["pit_snapshot_id"] = pit_snapshot_id
            empty_df.attrs["certified"] = is_certified
            empty_df.attrs["provenance_proof"] = _make_proof(0, hashlib.sha256(b"EMPTY").hexdigest())
            return empty_df

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

        end_clause = "AND trade_date <= %s" if effective_end_date else ""
        results: list[dict[str, Any]] = []

        for i in range(0, len(ts_codes), chunk_size):
            chunk = ts_codes[i : i + chunk_size]
            placeholders = ",".join(["%s"] * len(chunk))
            sql = f"""
                SELECT ts_code, trade_date, {col_map}
                FROM {self._t(table_name)}
                WHERE {snap_clause}ts_code IN ({placeholders}) AND trade_date >= %s {end_clause}
                ORDER BY trade_date, ts_code
            """
            params: list[Any] = snap_params + list(chunk) + [start_date]
            if effective_end_date:
                params.append(effective_end_date)
            rows = self._c._query(sql, tuple(params))
            results.extend(rows)

        if not results:
            empty_df = pd.DataFrame(columns=[
                "symbol", "ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"
            ])
            empty_df.attrs["release_id"] = release_id
            empty_df.attrs["pit_snapshot_id"] = pit_snapshot_id
            empty_df.attrs["certified"] = is_certified
            empty_df.attrs["provenance_proof"] = _make_proof(0, hashlib.sha256(b"EMPTY").hexdigest())
            return empty_df

        df = pd.DataFrame(results)
        df["symbol"] = df["ts_code"].map(ts_to_sym)
        cols = ["symbol", "ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
        out_df = df[[c for c in cols if c in df.columns]].copy()
        data_hash = hashlib.sha256(out_df.to_csv(index=False).encode("utf-8")).hexdigest()
        out_df.attrs["release_id"] = release_id
        out_df.attrs["pit_snapshot_id"] = pit_snapshot_id
        out_df.attrs["certified"] = is_certified
        out_df.attrs["provenance_proof"] = _make_proof(len(out_df), data_hash)
        return out_df

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
