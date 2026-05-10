from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scripts.export_signal_enhancement_dataset import DB_CONFIG
from scoreRank.core.bs_enhanced_score import calculate_bs_consensus_signal


MODEL_ROOT = PROJECT_ROOT / "exports" / "bs_signal_models"


def _latest_model_dir() -> Path:
    candidates = sorted([p for p in MODEL_ROOT.glob("20*") if p.is_dir()])
    if not candidates:
        raise FileNotFoundError("No model output directory found.")
    return candidates[-1]


def _normalize_symbol(value) -> str:
    s = str(value or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _ensure_columns(cursor) -> None:
    additions = {
        "bs_model_prob": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_prob DECIMAL(10,6) NULL COMMENT 'B点模型20日命中概率' AFTER bs_research_reason",
        "bs_model_rank_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_rank_score DECIMAL(10,4) NULL COMMENT 'B点模型综合排序分' AFTER bs_model_prob",
        "bs_model_version": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_version VARCHAR(32) NULL COMMENT 'B点模型版本' AFTER bs_model_rank_score",
        "bs_consensus_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_score DECIMAL(10,2) NULL COMMENT 'B点综合建议分' AFTER bs_model_version",
        "bs_consensus_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_label VARCHAR(16) NULL COMMENT 'B点综合建议标签' AFTER bs_consensus_score",
        "bs_consensus_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_reason VARCHAR(128) NULL COMMENT 'B点综合建议原因' AFTER bs_consensus_label",
    }
    cursor.execute("SHOW COLUMNS FROM score_rank_daily")
    existing = {row[0] for row in cursor.fetchall()}
    for col, ddl in additions.items():
        if col not in existing:
            cursor.execute(ddl)


def import_scores(model_dir: Path) -> dict:
    scored_path = model_dir / "latest_candidates_scored.csv"
    metrics_path = model_dir / "metrics.json"
    if not scored_path.exists():
        raise FileNotFoundError(scored_path)
    df = pd.read_csv(scored_path, dtype={"symbol": str})
    if df.empty:
        raise ValueError(f"No rows in {scored_path}")
    if "p_signal" not in df.columns or "model_rank_score" not in df.columns:
        raise ValueError("latest_candidates_scored.csv must contain p_signal and model_rank_score")

    if "asof_date" in df.columns:
        asof_date = pd.to_datetime(df["asof_date"].dropna().iloc[0]).date()
    else:
        raise ValueError("latest_candidates_scored.csv must contain asof_date")

    version = model_dir.name
    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        version = Path(metrics.get("summary", {}).get("output_dir", model_dir)).name

    df["symbol"] = df["symbol"].map(_normalize_symbol)
    df = df[df["symbol"] != ""].drop_duplicates(subset=["symbol"]).copy()
    updates = []
    for row in df.to_dict("records"):
        if pd.isna(row.get("p_signal")) or pd.isna(row.get("model_rank_score")):
            continue
        row["bs_model_prob"] = float(row["p_signal"])
        consensus = calculate_bs_consensus_signal(row)
        updates.append(
            (
                round(float(row["p_signal"]), 6),
                round(float(row["model_rank_score"]), 4),
                version,
                consensus["bs_consensus_score"],
                consensus["bs_consensus_label"],
                consensus["bs_consensus_reason"],
                asof_date,
                row["symbol"],
            )
        )

    with pymysql.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            _ensure_columns(cursor)
            cursor.executemany(
                """
                UPDATE score_rank_daily
                SET bs_model_prob=%s,
                    bs_model_rank_score=%s,
                    bs_model_version=%s,
                    bs_consensus_score=%s,
                    bs_consensus_label=%s,
                    bs_consensus_reason=%s
                WHERE trade_date=%s
                  AND symbol=%s
                """,
                updates,
            )
        conn.commit()

    return {
        "model_dir": str(model_dir),
        "asof_date": str(asof_date),
        "model_version": version,
        "candidate_rows": int(len(df)),
        "updated_rows": int(len(updates)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import latest B-signal model scores into score_rank_daily.")
    parser.add_argument("--model-dir", type=Path, default=None)
    args = parser.parse_args()
    result = import_scores(args.model_dir or _latest_model_dir())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
