"""
从聚宽 API 迁移至 Akshare 的量化策略示例。

说明：
- 聚宽的 get_fundamentals / get_factor_values 等接口在 Akshare 中不存在直接等价物，
  因此此脚本通过 Akshare 可用接口拼装近似指标。
- 如果某些列名或接口在你当前 Akshare 版本不可用，请根据日志提示替换为可用列/接口。
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import akshare as ak
import pandas as pd


AKSHARE_DOC_URL = "https://akshare.akfamily.xyz/data/stock/stock.html"

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class StockSignal:
    code: str
    name: str
    dividend_ratio: Optional[float]
    turnover_volatility: Optional[float]
    leverage_ratio: Optional[float]


def _resolve_api(ak_module, candidates: Sequence[str]):
    for name in candidates:
        if hasattr(ak_module, name):
            return getattr(ak_module, name)
    raise AttributeError(
        "无法找到可用的 Akshare API: "
        f"{', '.join(candidates)}. 请参考: {AKSHARE_DOC_URL}"
    )


def _pick_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _parse_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", ""), errors="coerce")


def fetch_spot_df() -> pd.DataFrame:
    api = _resolve_api(
        ak,
        ["stock_zh_a_spot_em", "stock_zh_a_spot"],
    )
    df = api()
    if df.empty:
        raise ValueError("Akshare 行情接口返回空数据")
    return df


def fetch_stock_codes() -> pd.DataFrame:
    api = _resolve_api(
        ak,
        ["stock_info_a_code_name", "stock_info_a_code"],
    )
    df = api()
    if "code" not in df.columns:
        raise ValueError("stock_info_a_code_name 返回数据缺少 code 列")
    name_col = _pick_column(df, ["name", "名称"])
    if name_col is None:
        df["name"] = ""
    else:
        df["name"] = df[name_col]
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df[["code", "name"]]


def filter_universe(df: pd.DataFrame, min_list_days: int = 375) -> pd.DataFrame:
    df = df.copy()
    df = df[~df["name"].str.contains("ST|退", na=False)]
    df = df[~df["code"].str.startswith(("688", "689", "8"))]

    listing_date_col = _pick_column(df, ["listing_date", "上市日期", "上市时间", "list_date"])
    if listing_date_col:
        df[listing_date_col] = pd.to_datetime(df[listing_date_col], errors="coerce")
        cutoff = dt.datetime.now() - dt.timedelta(days=min_list_days)
        df = df[df[listing_date_col].isna() | (df[listing_date_col] <= cutoff)]
    else:
        logger.warning("未找到上市日期列，跳过新股过滤")
    return df


def compute_dividend_ratio(code: str, market_cap: Optional[float]) -> Optional[float]:
    if not market_cap or market_cap <= 0:
        return None

    api = _resolve_api(
        ak,
        ["stock_dividend_cninfo", "stock_dividend", "stock_history_dividend"],
    )
    try:
        df = api(symbol=code)
    except TypeError:
        df = api(code)

    if df.empty:
        return None

    date_col = _pick_column(df, ["公告日期", "公告时间", "实施日期", "除权除息日", "date"])
    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        cutoff = dt.datetime.now() - dt.timedelta(days=365)
        df = df[df[date_col] >= cutoff]

    cash_col = _pick_column(
        df,
        [
            "派息(税前)",
            "派息", 
            "现金分红", 
            "每股派息", 
            "分红金额",
        ],
    )
    if cash_col is None:
        return None

    dividend_amount = _parse_numeric(df[cash_col]).sum()
    if dividend_amount <= 0:
        return None
    return dividend_amount / market_cap


def compute_turnover_volatility(code: str, days: int = 60) -> Optional[float]:
    end = dt.datetime.now().strftime("%Y%m%d")
    start = (dt.datetime.now() - dt.timedelta(days=days * 2)).strftime("%Y%m%d")

    api = _resolve_api(
        ak,
        ["stock_zh_a_hist", "stock_zh_a_hist_em"],
    )
    df = api(symbol=code, start_date=start, end_date=end, adjust="qfq")
    if df.empty:
        return None

    turnover_col = _pick_column(df, ["换手率", "turnover_rate", "turnover"])
    if turnover_col is None:
        return None

    series = _parse_numeric(df[turnover_col]).dropna()
    if len(series) < 5:
        return None
    return float(series.tail(days).std())


def compute_leverage_ratio(code: str) -> Optional[float]:
    api = _resolve_api(
        ak,
        [
            "stock_financial_analysis_indicator",
            "stock_financial_analysis_indicator_em",
            "stock_financial_indicator",
        ],
    )
    try:
        df = api(symbol=code)
    except TypeError:
        df = api(code)

    if df.empty:
        return None

    leverage_col = _pick_column(df, ["资产负债率", "资产负债率(%)", "资产负债率%", "debt_ratio"])
    if leverage_col is None:
        return None

    latest = _parse_numeric(df[leverage_col]).dropna()
    if latest.empty:
        return None
    return float(latest.iloc[0])


def build_signals(spot_df: pd.DataFrame, codes_df: pd.DataFrame) -> List[StockSignal]:
    spot_code_col = _pick_column(spot_df, ["代码", "code", "symbol"])
    if spot_code_col is None:
        raise ValueError("行情数据缺少代码列，无法合并股票池")

    merged = codes_df.merge(spot_df, left_on="code", right_on=spot_code_col)
    market_cap_col = _pick_column(merged, ["总市值", "总市值(元)", "market_cap"])

    signals: List[StockSignal] = []
    for _, row in merged.iterrows():
        market_cap = None
        if market_cap_col:
            market_cap = _parse_numeric(pd.Series([row[market_cap_col]])).iloc[0]
        code = str(row["code"])
        name = str(row["name"])

        dividend_ratio = compute_dividend_ratio(code, market_cap)
        turnover_vol = compute_turnover_volatility(code)
        leverage_ratio = compute_leverage_ratio(code)

        signals.append(
            StockSignal(
                code=code,
                name=name,
                dividend_ratio=dividend_ratio,
                turnover_volatility=turnover_vol,
                leverage_ratio=leverage_ratio,
            )
        )
    return signals


def select_stocks(
    signals: Iterable[StockSignal],
    max_holdings: int = 5,
) -> List[StockSignal]:
    df = pd.DataFrame([s.__dict__ for s in signals])

    df = df.dropna(subset=["dividend_ratio", "turnover_volatility", "leverage_ratio"])
    if df.empty:
        return []

    df = df.sort_values("dividend_ratio", ascending=False)
    df = df.head(int(len(df) * 0.5))

    df = df.sort_values("turnover_volatility", ascending=False)
    df = df.head(int(len(df) * 0.8))

    df = df.sort_values("leverage_ratio", ascending=True)
    df = df.head(max_holdings)

    return [
        StockSignal(
            code=row["code"],
            name=row["name"],
            dividend_ratio=row["dividend_ratio"],
            turnover_volatility=row["turnover_volatility"],
            leverage_ratio=row["leverage_ratio"],
        )
        for _, row in df.iterrows()
    ]


def run_strategy(max_holdings: int = 5) -> List[StockSignal]:
    spot_df = fetch_spot_df()
    codes_df = fetch_stock_codes()
    codes_df = filter_universe(codes_df)

    if codes_df.empty:
        raise ValueError("筛选后股票池为空")

    signals = build_signals(spot_df, codes_df)
    selected = select_stocks(signals, max_holdings=max_holdings)

    logger.info("选股完成，共选出 %s 只股票", len(selected))
    for stock in selected:
        logger.info(
            "%s %s | 股息率: %.4f, 换手波动: %.4f, 负债率: %.2f",
            stock.code,
            stock.name,
            stock.dividend_ratio or 0,
            stock.turnover_volatility or 0,
            stock.leverage_ratio or 0,
        )
    return selected


def main() -> None:
    run_strategy()


if __name__ == "__main__":
    main()
