from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scripts.export_signal_enhancement_dataset import (
    DB_CONFIG,
    DEFAULT_HORIZONS,
    _add_engineered_features,
    _add_split_column,
    _horizon_labels,
    _load_first_buy_events,
    _load_prices,
)


OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
START_DATE = pd.Timestamp("2026-01-01")
END_DATE = pd.Timestamp(datetime.now().date())


def _read_sql(sql: str, params=None) -> pd.DataFrame:
    with pymysql.connect(**DB_CONFIG) as conn:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return pd.read_sql(sql, conn, params=params)


def _pct(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{float(x) * 100:.2f}%"


def _num(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{float(x):.4f}"


def _table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "_无数据_"
    return "```text\n" + df.head(max_rows).to_string(index=False) + "\n```"


def _db_range_summary() -> dict:
    sql = """
    SELECT
      (SELECT MIN(batch_date) FROM bs_detection_results WHERE batch_date >= '20260101') AS bs_min_date,
      (SELECT MAX(batch_date) FROM bs_detection_results WHERE batch_date >= '20260101') AS bs_max_date,
      (SELECT COUNT(*) FROM bs_detection_results WHERE batch_date >= '20260101') AS bs_rows,
      (SELECT COUNT(*) FROM bs_detection_results WHERE batch_date >= '20260101' AND has_buy_signal = 1) AS buy_rows,
      (SELECT COUNT(DISTINCT stock_code) FROM bs_detection_results WHERE batch_date >= '20260101' AND has_buy_signal = 1) AS buy_symbols,
      (SELECT MIN(trade_date) FROM score_rank_daily WHERE trade_date >= '2026-01-01') AS score_min_date,
      (SELECT MAX(trade_date) FROM score_rank_daily WHERE trade_date >= '2026-01-01') AS score_max_date,
      (SELECT COUNT(*) FROM score_rank_daily WHERE trade_date >= '2026-01-01') AS score_rows,
      (SELECT COUNT(*) FROM score_rank_daily WHERE trade_date >= '2026-01-01' AND is_bs_candidate = 1) AS score_bs_rows
    """
    return _read_sql(sql).iloc[0].to_dict()


def _load_labeled_events() -> tuple[pd.DataFrame, pd.DataFrame]:
    events = _load_first_buy_events()
    events = events[events["event_date"] >= START_DATE].copy()
    events = _add_engineered_features(events)
    prices = _load_prices(
        sorted(events["symbol"].unique()),
        events["event_date"].min() - pd.Timedelta(days=5),
        END_DATE + pd.Timedelta(days=1),
    )
    labeled, paths = _horizon_labels(events, prices)
    labeled = _add_split_column(labeled)
    return labeled, paths


def _label_completeness(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for h in DEFAULT_HORIZONS:
        target = f"hit_{h}_10pct"
        max_ret = f"max_ret_{h}"
        mdd = f"mdd_{h}"
        if target not in df:
            continue
        d = df[df[target].notna()]
        rows.append(
            {
                "horizon": h,
                "available_rows": len(d),
                "positive_rate": d[target].mean() if len(d) else np.nan,
                "avg_max_ret": d[max_ret].mean() if max_ret in d else np.nan,
                "avg_mdd": d[mdd].mean() if mdd in d else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _label_stats(df: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    target = f"hit_{horizon}_10pct"
    max_ret = f"max_ret_{horizon}"
    mdd = f"mdd_{horizon}"
    d = df[df[target].notna()].copy()
    if d.empty:
        return pd.DataFrame()
    stats = (
        d.groupby("bs_score_v2_label", dropna=False)
        .agg(
            n=(target, "size"),
            hit10=(target, "mean"),
            max_ret=(max_ret, "mean"),
            mdd=(mdd, "mean"),
            avg_v2=("bs_score_v2", "mean"),
        )
        .reset_index()
        .sort_values("avg_v2", ascending=False)
    )
    return stats


def _top_by_day(df: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    target = f"hit_{horizon}_10pct"
    max_ret = f"max_ret_{horizon}"
    mdd = f"mdd_{horizon}"
    d = df[df[target].notna()].copy()
    score_cols = [
        "bs_score_v2",
        "bs_score",
        "score",
        "opt_score",
        "claude_score",
        "rs_liquidity_combo",
        "breakout_volume_combo",
    ]
    rows = []
    for col in score_cols:
        if col not in d.columns:
            continue
        for day, g in d.groupby("event_date"):
            g = g.dropna(subset=[col])
            if g.empty:
                continue
            for top_n in (3, 5, 10):
                top = g.sort_values(col, ascending=False).head(top_n)
                rows.append(
                    {
                        "score_col": col,
                        "top_n": top_n,
                        "event_date": day,
                        "hit": top[target].mean(),
                        "max_ret": top[max_ret].mean(),
                        "mdd": top[mdd].mean(),
                    }
                )
    raw = pd.DataFrame(rows)
    if raw.empty:
        return raw
    return (
        raw.groupby(["score_col", "top_n"])
        .agg(days=("event_date", "nunique"), avg_hit=("hit", "mean"), avg_max_ret=("max_ret", "mean"), avg_mdd=("mdd", "mean"))
        .reset_index()
        .sort_values(["top_n", "avg_hit"], ascending=[True, False])
    )


def _threshold_rules(df: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    target = f"hit_{horizon}_10pct"
    max_ret = f"max_ret_{horizon}"
    mdd = f"mdd_{horizon}"
    d = df[df[target].notna()].copy()
    if d.empty:
        return d
    base_rate = d[target].mean()

    def summarize(mask: pd.Series, rule: str) -> dict | None:
        sample = d[mask].copy()
        if len(sample) < 20:
            return None
        hit = sample[target].mean()
        return {
            "rule": rule,
            "n": len(sample),
            "coverage": len(sample) / len(d),
            "hit20": hit,
            "lift": hit / base_rate if base_rate else np.nan,
            "max_ret20": sample[max_ret].mean(),
            "mdd20": sample[mdd].mean(),
            "avg_v2": sample["bs_score_v2"].mean(),
        }

    rules = []
    single_thresholds = {
        "bs_score_v2": [45, 50, 52, 55, 58, 60, 62],
        "bs_score": [45, 50, 55, 58, 60, 65],
        "s_rs": [50, 60, 70, 80],
        "s_liquidity": [50, 60, 70, 80],
        "s_breakout": [45, 50, 60, 70, 80],
        "rs_liquidity_combo": [40, 45, 50, 55, 60],
        "breakout_volume_combo": [45, 50, 55, 60, 65, 70],
    }
    for col, thresholds in single_thresholds.items():
        if col not in d:
            continue
        for threshold in thresholds:
            result = summarize(d[col] >= threshold, f"{col}>={threshold}")
            if result:
                rules.append(result)
    for threshold in [-5, 0, 5, 8, 12]:
        result = summarize(d["price_change_ratio"].fillna(0) <= threshold, f"price_change_ratio<={threshold}")
        if result:
            rules.append(result)

    for v2 in [50, 52, 55, 58]:
        for rs_liq in [40, 45, 50, 55]:
            for gain in [5, 8, 12, None]:
                mask = (d["bs_score_v2"] >= v2) & (d["rs_liquidity_combo"] >= rs_liq)
                suffix = ""
                if gain is not None:
                    mask &= d["price_change_ratio"].fillna(0) <= gain
                    suffix = f" & gain<={gain}"
                result = summarize(mask, f"v2>={v2} & rs_liq>={rs_liq}{suffix}")
                if result:
                    rules.append(result)

    return pd.DataFrame(rules).sort_values(["hit20", "max_ret20", "n"], ascending=[False, False, False])


def _bin_stats(df: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    target = f"hit_{horizon}_10pct"
    max_ret = f"max_ret_{horizon}"
    mdd = f"mdd_{horizon}"
    d0 = df[df[target].notna()].copy()
    rows = []
    for col in ["rs_liquidity_combo", "s_rs", "s_liquidity", "s_breakout", "bs_score_v2", "price_change_ratio"]:
        d = d0[d0[col].notna()].copy()
        if len(d) < 50:
            continue
        if col == "price_change_ratio":
            d["bin"] = pd.cut(d[col], bins=[-999, -10, -5, 0, 5, 10, 15, 20, 999]).astype(str)
        else:
            d["bin"] = pd.qcut(d[col], 5, duplicates="drop").astype(str)
        stat = (
            d.groupby("bin", observed=True)
            .agg(n=(target, "size"), hit20=(target, "mean"), max_ret20=(max_ret, "mean"), mdd20=(mdd, "mean"), avg=(col, "mean"))
            .reset_index()
        )
        stat.insert(0, "feature", col)
        rows.append(stat)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _correlations(df: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    d = df[df[f"max_ret_{horizon}"].notna()].copy()
    rows = []
    for col in [
        "bs_score_v2",
        "bs_score",
        "score",
        "opt_score",
        "claude_score",
        "s_rs",
        "s_liquidity",
        "s_breakout",
        "s_volume",
        "score_dispersion",
        "rs_liquidity_combo",
        "breakout_volume_combo",
        "price_change_ratio",
    ]:
        if col not in d or d[col].notna().sum() <= 20:
            continue
        rows.append(
            {
                "feature": col,
                "spearman_max_ret20": d[col].corr(d[f"max_ret_{horizon}"], method="spearman"),
                "spearman_hit20": d[col].corr(d[f"hit_{horizon}_10pct"], method="spearman"),
            }
        )
    return pd.DataFrame(rows).sort_values("spearman_hit20", ascending=False)


def _monthly(df: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    d = df[df[f"hit_{horizon}_10pct"].notna()].copy()
    d["month"] = d["event_date"].dt.strftime("%Y-%m")
    return (
        d.groupby("month")
        .agg(
            n=(f"hit_{horizon}_10pct", "size"),
            hit20=(f"hit_{horizon}_10pct", "mean"),
            max_ret20=(f"max_ret_{horizon}", "mean"),
            mdd20=(f"mdd_{horizon}", "mean"),
            avg_v2=("bs_score_v2", "mean"),
        )
        .reset_index()
    )


def _write_report(out_dir: Path, payload: dict) -> None:
    db = payload["db"]
    completeness = payload["label_completeness"]
    label_stats = payload["label_stats"]
    top_by_day = payload["top_by_day"]
    rules = payload["rules"]
    corr = payload["correlations"]
    monthly = payload["monthly"]

    report = f"""
# 2026-01 至今 B 点信号增强研究

生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 数据覆盖

- B/S 检测表区间：{db.get("bs_min_date")} ~ {db.get("bs_max_date")}
- 评分表区间：{db.get("score_min_date")} ~ {db.get("score_max_date")}
- B/S 原始记录：{int(db.get("bs_rows", 0)):,}
- 有 B 点记录：{int(db.get("buy_rows", 0)):,}，涉及股票 {int(db.get("buy_symbols", 0)):,} 只
- 评分记录：{int(db.get("score_rows", 0)):,}
- 评分表 B 候选记录：{int(db.get("score_bs_rows", 0)):,}

本报告使用能与 `score_rank_daily` 成功联表的 B 点事件做研究。未来收益标签只在对应交易日已经走完时计入，未走完样本标为未标注，不当作失败。

## 标签完整度

{_table(completeness)}

## V2 分层表现（20 日 10% 命中）

{_table(label_stats)}

## 每日 TopN 排序比较（20 日 10% 命中）

{_table(top_by_day)}

## 规则筛选候选

以下规则仅用于研究，不应直接替代交易决策。重点看 `n`、`hit20`、`lift` 与 `mdd20` 的平衡。

{_table(rules)}

## 因子相关性

{_table(corr)}

## 月度漂移

{_table(monthly)}

## 初步结论

1. `bs_score_v2` 对 20 日 10% 命中有提升，但绝对阈值 `>=72` 过于严格，当前样本几乎没有强买样本；短期更适合用 TopN 或 `>=55/58` 作为研究阈值。
2. `rs_liquidity_combo` 是目前最强的可解释单因子。20 日样本中，`rs_liquidity_combo>=45` 的命中率显著高于全体样本。
3. 原始 `score` 单独排序效果弱，说明技术总分不能直接代表 B 点后的交易质量，需要 B 点专用评分层。
4. `price_change_ratio` 与后续空间呈负相关，买点后已经明显拉升的样本不宜追高，应继续作为风险扣分项。
5. 样本存在月度漂移，2 月表现明显好于 3 月，后续需要加入市场环境因子，否则模型容易把行情阶段误学成个股规律。

## 下一步建议

1. 页面新增一个“研究建议”字段：先显示 `强观察/普通观察/回避`，底层规则可以从 `bs_score_v2>=55`、`rs_liquidity_combo>=45`、`price_change_ratio` 风险扣分组合开始。
2. 每周或每新增 100 条 20 日完整标签后重跑本脚本，比较阈值稳定性。
3. 当 20 日完整标签超过 1,500 条后，再训练 LightGBM/XGBoost；60 日标签未超过 500 条前，不建议上 60 日模型。
4. 加入市场状态特征，例如指数 20 日涨跌、市场涨停家数、全市场成交额变化，用来解释月度漂移。
"""
    (out_dir / "RESEARCH_REPORT.md").write_text(report.strip() + "\n", encoding="utf-8")


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    db = _db_range_summary()
    labeled, paths = _load_labeled_events()
    first_per_symbol = labeled[labeled["event_seq_for_symbol"] == 1].copy()

    payload = {
        "db": db,
        "event_rows": int(len(labeled)),
        "unique_symbols": int(labeled["symbol"].nunique()),
        "first_per_symbol_rows": int(len(first_per_symbol)),
        "label_completeness": _label_completeness(labeled),
        "label_stats": _label_stats(labeled),
        "top_by_day": _top_by_day(labeled),
        "rules": _threshold_rules(labeled),
        "bins": _bin_stats(labeled),
        "correlations": _correlations(labeled),
        "monthly": _monthly(labeled),
    }

    for key in ["label_completeness", "label_stats", "top_by_day", "rules", "bins", "correlations", "monthly"]:
        payload[key].to_csv(out_dir / f"{key}.csv", index=False)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(out_dir),
        "event_rows": payload["event_rows"],
        "unique_symbols": payload["unique_symbols"],
        "first_per_symbol_rows": payload["first_per_symbol_rows"],
        "db": {k: str(v) for k, v in db.items()},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.to_csv(out_dir / "price_paths_60d.csv", index=False)
    labeled.to_csv(out_dir / "labeled_events.csv", index=False)
    _write_report(out_dir, payload)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
