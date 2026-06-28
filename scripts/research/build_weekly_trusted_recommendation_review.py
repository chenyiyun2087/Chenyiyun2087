"""Build a weekly, read-only review of trusted production recommendations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


def load_latest(engine, start: str, end: str, strategy: str) -> pd.DataFrame:
    return pd.read_sql(
        text(
            """
            SELECT c.trade_date AS signal_date, c.rank_no, c.symbol,
                   c.stock_name AS name, c.industry, c.rank_score,
                   c.effective_weight, c.bs_score_v2, c.is_bs_candidate,
                   c.index_bucket, c.market_liquidity_bucket
            FROM chenyiyun.ads_trusted_strategy_candidates c
            JOIN (
              SELECT trade_date, strategy, MAX(signal_time) latest_signal_time
              FROM chenyiyun.ads_trusted_strategy_candidates
              WHERE trade_date BETWEEN :start AND :end AND strategy=:strategy
              GROUP BY trade_date, strategy
            ) x ON x.trade_date=c.trade_date AND x.strategy=c.strategy
               AND x.latest_signal_time=c.signal_time
            WHERE c.strategy=:strategy
            ORDER BY c.trade_date, c.rank_no
            """
        ),
        engine,
        params={"start": start, "end": end, "strategy": strategy},
    )


def classify(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    bs = pd.to_numeric(out["bs_score_v2"], errors="coerce")
    out["review_status"] = "观察"
    out.loc[bs >= 65, "review_status"] = "可用"
    out.loc[bs < 60, "review_status"] = "剔除"
    out.loc[bs.isna(), "review_status"] = "观察"
    counts = out.groupby(["signal_date", "industry"])["symbol"].transform("count")
    out["risk_tags"] = ""
    out.loc[counts >= 3, "risk_tags"] = "行业集中"
    out.loc[out["review_status"] == "剔除", "risk_tags"] = (
        out.loc[out["review_status"] == "剔除", "risk_tags"].str.cat(
            pd.Series("BS门禁偏弱", index=out.index), sep=";"
        ).str.strip(";")
    )
    duplicate_counts = out.groupby("symbol")["signal_date"].transform("nunique")
    out.loc[duplicate_counts > 1, "risk_tags"] = (
        out.loc[duplicate_counts > 1, "risk_tags"] + ";周内重复推荐"
    ).str.strip(";")
    return out


def render_markdown(frame: pd.DataFrame, start: str, end: str, strategy: str) -> str:
    unique = frame["symbol"].nunique() if not frame.empty else 0
    status_counts = frame["review_status"].value_counts().to_dict() if not frame.empty else {}
    industries = frame["industry"].value_counts().to_dict() if not frame.empty else {}
    lines = [
        "# 本周生产可信策略股票清单",
        "",
        f"- 周期：{start} 至 {end}",
        f"- 策略：`{strategy}`",
        f"- 推荐记录：{len(frame)}；去重股票：{unique}",
        f"- 复核结论：可用 {status_counts.get('可用', 0)} / 观察 {status_counts.get('观察', 0)} / 剔除 {status_counts.get('剔除', 0)}",
        f"- 行业分布：{industries}",
        "- 说明：结论为研究复核，不是订单；BS 分数 ≥65 为可用、60–65 为观察、<60 为剔除，仍需执行日前检查停牌、涨跌停与公告。",
        "",
        "|信号日|排名|代码|名称|行业|排名分|权重|BS分|结论|风险标签|",
        "|---|---:|---|---|---|---:|---:|---:|---|---|",
    ]
    for row in frame.itertuples(index=False):
        lines.append(
            f"|{row.signal_date}|{int(row.rank_no)}|{row.symbol}|{row.name}|{row.industry}|"
            f"{row.rank_score:.2f}|{row.effective_weight:.2%}|{row.bs_score_v2:.2f}|"
            f"{row.review_status}|{row.risk_tags or '-'}|"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--strategy", default="production_governed_vol_position")
    parser.add_argument("--output-dir", default="exports/weekly_trusted_recommendations")
    args = parser.parse_args()
    frame = classify(load_latest(create_engine(build_sqlalchemy_url()), args.start, args.end, args.strategy))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}_trusted_recommendations"
    csv_path = out / f"{stem}.csv"
    md_path = out / f"{stem}.md"
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    md_path.write_text(render_markdown(frame, args.start, args.end, args.strategy), encoding="utf-8")
    print(md_path)
    print(csv_path)


if __name__ == "__main__":
    main()
