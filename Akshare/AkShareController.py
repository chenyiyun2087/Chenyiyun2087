import argparse
import akshare as ak
import json
import re
import time
from datetime import date
from typing import Callable, Dict, List, Tuple

import pandas as pd
import pymysql


AKSHARE_DOC_URL = "https://akshare.akfamily.xyz/data/nlp/nlp.html"
DATE_COLUMNS = ("date", "trade_date", "日期", "交易日期")
FUND_FLOW_COLUMN_MAP = {
    "trade_date": ("日期", "trade_date", "date", "交易日期"),
    "net_inflow": ("主力净流入", "净流入", "net_inflow", "主力净额"),
    "super_large_inflow": ("超大单净流入", "超大单净额", "超大单"),
    "large_inflow": ("大单净流入", "大单净额", "大单"),
    "medium_inflow": ("中单净流入", "中单净额", "中单"),
    "small_inflow": ("小单净流入", "小单净额", "小单"),
}
CHIP_COLUMN_MAP = {
    "trade_date": ("日期", "trade_date", "date", "交易日期"),
    "chip_concentration": ("集中度", "筹码集中度", "集中度(%)"),
    "chip_pct_90": ("90集中度", "90%集中度", "90%筹码集中度"),
    "avg_cost": ("平均成本", "平均成本(元)", "平均成本(元/股)", "平均成本"),
}

FUND_FLOW_SQL = """
INSERT INTO a_share_daily_fund_flow (
    stock_code, trade_date, net_inflow, super_large_inflow, large_inflow, medium_inflow, small_inflow
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    net_inflow = VALUES(net_inflow),
    super_large_inflow = VALUES(super_large_inflow),
    large_inflow = VALUES(large_inflow),
    medium_inflow = VALUES(medium_inflow),
    small_inflow = VALUES(small_inflow),
    updated_at = CURRENT_TIMESTAMP
"""

CHIP_SQL = """
INSERT INTO a_share_daily_chip (
    stock_code, trade_date, chip_concentration, chip_pct_90, avg_cost, chip_distribution
) VALUES (%s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    chip_concentration = VALUES(chip_concentration),
    chip_pct_90 = VALUES(chip_pct_90),
    avg_cost = VALUES(avg_cost),
    chip_distribution = VALUES(chip_distribution),
    updated_at = CURRENT_TIMESTAMP
"""


