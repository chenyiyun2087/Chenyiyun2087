import argparse
import akshare as ak
import time
from typing import Callable, Dict, List, Tuple

import pandas as pd


AKSHARE_DOC_URL = "https://akshare.akfamily.xyz/data/nlp/nlp.html"


def _resolve_api(ak_module, candidates: List[str]) -> Callable:
    for name in candidates:
        if hasattr(ak_module, name):
            return getattr(ak_module, name)
    raise AttributeError(
        f"无法找到可用的 Akshare API: {', '.join(candidates)}. "
        f"请参考: {AKSHARE_DOC_URL}"
    )


def fetch_stock_codes(limit: int = 500) -> List[str]:
    import akshare as ak

    info_df = ak.stock_info_a_code_name()
    if "code" not in info_df.columns:
        raise ValueError("stock_info_a_code_name 返回数据缺少 code 列")
    codes = info_df["code"].astype(str).str.zfill(6).tolist()
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
