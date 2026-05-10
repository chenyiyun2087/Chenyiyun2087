from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql


DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "19871019",
    "database": "chenyiyun",
    "charset": "utf8mb4",
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = PROJECT_ROOT / "exports" / "signal_enhancement"

SCORE_FEATURE_COLUMNS = [
    "score",
    "base_score",
    "penalty",
    "s_trend",
    "s_breakout",
    "s_volume",
    "s_rs",
    "s_contraction",
    "s_liquidity",
    "opt_score",
    "claude_score",
    "bs_score",
    "bs_entry_score",
    "close_price",
    "buy_point_close",
    "price_change_ratio",
    "is_limit_up",
    "pool_type",
    "is_self_selected",
]


def _connect():
    return pymysql.connect(**DB_CONFIG)


def _read_sql(sql: str, params=None) -> pd.DataFrame:
    with _connect() as conn:
        return pd.read_sql(sql, conn, params=params)


def _normalize_symbol(series: pd.Series) -> pd.Series:
    return series.astype(str).str.extract(r"(\d{6})", expand=False).fillna("").str.zfill(6)


def _to_ts_code(symbol: str) -> str:
    s = str(symbol).zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SZ"


def _add_ts_code(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "symbol" not in df.columns:
        return df
    out = df.copy()
    out["ts_code"] = out["symbol"].map(_to_ts_code)
    return out


def _to_date_key(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.strftime("%Y%m%d").astype(int)


def _load_first_buy_events() -> pd.DataFrame:
    sql = """
    SELECT
        STR_TO_DATE(b.batch_date, '%Y%m%d') AS event_date,
        b.batch_date AS event_date_key,
        b.stock_code AS symbol,
        b.buy_signal_description,
        b.sell_signal_description,
        b.total_b_points,
        b.total_s_points,
        b.buy_points_count,
        b.sell_points_count,
        b.process_time,
        s.name,
        s.score,
        s.base_score,
        s.penalty,
        s.s_trend,
        s.s_breakout,
        s.s_volume,
        s.s_rs,
        s.s_contraction,
        s.s_liquidity,
        s.opt_score,
        s.claude_score,
        s.bs_score,
        s.bs_entry_score,
        s.close_price,
        s.buy_point_close,
        s.price_change_ratio,
        s.is_limit_up,
        s.pool_type,
        s.is_self_selected
    FROM bs_detection_results b
    INNER JOIN score_rank_daily s
      ON s.symbol = b.stock_code
     AND s.trade_date = STR_TO_DATE(b.batch_date, '%Y%m%d')
    WHERE b.has_buy_signal = 1
    ORDER BY b.batch_date, b.stock_code
    """
    df = _read_sql(sql)
    if df.empty:
        return df
    df["symbol"] = _normalize_symbol(df["symbol"])
    df["event_date"] = pd.to_datetime(df["event_date"])
    df["event_seq_for_symbol"] = df.groupby("symbol").cumcount() + 1
    df["event_uid"] = (
        pd.to_datetime(df["event_date"]).dt.strftime("%Y%m%d")
        + "_"
        + df["symbol"]
        + "_"
        + df["event_seq_for_symbol"].astype(str).str.zfill(2)
    )
    return _add_ts_code(df)


def _load_active_panel() -> pd.DataFrame:
    sql = """
    SELECT
        s.trade_date AS event_date,
        DATE_FORMAT(s.trade_date, '%Y%m%d') AS event_date_key,
        s.symbol,
        s.name,
        s.score,
        s.base_score,
        s.penalty,
        s.s_trend,
        s.s_breakout,
        s.s_volume,
        s.s_rs,
        s.s_contraction,
        s.s_liquidity,
        s.opt_score,
        s.claude_score,
        s.bs_score,
        s.bs_entry_score,
        s.close_price,
        s.buy_point_close,
        s.price_change_ratio,
        s.is_limit_up,
        s.pool_type,
        s.is_self_selected,
        f.is_eligible,
        f.is_high_risk,
        k.ret_3,
        k.ret_5,
        k.ret_10,
        k.hit_3_10pct,
        k.hit_5_10pct,
        k.hit_10_10pct,
        k.mdd_3,
        k.mdd_5,
        k.mdd_10
    FROM score_rank_daily s
    LEFT JOIN b_event_fact f
      ON f.event_date = s.trade_date AND f.symbol = s.symbol
    LEFT JOIN b_event_kpi k
      ON k.event_date = s.trade_date AND k.symbol = s.symbol
    WHERE s.is_bs_candidate = 1
    ORDER BY s.trade_date, s.symbol
    """
    df = _read_sql(sql)
    if df.empty:
        return df
    df["symbol"] = _normalize_symbol(df["symbol"])
    df["event_date"] = pd.to_datetime(df["event_date"])
    return _add_ts_code(df)


def _load_latest_candidates() -> pd.DataFrame:
    sql = """
    SELECT
        s.trade_date AS asof_date,
        DATE_FORMAT(s.trade_date, '%Y%m%d') AS asof_date_key,
        s.symbol,
        s.name,
        s.score,
        s.base_score,
        s.penalty,
        s.s_trend,
        s.s_breakout,
        s.s_volume,
        s.s_rs,
        s.s_contraction,
        s.s_liquidity,
        s.opt_score,
        s.claude_score,
        s.bs_score,
        s.bs_entry_score,
        s.close_price,
        s.buy_point_close,
        s.price_change_ratio,
        s.is_limit_up,
        s.pool_type,
        s.is_self_selected
    FROM score_rank_daily s
    WHERE s.trade_date = (SELECT MAX(trade_date) FROM score_rank_daily)
      AND s.is_bs_candidate = 1
    ORDER BY s.bs_score DESC, s.score DESC, s.symbol
    """
    df = _read_sql(sql)
    if df.empty:
        return df
    df["symbol"] = _normalize_symbol(df["symbol"])
    df["asof_date"] = pd.to_datetime(df["asof_date"])
    return _add_ts_code(df)


def _load_prices(symbols: list[str], start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
    SELECT
        SUBSTR(ts_code, 1, 6) AS symbol,
        trade_date,
        adj_close AS close,
        vol
    FROM tushare_stock.dwd_stock_daily_standard
    WHERE trade_date >= %s
      AND trade_date <= %s
      AND SUBSTR(ts_code, 1, 6) IN ({placeholders})
    ORDER BY symbol, trade_date
    """
    params = [int(start_date.strftime("%Y%m%d")), int(end_date.strftime("%Y%m%d"))] + symbols
    px = _read_sql(sql, params=params)
    if px.empty:
        return px
    px["symbol"] = _normalize_symbol(px["symbol"])
    px["trade_date"] = pd.to_datetime(px["trade_date"].astype(str))
    px["close"] = pd.to_numeric(px["close"], errors="coerce")
    px["vol"] = pd.to_numeric(px["vol"], errors="coerce")
    return px.dropna(subset=["close"]).reset_index(drop=True)


def _horizon_labels(events: pd.DataFrame, prices: pd.DataFrame, horizons=(1, 3, 5, 10, 20)) -> pd.DataFrame:
    if events.empty or prices.empty:
        return events.copy()

    px_groups = {s: g.reset_index(drop=True) for s, g in prices.groupby("symbol", sort=False)}
    label_rows = []
    path_rows = []

    for row in events.itertuples(index=False):
        symbol = row.symbol
        event_date = pd.Timestamp(row.event_date)
        g = px_groups.get(symbol)
        event_uid = getattr(row, "event_uid", f"{event_date.strftime('%Y%m%d')}_{symbol}")
        ts_code = getattr(row, "ts_code", _to_ts_code(symbol))
        label = {"event_uid": event_uid, "event_date": event_date, "symbol": symbol, "ts_code": ts_code}
        path = {"event_uid": event_uid, "event_date": event_date, "symbol": symbol, "ts_code": ts_code}

        if g is None or g.empty:
            label_rows.append(label)
            path_rows.append(path)
            continue

        matches = g.index[g["trade_date"] == event_date].tolist()
        if not matches:
            label_rows.append(label)
            path_rows.append(path)
            continue

        idx = matches[-1]
        c0 = float(g.iloc[idx]["close"])
        if c0 <= 0:
            label_rows.append(label)
            path_rows.append(path)
            continue

        max_end = min(idx + max(horizons), len(g) - 1)
        future = g.iloc[idx : max_end + 1].copy()
        rel = future["close"].astype(float) / c0 - 1.0
        for day_no, ret in enumerate(rel.tolist()):
            path[f"rel_ret_d{day_no}"] = round(float(ret), 6)

        for h in horizons:
            if idx + h < len(g):
                window = g.iloc[idx : idx + h + 1]["close"].astype(float)
                rel_window = window / c0 - 1.0
                ret_h = float(rel_window.iloc[-1])
                label[f"ret_{h}"] = round(ret_h, 6)
                label[f"max_ret_{h}"] = round(float(rel_window.max()), 6)
                label[f"mdd_{h}"] = round(float(rel_window.min()), 6)
                label[f"hit_{h}_5pct"] = int(rel_window.max() >= 0.05)
                label[f"hit_{h}_10pct"] = int(rel_window.max() >= 0.10)
                hit_idx = np.flatnonzero(rel_window.to_numpy() >= 0.10)
                label[f"days_to_10pct_within_{h}"] = int(hit_idx[0]) if len(hit_idx) else None
            else:
                label[f"ret_{h}"] = None
                label[f"max_ret_{h}"] = None
                label[f"mdd_{h}"] = None
                label[f"hit_{h}_5pct"] = None
                label[f"hit_{h}_10pct"] = None
                label[f"days_to_10pct_within_{h}"] = None

        label_rows.append(label)
        path_rows.append(path)

    labels = pd.DataFrame(label_rows)
    paths = pd.DataFrame(path_rows)
    out = events.merge(labels.drop(columns=["event_date", "symbol", "ts_code"], errors="ignore"), on=["event_uid"], how="left")
    return out, paths


def _add_split_column(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df["sample_split"] = []
        return df
    out = df.copy()
    ordered_dates = sorted(pd.to_datetime(out["event_date"]).dt.date.unique())
    if not ordered_dates:
        out["sample_split"] = "train"
        return out
    train_cut = ordered_dates[int(len(ordered_dates) * 0.70)]
    valid_cut = ordered_dates[int(len(ordered_dates) * 0.85)]

    dates = pd.to_datetime(out["event_date"]).dt.date
    out["sample_split"] = np.select(
        [dates <= train_cut, dates <= valid_cut],
        ["train", "validation"],
        default="test",
    )
    return out


def _write_markdown(path: Path, title: str, body: str) -> None:
    path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")


def _write_docs(out_dir: Path, summary: dict) -> None:
    _write_markdown(
        out_dir / "README_FOR_EXPERT.md",
        "Sina B点信号增强数据包",
        f"""
## 目标

请基于“B点首次出现时，当时已知的信息”增强买点后的排序/过滤信号。建议目标不是预测所有股票涨跌，而是在已经出现 B 点的候选中，提高：

- 未来 10/20 个交易日最大涨幅命中率，例如 `hit_10_10pct`、`hit_20_10pct`
- 收益回撤比，例如 `max_ret_20` 与 `mdd_20`
- 最新候选的可交易排序

## 文件

- `first_buy_events_labeled.csv`：主训练表，一行代表某股票某日首次出现 B 点。包含当时评分、信号描述、未来收益标签。
- `first_buy_price_paths_20d.csv`：首次 B 点后最多 20 个交易日的相对收益路径，`rel_ret_d0=0`。
- `active_b_daily_panel_labeled.csv`：辅助表，一行代表某股票在某日仍处于 B 点有效状态，适合研究持有期加减仓。
- `latest_b_candidates.csv`：最新交易日仍有效的 B 点候选，仅用于专家产出排序/打分，无未来标签。
- `signal_enhancement_dataset.xlsx`：同内容 Excel 汇总版。
- `DATA_DICTIONARY.md`：字段解释。
- `summary.json`：本次导出的统计摘要。

## 防泄漏约束

训练新信号时只能使用 `ret_*`、`max_ret_*`、`mdd_*`、`hit_*`、`days_to_*` 以外的字段作为特征。所有未来收益字段只能作为标签或评估指标。

## 本次样本规模

- 首次 B 点事件：{summary["first_buy_events_rows"]} 行
- 带 10 日标签的首次 B 点事件：{summary["first_buy_events_ret10_rows"]} 行
- B 点有效状态日切片：{summary["active_panel_rows"]} 行
- 最新候选：{summary["latest_candidates_rows"]} 行
- 数据日期范围：{summary["date_min"]} 至 {summary["date_max"]}

## 建议交付物

请专家返回：

- 新评分公式或模型说明
- 每个候选的增强分，最好 0-100
- 分层阈值建议：强买/观察/剔除
- 在 train/validation/test 三段上的命中率、平均最大涨幅、平均最大回撤
""",
    )

    _write_markdown(
        out_dir / "DATA_DICTIONARY.md",
        "字段字典",
        """
## 标识字段

- `event_date`：首次 B 点出现日期或 B 点有效日。
- `event_uid`：首次 B 点事件唯一 ID，可用于连接价格路径表。
- `symbol` / `ts_code` / `name`：股票代码、带交易所后缀的代码和名称。CSV 被 Excel 打开时优先使用 `ts_code`，避免前导 0 丢失。
- `event_seq_for_symbol`：同一股票第几次出现 B 点事件。
- `sample_split`：按时间切分的 train / validation / test，避免随机切分导致时间泄漏。

## 当时可见信号字段

- `buy_signal_description`：B 点检测描述。
- `total_b_points` / `total_s_points`：图上历史 B/S 点数量。
- `buy_points_count` / `sell_points_count`：当日识别出的 B/S 点数量。
- `score`：Technical 总分。
- `base_score` / `penalty`：Technical 基础分与风险扣分。
- `s_trend`：趋势项。
- `s_breakout`：突破项。
- `s_volume`：量能项。
- `s_rs`：近 20 日相对强弱项。
- `s_contraction`：波动收敛项，当前分值越高代表越收敛。
- `s_liquidity`：流动性项。
- `opt_score`：因子优化分，当前通常是 0-10 标尺。
- `claude_score`：Claude 六维评分，0-100 标尺。
- `bs_score`：当前系统的 B 点增强分，0-100 标尺。
- `bs_entry_score`：买点后节奏分，偏好买点后温和确认、不过度追高。
- `close_price`：事件日收盘价。
- `buy_point_close`：买点日收盘价。
- `price_change_ratio`：事件日相对买点价涨幅百分比。
- `is_limit_up`：事件日是否涨停。
- `pool_type`：当前系统分层，`TRADE` / `WATCH` / 空。
- `is_self_selected`：是否在自选池。

## 标签字段

- `ret_1` / `ret_3` / `ret_5` / `ret_10` / `ret_20`：事件后第 N 个交易日收益。
- `max_ret_N`：事件后 N 个交易日窗口内最大收益。
- `mdd_N`：事件后 N 个交易日窗口内最大不利浮亏。
- `hit_N_5pct` / `hit_N_10pct`：N 日内是否曾达到 +5% / +10%。
- `days_to_10pct_within_N`：N 日内首次达到 +10% 所需交易日数；空表示未达到或数据不足。

## 价格路径字段

- `rel_ret_d0` 至 `rel_ret_d20`：事件后第 N 个交易日相对事件日收盘价的收益，`d0=0`。
""",
    )


def _save_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = EXPORT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    first_buy = _load_first_buy_events()
    active_panel = _load_active_panel()
    latest = _load_latest_candidates()

    if first_buy.empty:
        raise RuntimeError("No first buy events found after joining score_rank_daily.")

    start = first_buy["event_date"].min() - timedelta(days=5)
    end = max(first_buy["event_date"].max(), latest["asof_date"].max() if not latest.empty else first_buy["event_date"].max()) + timedelta(days=40)
    symbols = sorted(set(first_buy["symbol"].tolist()) | set(active_panel["symbol"].tolist()) | set(latest["symbol"].tolist()))
    prices = _load_prices(symbols, start, end)

    first_labeled, price_paths = _horizon_labels(first_buy, prices)
    first_labeled = _add_split_column(first_labeled)

    preferred_cols = [
        "sample_split",
        "event_date",
        "event_date_key",
        "event_uid",
        "symbol",
        "ts_code",
        "name",
        "event_seq_for_symbol",
        "buy_signal_description",
        "sell_signal_description",
        "total_b_points",
        "total_s_points",
        "buy_points_count",
        "sell_points_count",
        "process_time",
        *SCORE_FEATURE_COLUMNS,
    ]
    label_cols = [c for c in first_labeled.columns if c.startswith(("ret_", "max_ret_", "mdd_", "hit_", "days_to_"))]
    first_labeled = first_labeled[[c for c in preferred_cols + label_cols if c in first_labeled.columns]]

    if not active_panel.empty:
        active_panel = _add_split_column(active_panel)
        active_front = [
            "sample_split",
            "event_date",
            "event_date_key",
            "symbol",
            "ts_code",
            "name",
            *SCORE_FEATURE_COLUMNS,
            "is_eligible",
            "is_high_risk",
        ]
        active_labels = [c for c in active_panel.columns if c.startswith(("ret_", "mdd_", "hit_"))]
        active_panel = active_panel[[c for c in active_front + active_labels if c in active_panel.columns]]

    if not latest.empty:
        latest_front = [
            "asof_date",
            "asof_date_key",
            "symbol",
            "ts_code",
            "name",
            *SCORE_FEATURE_COLUMNS,
        ]
        latest = latest[[c for c in latest_front if c in latest.columns]]

    _save_csv(first_labeled, out_dir / "first_buy_events_labeled.csv")
    _save_csv(price_paths, out_dir / "first_buy_price_paths_20d.csv")
    _save_csv(active_panel, out_dir / "active_b_daily_panel_labeled.csv")
    _save_csv(latest, out_dir / "latest_b_candidates.csv")

    summary = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "date_min": str(first_labeled["event_date"].min().date()),
        "date_max": str(first_labeled["event_date"].max().date()),
        "first_buy_events_rows": int(len(first_labeled)),
        "first_buy_events_ret10_rows": int(first_labeled["ret_10"].notna().sum()) if "ret_10" in first_labeled else 0,
        "active_panel_rows": int(len(active_panel)),
        "latest_candidates_rows": int(len(latest)),
        "unique_symbols_first_buy": int(first_labeled["symbol"].nunique()),
        "files": [
            "first_buy_events_labeled.csv",
            "first_buy_price_paths_20d.csv",
            "active_b_daily_panel_labeled.csv",
            "latest_b_candidates.csv",
            "signal_enhancement_dataset.xlsx",
            "README_FOR_EXPERT.md",
            "DATA_DICTIONARY.md",
        ],
    }

    with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    _write_docs(out_dir, summary)

    xlsx_path = out_dir / "signal_enhancement_dataset.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        first_labeled.to_excel(writer, sheet_name="first_buy_events", index=False)
        price_paths.to_excel(writer, sheet_name="price_paths_20d", index=False)
        active_panel.to_excel(writer, sheet_name="active_b_panel", index=False)
        latest.to_excel(writer, sheet_name="latest_candidates", index=False)
        pd.DataFrame([summary]).to_excel(writer, sheet_name="summary", index=False)

    zip_path = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.iterdir()):
            zf.write(path, arcname=f"{out_dir.name}/{path.name}")

    print(json.dumps({**summary, "output_dir": str(out_dir), "zip_path": str(zip_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
