import argparse
import akshare as ak
import re
import time
from typing import Callable, Dict, List, Tuple

import pandas as pd
import pymysql


AKSHARE_DOC_URL = "https://akshare.akfamily.xyz/data/nlp/nlp.html"


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
        statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]

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
