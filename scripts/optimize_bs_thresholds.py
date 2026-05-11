from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

DATASET_ROOT = PROJECT_ROOT / "exports" / "signal_enhancement"
OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


def _latest_dataset_dir() -> Path:
    candidates = sorted([p for p in DATASET_ROOT.glob("20*") if p.is_dir()])
    if not candidates:
        raise FileNotFoundError("No signal enhancement dataset found.")
    return candidates[-1]


def _safe_numeric(frame: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)


def assign_pool_by_thresholds(
    frame: pd.DataFrame,
    consensus_trade: float,
    consensus_watch: float,
    model_trade: float,
    model_watch: float,
    v2_trade: float,
    v2_watch: float,
) -> pd.Series:
    is_bs = _safe_numeric(frame, "is_bs_candidate", 1.0).astype(int) == 1
    gate = frame.get("bs_gate_label", pd.Series("", index=frame.index)).fillna("").astype(str)
    gate_ok = gate.ne("过滤")
    consensus = _safe_numeric(frame, "bs_consensus_score")
    model_rank = _safe_numeric(frame, "bs_model_rank_score")
    v2 = _safe_numeric(frame, "bs_score_v2")

    trade = is_bs & gate_ok & (((consensus >= consensus_trade) & (model_rank >= model_trade)) | (v2 >= v2_trade))
    watch = (
        is_bs
        & gate_ok
        & ~trade
        & (((consensus >= consensus_watch) & (model_rank >= model_watch)) | (v2 >= v2_watch))
    )
    out = pd.Series("FILTER", index=frame.index, dtype=object)
    out.loc[watch] = "WATCH"
    out.loc[trade] = "TRADE"
    return out


def evaluate_threshold_candidate(
    frame: pd.DataFrame,
    thresholds: dict[str, float],
    horizon: int = 20,
    min_trade_rows: int = 20,
) -> dict:
    target = f"hit_{horizon}_10pct"
    max_ret_col = f"max_ret_{horizon}"
    mdd_col = f"mdd_{horizon}"
    ret_col = f"ret_{horizon}"
    if target not in frame.columns:
        raise ValueError(f"Missing target column: {target}")

    d = frame[frame[target].notna()].copy()
    if d.empty:
        raise ValueError(f"No labeled rows for {target}")
    pool = assign_pool_by_thresholds(d, **thresholds)
    trade = d[pool == "TRADE"]
    watch = d[pool == "WATCH"]

    trade_hit = float(trade[target].mean()) if not trade.empty else 0.0
    watch_hit = float(watch[target].mean()) if not watch.empty else 0.0
    avg_max_ret = float(pd.to_numeric(trade.get(max_ret_col), errors="coerce").mean()) if max_ret_col in trade else np.nan
    avg_ret = float(pd.to_numeric(trade.get(ret_col), errors="coerce").mean()) if ret_col in trade else np.nan
    avg_mdd = float(pd.to_numeric(trade.get(mdd_col), errors="coerce").mean()) if mdd_col in trade else np.nan
    coverage = float(len(trade) / len(d)) if len(d) else 0.0

    too_sparse_penalty = max(0, min_trade_rows - len(trade)) / max(1, min_trade_rows)
    objective = (
        trade_hit * 100.0
        + (0.0 if np.isnan(avg_max_ret) else avg_max_ret * 80.0)
        + (0.0 if np.isnan(avg_ret) else avg_ret * 60.0)
        + (0.0 if np.isnan(avg_mdd) else avg_mdd * 50.0)
        + min(coverage, 0.20) * 20.0
        - too_sparse_penalty * 25.0
    )
    return {
        **thresholds,
        "rows": int(len(d)),
        "trade_rows": int(len(trade)),
        "watch_rows": int(len(watch)),
        "filter_rows": int((pool == "FILTER").sum()),
        "trade_hit_rate": round(trade_hit, 6),
        "watch_hit_rate": round(watch_hit, 6),
        "trade_avg_ret": round(avg_ret, 6) if not np.isnan(avg_ret) else None,
        "trade_avg_max_ret": round(avg_max_ret, 6) if not np.isnan(avg_max_ret) else None,
        "trade_avg_mdd": round(avg_mdd, 6) if not np.isnan(avg_mdd) else None,
        "trade_coverage": round(coverage, 6),
        "objective": round(float(objective), 6),
    }


def optimize_thresholds(
    frame: pd.DataFrame,
    horizon: int = 20,
    top_k: int = 20,
    min_trade_rows: int = 20,
) -> dict:
    grids = {
        "consensus_trade": [62.0, 66.0, 70.0, 74.0],
        "consensus_watch": [50.0, 54.0, 58.0],
        "model_trade": [56.0, 62.0, 68.0],
        "model_watch": [46.0, 52.0, 58.0],
        "v2_trade": [66.0, 70.0, 74.0, 78.0],
        "v2_watch": [50.0, 54.0, 58.0, 62.0],
    }
    rows = []
    keys = list(grids)
    for values in product(*(grids[k] for k in keys)):
        candidate = dict(zip(keys, values))
        if candidate["consensus_watch"] >= candidate["consensus_trade"]:
            continue
        if candidate["model_watch"] >= candidate["model_trade"]:
            continue
        if candidate["v2_watch"] >= candidate["v2_trade"]:
            continue
        rows.append(evaluate_threshold_candidate(frame, candidate, horizon=horizon, min_trade_rows=min_trade_rows))
    ranked = sorted(rows, key=lambda x: x["objective"], reverse=True)
    return {
        "horizon": horizon,
        "candidates": len(rows),
        "best": ranked[0] if ranked else None,
        "top": ranked[:top_k],
    }


def build_report(dataset_dir: Path, horizon: int = 20, top_k: int = 20, min_trade_rows: int = 20) -> dict:
    events_path = dataset_dir / "first_buy_events_labeled.csv"
    if not events_path.exists():
        raise FileNotFoundError(events_path)
    events = pd.read_csv(events_path, dtype={"symbol": str})
    result = optimize_thresholds(events, horizon=horizon, top_k=top_k, min_trade_rows=min_trade_rows)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        **result,
    }


def _write_report(report: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "bs_threshold_optimization.json"
    md_path = out_dir / "bs_threshold_optimization.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = pd.DataFrame(report.get("top", []))
    lines = [
        "# B点阈值自动调优报告",
        "",
        f"- 数据目录：`{report['dataset_dir']}`",
        f"- Horizon：{report['horizon']}",
        f"- 候选组合：{report['candidates']}",
        "",
        "## 最优组合",
        "",
        "```json",
        json.dumps(report.get("best", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Top 候选",
        "",
        rows.to_markdown(index=False) if not rows.empty else "_无可用候选_",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(md_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid-search B-signal pool thresholds on labeled events.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--min-trade-rows", type=int, default=20)
    parser.add_argument("--write", action="store_true", help="Write JSON and Markdown report under exports/signal_research.")
    args = parser.parse_args()
    dataset_dir = args.dataset_dir or _latest_dataset_dir()
    report = build_report(dataset_dir, horizon=args.horizon, top_k=args.top_k, min_trade_rows=args.min_trade_rows)
    if args.write:
        stamp_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_thresholds")
        report["files"] = _write_report(report, stamp_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