class AkShareController:
    """Akshare 常用接口封装，兼容类方式调用。"""

    def __init__(self, mysql_config=None, schema_path: str | None = None) -> None:
        self.mysql_config = mysql_config
        self.schema_path = schema_path

    def init_db(self) -> None:
        if not self.mysql_config:
            raise ValueError("mysql_config 不能为空")
        if not self.schema_path:
            raise ValueError("schema_path 不能为空")
        with open(self.schema_path, "r", encoding="utf-8") as file_handle:
            sql_text = file_handle.read()

        sql_text = sql_text.split("-- 入库示例", 1)[0]
        sql_text = re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)
        raw_statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
        statements = []
        for statement in raw_statements:
            cleaned = re.sub(r"/\*.*?\*/", "", statement, flags=re.DOTALL).strip()
            if not cleaned:
                continue
            if cleaned.startswith(("/*", "--", "#")):
                continue
            statements.append(cleaned)

        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                for statement in statements:
                    try:
                        cursor.execute(statement)
                    except pymysql.err.OperationalError as exc:
                        if exc.args and exc.args[0] == 1061:
                            continue
                        raise
            conn.commit()

    @staticmethod
    def resolve_api(ak_module, candidates: List[str]) -> Callable:
        return _resolve_api(ak_module, candidates)

    @staticmethod
    def fetch_stock_codes(limit: int | None = 500) -> List[str]:
        return fetch_stock_codes(limit=limit)

    def fetch_stock_list(self) -> pd.DataFrame:
        return fetch_stock_list()

    def upsert_stock_list(self, stock_df: pd.DataFrame) -> None:
        if not self.mysql_config:
            raise ValueError("mysql_config 不能为空")
        if stock_df.empty:
            return
        rows = []
        for _, row in stock_df.iterrows():
            code = str(row.get("code", "")).zfill(6)
            name = row.get("name") or row.get("股票简称") or ""
            exchange = derive_exchange(code)
            rows.append((code, name, exchange, None, True))
        if not rows:
            return
        sql = """
        INSERT INTO a_share_stock_list (stock_code, stock_name, exchange, list_date, is_active)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            stock_name = VALUES(stock_name),
            exchange = VALUES(exchange),
            list_date = VALUES(list_date),
            is_active = VALUES(is_active),
            updated_at = CURRENT_TIMESTAMP
        """
        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql, rows)
            conn.commit()

    def sync_one(self, symbol: str, trade_date: str) -> None:
        if not self.mysql_config:
            raise ValueError("mysql_config 不能为空")
        target_date = parse_trade_date(trade_date)
        if target_date is None:
            raise ValueError("trade_date 无法解析")

        fund_df = fetch_ths_fund_flow(ak, symbol)
        chip_df = fetch_chip_distribution(ak, symbol)

        fund_df = filter_by_dates(fund_df, [target_date])
        chip_df = filter_by_dates(chip_df, [target_date])

        fund_rows = prepare_fund_flow_rows(symbol, fund_df)
        chip_rows = prepare_chip_rows(symbol, chip_df)

        if not fund_rows and not chip_rows:
            return
        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                if fund_rows:
                    cursor.executemany(FUND_FLOW_SQL, fund_rows)
                if chip_rows:
                    cursor.executemany(CHIP_SQL, chip_rows)
            conn.commit()

    @staticmethod
    def fetch_ths_fund_flow(ak_module, symbol: str) -> pd.DataFrame:
        return fetch_ths_fund_flow(ak_module, symbol)

    @staticmethod
    def fetch_em_fund_flow(ak_module, symbol: str) -> pd.DataFrame:
        return fetch_em_fund_flow(ak_module, symbol)

    @staticmethod
    def fetch_chip_distribution(ak_module, symbol: str) -> pd.DataFrame:
        return fetch_chip_distribution(ak_module, symbol)

    @staticmethod
    def batch_fetch_signals(
        symbols: List[str],
        sleep_s: float = 0.2,
    ) -> Dict[str, List[Tuple[str, int]]]:
        return batch_fetch_signals(symbols, sleep_s=sleep_s)

    @staticmethod
    def run_sample(limit: int = 500, sleep_s: float = 0.2) -> None:
        run_sample(limit=limit, sleep_s=sleep_s)


class AkShareController:
    """Akshare 常用接口封装，兼容类方式调用。"""

    def __init__(self, mysql_config=None, schema_path: str | None = None) -> None:
        self.mysql_config = mysql_config
        self.schema_path = schema_path

    def init_db(self) -> None:
        if not self.mysql_config:
            raise ValueError("mysql_config 不能为空")
        if not self.schema_path:
            raise ValueError("schema_path 不能为空")
        with open(self.schema_path, "r", encoding="utf-8") as file_handle:
            sql_text = file_handle.read()

        sql_text = sql_text.split("-- 入库示例", 1)[0]
        sql_text = re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)
        raw_statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
        statements = []
        for statement in raw_statements:
            cleaned = re.sub(r"/\*.*?\*/", "", statement, flags=re.DOTALL).strip()
            if not cleaned:
                continue
            if cleaned.startswith(("/*", "--", "#")):
                continue
            statements.append(cleaned)

        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                for statement in statements:
                    try:
                        cursor.execute(statement)
                    except pymysql.err.OperationalError as exc:
                        if exc.args and exc.args[0] == 1061:
                            continue
                        raise
            conn.commit()

    @staticmethod
    def resolve_api(ak_module, candidates: List[str]) -> Callable:
        return _resolve_api(ak_module, candidates)

    @staticmethod
    def fetch_stock_codes(limit: int | None = 500) -> List[str]:
        return fetch_stock_codes(limit=limit)

    def fetch_stock_list(self) -> pd.DataFrame:
        return fetch_stock_list()

    def upsert_stock_list(self, stock_df: pd.DataFrame) -> None:
        if not self.mysql_config:
            raise ValueError("mysql_config 不能为空")
        if stock_df.empty:
            return
        rows = []
        for _, row in stock_df.iterrows():
            code = str(row.get("code", "")).zfill(6)
            name = row.get("name") or row.get("股票简称") or ""
            exchange = derive_exchange(code)
            rows.append((code, name, exchange, None, True))
        if not rows:
            return
        sql = """
        INSERT INTO a_share_stock_list (stock_code, stock_name, exchange, list_date, is_active)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            stock_name = VALUES(stock_name),
            exchange = VALUES(exchange),
            list_date = VALUES(list_date),
            is_active = VALUES(is_active),
            updated_at = CURRENT_TIMESTAMP
        """
        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql, rows)
            conn.commit()

    @staticmethod
    def fetch_ths_fund_flow(ak_module, symbol: str) -> pd.DataFrame:
        return fetch_ths_fund_flow(ak_module, symbol)

    @staticmethod
    def fetch_em_fund_flow(ak_module, symbol: str) -> pd.DataFrame:
        return fetch_em_fund_flow(ak_module, symbol)

    @staticmethod
    def fetch_chip_distribution(ak_module, symbol: str) -> pd.DataFrame:
        return fetch_chip_distribution(ak_module, symbol)

    @staticmethod
    def batch_fetch_signals(
        symbols: List[str],
        sleep_s: float = 0.2,
    ) -> Dict[str, List[Tuple[str, int]]]:
        return batch_fetch_signals(symbols, sleep_s=sleep_s)

    @staticmethod
    def run_sample(limit: int = 500, sleep_s: float = 0.2) -> None:
        run_sample(limit=limit, sleep_s=sleep_s)


