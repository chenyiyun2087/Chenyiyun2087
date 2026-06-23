"""ADCClient — Chenyiyun2087 消费 AShareDataCenter 数据的统一入口。

所有查询通过 `tushare_stock.` 跨库前缀访问 ADC 数据。
禁止直接拼接 PIT 条件；统一使用本 SDK。
"""

from __future__ import annotations

import os
from typing import Any

import pymysql

ADC_DATABASE = "tushare_stock"

# ===========================================================================
# DB Connection
# ===========================================================================


class ADCClient:
    """AShareDataCenter 跨库查询客户端。

    Usage:
        client = ADCClient()
        status = client.pit.stock_status("000001.SZ", 20260623)
        features = client.features.query(feature_set_version="fs_v1", trade_date=20260623)
    """

    def __init__(self):
        self._conn = None

    def _get_conn(self):
        if self._conn is None or not self._conn.open:
            # 读取 AShareDataCenter 的 MySQL 配置（同一服务器，不同 database）
            import configparser as _cp
            _cfg = _cp.ConfigParser()
            _cfg.read("/Volumes/extension/projects/AShareDataCenter/config/etl.ini")
            self._conn = pymysql.connect(
                host=_cfg.get("mysql", "host", fallback="localhost"),
                port=_cfg.getint("mysql", "port", fallback=3306),
                user=_cfg.get("mysql", "user", fallback="root"),
                password=_cfg.get("mysql", "password", fallback=""),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                database=ADC_DATABASE,
            )
        return self._conn

    def _query(self, sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def _table_ready(self, table_name: str) -> bool:
        name = table_name.replace(f"{ADC_DATABASE}.", "")
        rows = self._query(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (ADC_DATABASE, name),
        )
        return len(rows) > 0

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
# PIT Client
# ===========================================================================


class ADCPitClient:
    def __init__(self, client: ADCClient):
        self._c = client

    def _t(self, name: str) -> str:
        return f"{ADC_DATABASE}.{name}"

    def stock_status(self, ts_code: str, trade_date: int) -> dict[str, Any]:
        """查询 PIT 股票状态。回退链: dim_stock_status_scd → dim_stock。"""
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
                return {"ts_code": ts_code, "trade_date": trade_date, "status": status,
                        "is_listed": status not in ("delisted", "pre_listed"),
                        "is_st": status in ("st", "st_star", "risk_warning"),
                        "source": "dim_stock_status_scd"}

        # 回退: dim_stock
        rows = self._c._query(f"""
            SELECT list_date, delist_date FROM {self._t('dim_stock')}
            WHERE ts_code = %s LIMIT 1
        """, (ts_code,))
        if rows:
            r = rows[0]
            listed = r["list_date"] <= trade_date
            delisted = r.get("delist_date") and r["delist_date"] < trade_date
            return {"ts_code": ts_code, "trade_date": trade_date,
                    "status": "delisted" if delisted else "listed" if listed else "pre_listed",
                    "is_listed": listed and not delisted, "is_st": False, "source": "dim_stock"}
        return {"ts_code": ts_code, "trade_date": trade_date, "status": "unknown",
                "is_listed": False, "is_st": False, "source": "none"}

    def industry(self, ts_code: str, trade_date: int, system: str = "SW2021") -> dict[str, Any]:
        """查询 PIT 行业归属。缺失返回 UNKNOWN。"""
        if not self._c._table_ready("dwd_stock_industry_scd"):
            return {"ts_code": ts_code, "trade_date": trade_date, "industry_name": "UNKNOWN",
                    "source": "fallback_unknown"}
        rows = self._c._query(f"""
            SELECT industry_name, industry_code FROM {self._t('dwd_stock_industry_scd')}
            WHERE ts_code = %s AND industry_system = %s
              AND effective_date <= %s AND (expire_date IS NULL OR %s < expire_date)
            ORDER BY effective_date DESC LIMIT 1
        """, (ts_code, system, trade_date, trade_date))
        if rows:
            return {"ts_code": ts_code, "trade_date": trade_date, **rows[0], "source": "dwd_stock_industry_scd"}
        return {"ts_code": ts_code, "trade_date": trade_date, "industry_name": "UNKNOWN", "source": "fallback_unknown"}

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
        return f"{ADC_DATABASE}.{name}"

    def get_daily_bars(self, ts_code: str, start_date: int, end_date: int) -> list[dict[str, Any]]:
        """获取前复权日线。"""
        return self._c._query(f"""
            SELECT trade_date, ts_code, qfq_close, qfq_ret_1, qfq_ret_5, qfq_ret_20, qfq_ret_60
            FROM {self._t('dws_price_adj_daily')}
            WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (ts_code, start_date, end_date))

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
