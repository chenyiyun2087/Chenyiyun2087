from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_monitoring import cost_sensitive_topn_report, shadow_pool_overlap, topn_rank_report


DATASET_ROOT = PROJECT_ROOT / "exports" / "signal_enhancement"
OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
DEFAULT_SCORE_COLS = [
    "bs_score_v2",
    "bs_research_score",
    "bs_model_rank_score",
    "bs_consensus_score",
    "score",
]


def _latest_dataset_dir() -> Path:
    candidates = sorted([p for p in DATASET_ROOT.glob("20*") if p.is_dir()])
    if not candidates:
        raise FileNotFoundError("No signal enhancement dataset found.")
    return candidates[-1]


def build_ranker_report(
    dataset_dir: Path,
    horizon: int = 20,
    cost_bps: float = 20.0,
    slippage_bps: float = 15.0,
    capital_per_trade: float | None = None,
    capacity_ratio: float = 0.02,
) -> dict:
    events_path = dataset_dir / "first_buy_events_labeled.csv"
    latest_path = dataset_dir / "latest_b_candidates.csv"
    if not events_path.exists():
        raise FileNotFoundError(events_path)
    events = pd.read_csv(events_path, dtype={"symbol": str})
    latest = pd.read_csv(latest_path, dtype={"symbol": str}) if latest_path.exists() else pd.DataFrame()
    score_cols = [c for c in DEFAULT_SCORE_COLS if c in events.columns]
    topn = topn_rank_report(events, score_cols=score_cols, horizon=horizon)
    cost_topn = cost_sensitive_topn_report(
        events,
        score_cols=score_cols,
        horizon=horizon,
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
        capital_per_trade=capital_per_trade,
        max_avg_amount_ratio=capacity_ratio,
    )
    return {
        "dataset_dir": str(dataset_dir),
        "horizon": horizon,
        "cost_bps": cost_bps,
        "slippage_bps": slippage_bps,
        "capital_per_trade": capital_per_trade,
        "capacity_ratio": capacity_ratio,
        "score_cols": score_cols,
        "rows": int(len(events)),
        "topn": topn.to_dict("records"),
        "cost_sensitive_topn": cost_topn.to_dict("records"),
        "shadow_pool_overlap_latest": shadow_pool_overlap(latest),
    }


def _write_report(report: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "bs_ranker_topn_report.json"
    md_path = out_dir / "bs_ranker_topn_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = pd.DataFrame(report.get("topn", []))
    lines = [
        "# B点排序 TopN 验证报告",
        "",
        f"- 数据目录：`{report['dataset_dir']}`",
        f"- Horizon：{report['horizon']}",
        f"- 样本行数：{report['rows']}",
        "",
        "## TopN",
        "",
        rows.to_markdown(index=False) if not rows.empty else "_无可用 TopN 数据_",
        "",
        "## 成本/容量敏感 TopN",
        "",
        pd.DataFrame(report.get("cost_sensitive_topn", [])).to_markdown(index=False)
        if report.get("cost_sensitive_topn")
        else "_无可用成本敏感 TopN 数据_",
        "",
        "## 影子池重叠",
        "",
        "```json",
        json.dumps(report.get("shadow_pool_overlap_latest", {}), ensure_ascii=False, indent=2),
        "```",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(md_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate B-signal ranker TopN performance.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--cost-bps", type=float, default=20.0, help="One-way commission/tax cost in basis points.")
    parser.add_argument("--slippage-bps", type=float, default=15.0, help="One-way slippage in basis points.")
    parser.add_argument("--capital-per-trade", type=float, default=None, help="Optional capital size used for capacity filter.")
    parser.add_argument("--capacity-ratio", type=float, default=0.02, help="Max share of avg amount allowed per trade.")
    parser.add_argument("--write", action="store_true", help="Write JSON and Markdown report under exports/signal_research.")
    args = parser.parse_args()
    dataset_dir = args.dataset_dir or _latest_dataset_dir()
    report = build_ranker_report(
        dataset_dir,
        horizon=args.horizon,
        cost_bps=args.cost_bps,
        slippage_bps=args.slippage_bps,
        capital_per_trade=args.capital_per_trade,
        capacity_ratio=args.capacity_ratio,
    )
    if args.write:
        stamp_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_rankers")
        report["files"] = _write_report(report, stamp_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