def _resolve_api(ak_module, candidates: List[str]) -> Callable:
    for name in candidates:
        if hasattr(ak_module, name):
            return getattr(ak_module, name)
    raise AttributeError(
        f"无法找到可用的 Akshare API: {', '.join(candidates)}. "
        f"请参考: {AKSHARE_DOC_URL}"
    )


def fetch_stock_codes(limit: int | None = 500) -> List[str]:
    import akshare as ak

    info_df = ak.stock_info_a_code_name()
    if "code" not in info_df.columns:
        raise ValueError("stock_info_a_code_name 返回数据缺少 code 列")
    codes = info_df["code"].astype(str).str.zfill(6).tolist()
    if limit is None:
        return codes
    return codes[:limit]


def fetch_stock_list() -> pd.DataFrame:
    import akshare as ak

    return ak.stock_info_a_code_name()


def derive_exchange(stock_code: str) -> str:
    if stock_code.startswith("6"):
        return "SH"
    if stock_code.startswith(("0", "3")):
        return "SZ"
    if stock_code.startswith(("4", "8")):
        return "BJ"
    return "UNKNOWN"


def pick_column(df: pd.DataFrame, candidates: Tuple[str, ...]) -> str | None:
    for column in candidates:
        if column in df.columns:
            return column
    return None


def parse_trade_date(value) -> date | None:
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def filter_by_dates(df: pd.DataFrame, targets: List[date]) -> pd.DataFrame:
    for column in DATE_COLUMNS:
        if column in df.columns:
            parsed = pd.to_datetime(df[column], errors="coerce").dt.date
            return df.loc[parsed.isin(set(targets))]
    return df


def normalize_numeric(value):
    if value is None or value == "":
        return None
    return pd.to_numeric(value, errors="coerce")


def prepare_fund_flow_rows(symbol: str, df: pd.DataFrame) -> List[tuple]:
    if df.empty:
        return []
    date_col = pick_column(df, FUND_FLOW_COLUMN_MAP["trade_date"])
    if date_col is None:
        return []
    columns = {
        key: pick_column(df, candidates)
        for key, candidates in FUND_FLOW_COLUMN_MAP.items()
        if key != "trade_date"
    }
    rows = []
    for _, row in df.iterrows():
        trade_date = parse_trade_date(row.get(date_col))
        if trade_date is None:
            continue
        rows.append(
            (
                symbol,
                trade_date,
                normalize_numeric(row.get(columns["net_inflow"])) if columns["net_inflow"] else None,
                normalize_numeric(row.get(columns["super_large_inflow"]))
                if columns["super_large_inflow"]
                else None,
                normalize_numeric(row.get(columns["large_inflow"])) if columns["large_inflow"] else None,
                normalize_numeric(row.get(columns["medium_inflow"])) if columns["medium_inflow"] else None,
                normalize_numeric(row.get(columns["small_inflow"])) if columns["small_inflow"] else None,
            )
        )
    return rows


