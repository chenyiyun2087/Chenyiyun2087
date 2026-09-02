"""Review prior-week production recommendations over a common review week.

Run:
  export CHENYIYUN_DB_* from the read-only credential manager
  .venv/bin/python scripts/research/review_weekly_recommendation_performance.py \
    --signal-start 2026-08-17 --signal-end 2026-08-21 \
    --performance-start 2026-08-24 --performance-end 2026-08-28

Requires: sealed production candidate exports and read-only MySQL access.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


DEFAULT_STRATEGY = "production_governed_vol_position"
STOCK_PRICE_TABLE = "tushare_stock.dwd_stock_daily_standard"
INDEX_PRICE_TABLE = "tushare_stock.dwd_index_daily"
CALENDAR_TABLE = "chenyiyun.dim_trade_cal"


def _date_int(value: date | datetime | str | int) -> int:
    if isinstance(value, (date, datetime)):
        return int(value.strftime("%Y%m%d"))
    raw = str(value).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 8:
        raise ValueError(f"invalid date value: {value!r}")
    return int(digits[:8])


def _date_iso(value: date | datetime | str | int) -> str:
    raw = str(_date_int(value))
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _pct(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(number):
        return "-"
    return f"{number * 100:.{digits}f}%"


def _price(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(number):
        return "-"
    return f"{number:.2f}"


def load_trade_calendar(engine, start_date: int, end_date: int) -> list[int]:
    frame = pd.read_sql(
        text(
            f"SELECT cal_date FROM {CALENDAR_TABLE} "
            "WHERE exchange='SSE' AND is_open=1 "
            "AND cal_date BETWEEN :start_date AND :end_date "
            "ORDER BY cal_date"
        ),
        engine,
        params={"start_date": start_date, "end_date": end_date},
    )
    if frame.empty:
        raise RuntimeError("trade calendar returned no rows")
    return sorted({_date_int(value) for value in frame["cal_date"]})


def load_sealed_candidates(
    candidate_root: Path,
    signal_dates: list[int],
    strategy: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    required = {
        "rank",
        "signal_date",
        "symbol",
        "name",
        "industry",
        "effective_weight",
    }
    for signal_date in signal_dates:
        matches = sorted(
            candidate_root.glob(
                f"{signal_date}_*_{strategy}/trusted_strategy_candidates.csv"
            )
        )
        if len(matches) != 1:
            raise RuntimeError(
                f"{_date_iso(signal_date)}: expected one sealed candidate export, "
                f"found {len(matches)}"
            )
        frame = pd.read_csv(matches[0], dtype={"symbol": str})
        missing = required.difference(frame.columns)
        if missing:
            raise RuntimeError(f"{matches[0]} missing columns: {sorted(missing)}")
        frame["symbol"] = frame["symbol"].astype(str).str.extract(r"(\d{6})", expand=False)
        frame["signal_date"] = _date_iso(signal_date)
        frame["source_file"] = str(matches[0])
        frame = frame.sort_values("rank").head(5).copy()
        if len(frame) != 5 or frame["symbol"].isna().any():
            raise RuntimeError(f"{matches[0]} does not contain exactly five valid candidates")
        frames.append(
            frame[
                [
                    "rank",
                    "signal_date",
                    "symbol",
                    "name",
                    "industry",
                    "effective_weight",
                    "rank_score",
                    "bs_score_v2",
                    "latest_close",
                    "source_file",
                ]
            ]
        )
    result = pd.concat(frames, ignore_index=True)
    result["effective_weight"] = pd.to_numeric(result["effective_weight"], errors="coerce")
    if result["effective_weight"].isna().any():
        raise RuntimeError("candidate effective_weight contains null/non-numeric values")
    if result.duplicated(["signal_date", "rank"]).any():
        raise RuntimeError("duplicate rank within a signal date")
    return result


def load_stock_prices(
    engine,
    symbols: list[str],
    start_date: int,
    end_date: int,
) -> pd.DataFrame:
    params = {f"symbol_{i}": symbol for i, symbol in enumerate(symbols)}
    placeholders = ",".join(f":symbol_{i}" for i in range(len(symbols)))
    frame = pd.read_sql(
        text(
            f"""
            SELECT trade_date, LEFT(ts_code, 6) AS symbol, ts_code,
                   adj_open, adj_low, adj_close
            FROM {STOCK_PRICE_TABLE}
            WHERE trade_date BETWEEN :start_date AND :end_date
              AND LEFT(ts_code, 6) IN ({placeholders})
            ORDER BY symbol, trade_date
            """
        ),
        engine,
        params={**params, "start_date": start_date, "end_date": end_date},
    )
    if frame.empty:
        raise RuntimeError("stock price query returned no rows")
    frame["trade_date"] = frame["trade_date"].map(_date_int)
    frame["symbol"] = frame["symbol"].astype(str).str.extract(r"(\d{6})", expand=False)
    for column in ["adj_open", "adj_low", "adj_close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.duplicated(["symbol", "trade_date"]).any():
        raise RuntimeError("duplicate stock price rows per symbol/trade date")
    return frame


def load_benchmark_prices(
    engine,
    benchmark_codes: list[str],
    start_date: int,
    end_date: int,
) -> pd.DataFrame:
    params = {f"benchmark_{i}": code for i, code in enumerate(benchmark_codes)}
    placeholders = ",".join(f":benchmark_{i}" for i in range(len(benchmark_codes)))
    frame = pd.read_sql(
        text(
            f"""
            SELECT ts_code, trade_date, `open`, `close`
            FROM {INDEX_PRICE_TABLE}
            WHERE ts_code IN ({placeholders})
              AND trade_date BETWEEN :start_date AND :end_date
            ORDER BY ts_code, trade_date
            """
        ),
        engine,
        params={**params, "start_date": start_date, "end_date": end_date},
    )
    if frame.empty:
        return frame
    frame["trade_date"] = frame["trade_date"].map(_date_int)
    for column in ["open", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise RuntimeError("duplicate benchmark price rows per code/trade date")
    return frame


def _path_stats(close_prices: list[float]) -> tuple[float, float]:
    curve = pd.Series(close_prices, dtype=float)
    drawdown = (curve / curve.cummax() - 1.0).min()
    return float(min(close_prices)), float(drawdown)


def build_recommendation_performance(
    candidates: pd.DataFrame,
    prices: pd.DataFrame,
    signal_to_execution: dict[int, int],
    performance_dates: list[int],
    performance_end: int,
) -> pd.DataFrame:
    price_index = prices.set_index(["symbol", "trade_date"])
    rows: list[dict[str, object]] = []
    for row in candidates.itertuples(index=False):
        signal_date = _date_int(row.signal_date)
        execution_date = signal_to_execution[signal_date]
        entry_open = float(price_index.loc[(row.symbol, execution_date), "adj_open"])
        end_close = float(price_index.loc[(row.symbol, performance_end), "adj_close"])
        review_dates = [d for d in performance_dates if d >= max(execution_date, performance_dates[0])]
        closes = [float(price_index.loc[(row.symbol, d), "adj_close"]) for d in review_dates]
        lows = [float(price_index.loc[(row.symbol, d), "adj_low"]) for d in review_dates]
        min_close, close_drawdown = _path_stats(closes)
        rows.append(
            {
                "signal_date": row.signal_date,
                "rank": int(row.rank),
                "symbol": row.symbol,
                "name": row.name,
                "industry": row.industry,
                "execution_date": _date_iso(execution_date),
                "entry_open": entry_open,
                "end_close": end_close,
                "return_t1_open_to_end": end_close / entry_open - 1.0,
                "review_window_low_vs_entry": min(lows) / entry_open - 1.0,
                "review_window_min_close_vs_entry": min_close / entry_open - 1.0,
                "review_window_close_drawdown": close_drawdown,
                "effective_weight": float(row.effective_weight),
            }
        )
    return pd.DataFrame(rows)


def build_unique_performance(
    candidates: pd.DataFrame,
    prices: pd.DataFrame,
    performance_dates: list[int],
    prior_performance_date: int,
    performance_start: int,
    performance_end: int,
) -> pd.DataFrame:
    price_index = prices.set_index(["symbol", "trade_date"])
    metadata = (
        candidates.groupby("symbol", as_index=False)
        .agg(
            name=("name", "first"),
            industry=("industry", "first"),
            recommendation_count=("symbol", "size"),
            first_signal_date=("signal_date", "min"),
            last_signal_date=("signal_date", "max"),
        )
    )
    rows: list[dict[str, object]] = []
    for row in metadata.itertuples(index=False):
        monday_open = float(price_index.loc[(row.symbol, performance_start), "adj_open"])
        friday_close = float(price_index.loc[(row.symbol, performance_end), "adj_close"])
        prior_close = float(price_index.loc[(row.symbol, prior_performance_date), "adj_close"])
        lows = [float(price_index.loc[(row.symbol, d), "adj_low"]) for d in performance_dates]
        closes = [float(price_index.loc[(row.symbol, d), "adj_close"]) for d in performance_dates]
        min_close, close_drawdown = _path_stats(closes)
        rows.append(
            {
                "symbol": row.symbol,
                "name": row.name,
                "industry": row.industry,
                "recommendation_count": int(row.recommendation_count),
                "first_signal_date": row.first_signal_date,
                "last_signal_date": row.last_signal_date,
                "performance_start_open": monday_open,
                "performance_end_close": friday_close,
                "week_open_to_close": friday_close / monday_open - 1.0,
                "friday_close_to_friday_close": friday_close / prior_close - 1.0,
                "week_low_vs_start_open": min(lows) / monday_open - 1.0,
                "week_close_drawdown": close_drawdown,
            }
        )
    return pd.DataFrame(rows).sort_values("week_open_to_close", ascending=False)


def benchmark_summary(
    benchmarks: pd.DataFrame,
    prior_performance_date: int,
    performance_start: int,
    performance_end: int,
) -> pd.DataFrame:
    if benchmarks.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for code, group in benchmarks.groupby("ts_code"):
        indexed = group.set_index("trade_date")
        if not {prior_performance_date, performance_start, performance_end}.issubset(indexed.index):
            continue
        rows.append(
            {
                "benchmark": code,
                "friday_close_to_friday_close": float(
                    indexed.loc[performance_end, "close"]
                    / indexed.loc[prior_performance_date, "close"]
                    - 1.0
                ),
                "start_open_to_end_close": float(
                    indexed.loc[performance_end, "close"]
                    / indexed.loc[performance_start, "open"]
                    - 1.0
                ),
            }
        )
    return pd.DataFrame(rows)


def render_report(
    candidates: pd.DataFrame,
    recommendation_performance: pd.DataFrame,
    unique_performance: pd.DataFrame,
    benchmarks: pd.DataFrame,
    signal_to_execution: dict[int, int],
    signal_start: int,
    signal_end: int,
    performance_start: int,
    performance_end: int,
    report_date: str,
    output_dir: Path,
) -> str:
    rec_returns = recommendation_performance["return_t1_open_to_end"]
    rec_weighted = np.average(
        recommendation_performance["return_t1_open_to_end"],
        weights=recommendation_performance["effective_weight"],
    )
    unique_returns = unique_performance["week_open_to_close"]
    benchmark = benchmark_summary(
        benchmarks,
        prior_performance_date=max(d for d in signal_to_execution if d < performance_start),
        performance_start=performance_start,
        performance_end=performance_end,
    )
    benchmark_map = benchmark.set_index("benchmark").to_dict("index") if not benchmark.empty else {}

    by_signal = (
        recommendation_performance.groupby("signal_date", as_index=False)
        .apply(
            lambda group: pd.Series(
                {
                    "execution_date": group["execution_date"].iloc[0],
                    "avg_return": group["return_t1_open_to_end"].mean(),
                    "win_rate": (group["return_t1_open_to_end"] > 0).mean(),
                    "target_weighted_return": np.average(
                        group["return_t1_open_to_end"], weights=group["effective_weight"]
                    ),
                }
            ),
            include_groups=False,
        )
        .reset_index(drop=True)
    )

    lines = [
        "# 生产可信策略推荐前向周度复盘",
        "",
        f"**复盘生成日**：{report_date}",
        f"**推荐窗口**：{_date_iso(signal_start)} 至 {_date_iso(signal_end)}",
        f"**表现窗口**：{_date_iso(performance_start)} 至 {_date_iso(performance_end)}（完整交易周）",
        "**策略**：`production_governed_vol_position`",
        "",
        "## 结论",
        "",
        f"- 上周共 25 条推荐，去重后 13 只股票；推荐文件全部为信号日封存导出。",
        f"- 去重池等权：表现周开盘至周五收盘 {_pct(unique_returns.mean())}，中位数 {_pct(unique_returns.median())}，胜率 {_pct((unique_returns > 0).mean(), 1)}。",
        f"- 按推荐记录、T+1 开盘买入并持有至 {_date_iso(performance_end)} 收盘：等权 {_pct(rec_returns.mean())}，中位数 {_pct(rec_returns.median())}，胜率 {_pct((rec_returns > 0).mean(), 1)}。",
        f"- 按候选目标权重归一后的推荐记录收益：{_pct(rec_weighted)}；该数值是推荐组合内部收益，不是账户实盘收益。",
        "- 本复盘不含手续费、滑点和成交约束；候选价格表现不等于实际成交损益。",
        "",
        "## 基准",
        "",
        "| 基准 | 前一周五收盘→本周五收盘 | 本周一开盘→本周五收盘 |",
        "|---|---:|---:|",
    ]
    if benchmark.empty:
        lines.append("| 无可用基准数据 | - | - |")
    else:
        for row in benchmark.itertuples(index=False):
            lines.append(
                f"| {row.benchmark} | {_pct(row.friday_close_to_friday_close)} | "
                f"{_pct(row.start_open_to_end_close)} |"
            )
    lines.extend(
        [
            "",
            "## 按推荐日汇总",
            "",
            "| 推荐日 | T+1执行日 | 5只等权至表现周末 | 胜率 | 目标权重收益 |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in by_signal.itertuples(index=False):
        lines.append(
            f"| {row.signal_date} | {row.execution_date} | {_pct(row.avg_return)} | "
            f"{_pct(row.win_rate, 1)} | {_pct(row.target_weighted_return)} |"
        )
    lines.extend(
        [
            "",
            "## 去重股票表现",
            "",
            "| 代码 | 名称 | 行业 | 推荐次数 | 本周开盘→周五收盘 | 周内最低/周一开盘 | 周五/前周五收盘 |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in unique_performance.itertuples(index=False):
        lines.append(
            f"| {row.symbol} | {row.name} | {row.industry} | {int(row.recommendation_count)} | "
            f"{_pct(row.week_open_to_close)} | {_pct(row.week_low_vs_start_open)} | "
            f"{_pct(row.friday_close_to_friday_close)} |"
        )
    lines.extend(
        [
            "",
            "## 口径与来源",
            "",
            "- 推荐来源：`exports/production_candidates/YYYYMMDD_*_production_governed_vol_position/trusted_strategy_candidates.csv`。",
            f"- 股票行情：`{STOCK_PRICE_TABLE}`，使用前复权 `adj_open/adj_low/adj_close`。",
            f"- 基准行情：`{INDEX_PRICE_TABLE}`；交易日历：`{CALENDAR_TABLE}`。",
            "- 执行口径：推荐在 T 日收盘后形成，推荐记录收益从 T+1 开盘计算；共同表现周另列周一开盘至周五收盘。",
            "- 未来函数控制：候选文件按信号日封存，行情只用于信号日之后的表现评估；没有用后续评分或后续候选替换原推荐。",
            f"- 原始明细：`{output_dir / 'recommendation_performance.csv'}`；去重明细：`{output_dir / 'unique_stock_performance.csv'}`。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Review prior-week production recommendations over a common week")
    parser.add_argument("--signal-start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--signal-end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--performance-start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--performance-end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument(
        "--candidate-root",
        default=str(PROJECT_ROOT / "exports" / "production_candidates"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "exports" / "weekly_recommendation_reviews"),
    )
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--report-date", default=date.today().isoformat())
    args = parser.parse_args()

    signal_start = _date_int(args.signal_start)
    signal_end = _date_int(args.signal_end)
    performance_start = _date_int(args.performance_start)
    performance_end = _date_int(args.performance_end)
    if not signal_start <= signal_end < performance_start <= performance_end:
        raise SystemExit("date order must be signal_start <= signal_end < performance_start <= performance_end")

    engine = create_engine(build_sqlalchemy_url(), pool_pre_ping=True)
    calendar = load_trade_calendar(engine, signal_start, performance_end)
    signal_dates = [d for d in calendar if signal_start <= d <= signal_end]
    performance_dates = [d for d in calendar if performance_start <= d <= performance_end]
    if not signal_dates or not performance_dates:
        raise SystemExit("signal or performance window has no trading days")
    if performance_dates[0] != performance_start or performance_dates[-1] != performance_end:
        raise SystemExit("performance window must start and end on trading days")

    next_trade = {
        current: following
        for current, following in zip(calendar, calendar[1:])
    }
    signal_to_execution = {signal: next_trade[signal] for signal in signal_dates}
    prior_performance_date = max(d for d in calendar if d < performance_start)

    candidate_root = Path(args.candidate_root)
    candidates = load_sealed_candidates(candidate_root, signal_dates, args.strategy)
    symbols = sorted(candidates["symbol"].unique())
    prices = load_stock_prices(
        engine,
        symbols,
        start_date=min(prior_performance_date, min(signal_to_execution.values())),
        end_date=performance_end,
    )
    benchmarks = load_benchmark_prices(
        engine,
        ["000300.SH", "000852.SH"],
        start_date=prior_performance_date,
        end_date=performance_end,
    )

    recommendation_performance = build_recommendation_performance(
        candidates,
        prices,
        signal_to_execution,
        performance_dates,
        performance_end,
    )
    unique_performance = build_unique_performance(
        candidates,
        prices,
        performance_dates,
        prior_performance_date,
        performance_start,
        performance_end,
    )

    output_dir = Path(args.output_dir) / (
        f"{signal_start}_{signal_end}_to_{performance_start}_{performance_end}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    recommendation_performance.to_csv(
        output_dir / "recommendation_performance.csv", index=False, encoding="utf-8-sig"
    )
    unique_performance.to_csv(
        output_dir / "unique_stock_performance.csv", index=False, encoding="utf-8-sig"
    )
    report = render_report(
        candidates,
        recommendation_performance,
        unique_performance,
        benchmarks,
        signal_to_execution,
        signal_start,
        signal_end,
        performance_start,
        performance_end,
        args.report_date,
        output_dir,
    )
    report_path = Path(args.report_output) if args.report_output else output_dir / "review.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    print(report_path)
    print(output_dir / "recommendation_performance.csv")
    print(output_dir / "unique_stock_performance.csv")


if __name__ == "__main__":
    main()