def prepare_chip_rows(symbol: str, df: pd.DataFrame) -> List[tuple]:
    if df.empty:
        return []
    date_col = pick_column(df, CHIP_COLUMN_MAP["trade_date"])
    if date_col is None:
        return []
    chip_cols = {
        key: pick_column(df, candidates)
        for key, candidates in CHIP_COLUMN_MAP.items()
        if key != "trade_date"
    }
    rows = []
    for trade_date, group in df.groupby(df[date_col]):
        parsed_date = parse_trade_date(trade_date)
        if parsed_date is None:
            continue
        sample = group.iloc[0]
        distribution = group.drop(columns=[date_col], errors="ignore").to_dict(orient="records")
        rows.append(
            (
                symbol,
                parsed_date,
                normalize_numeric(sample.get(chip_cols["chip_concentration"]))
                if chip_cols["chip_concentration"]
                else None,
                normalize_numeric(sample.get(chip_cols["chip_pct_90"])) if chip_cols["chip_pct_90"] else None,
                normalize_numeric(sample.get(chip_cols["avg_cost"])) if chip_cols["avg_cost"] else None,
                json.dumps(distribution, ensure_ascii=False),
            )
        )
    return rows


def fetch_ths_fund_flow(ak_module, symbol: str) -> pd.DataFrame:
    api = _resolve_api(
        ak_module,
        [
            "stock_individual_fund_flow_ths",
            "stock_individual_fund_flow",
            "stock_fund_flow_individual_ths",
        ],
    )
    return api(symbol=symbol)


def fetch_em_fund_flow(ak_module, symbol: str) -> pd.DataFrame:
    api = _resolve_api(
        ak_module,
        [
            "stock_fund_flow_individual_em",
            "stock_fund_flow_individual",
            "stock_individual_fund_flow_em",
        ],
    )
    return api(symbol=symbol)


def fetch_chip_distribution(ak_module, symbol: str) -> pd.DataFrame:
    api = _resolve_api(
        ak_module,
        [
            "stock_cyq_em",
            "stock_chip_distribution_em",
            "stock_chip_distribution",
        ],
    )
    return api(symbol=symbol)


def batch_fetch_signals(
    symbols: List[str],
    sleep_s: float = 0.2,
) -> Dict[str, List[Tuple[str, int]]]:
    import akshare as ak

    results: Dict[str, List[Tuple[str, int]]] = {
        "ths_fund_flow": [],
        "em_fund_flow": [],
        "chip_distribution": [],
    }

    for symbol in symbols:
        ths_df = fetch_ths_fund_flow(ak, symbol)
        results["ths_fund_flow"].append((symbol, len(ths_df)))

        em_df = fetch_em_fund_flow(ak, symbol)
        results["em_fund_flow"].append((symbol, len(em_df)))

        chip_df = fetch_chip_distribution(ak, symbol)
        results["chip_distribution"].append((symbol, len(chip_df)))

        if sleep_s > 0:
            time.sleep(sleep_s)

    return results


def run_sample(limit: int = 500, sleep_s: float = 0.2) -> None:
    symbols = fetch_stock_codes(limit=limit)
    results = batch_fetch_signals(symbols, sleep_s=sleep_s)

    print("=== 批量测试结果 ===")
    print(f"Akshare 文档: {AKSHARE_DOC_URL}")
    print(f"股票数量: {len(symbols)}")
    for key, rows in results.items():
        total_rows = sum(count for _, count in rows)
        print(f"{key}: 成功 {len(rows)} 条, 总行数 {total_rows}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Akshare 批量获取资金流向与筹码分布测试")
    parser.add_argument("--limit", type=int, default=500, help="批量股票数量，默认500")
    parser.add_argument("--sleep", type=float, default=0.2, help="请求间隔(秒)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_sample(limit=args.limit, sleep_s=args.sleep)
