from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_model_infer import DEFAULT_MODEL_ROOT, apply_bs_model_scores, latest_model_path
from scoreRank.core.bs_monitoring import portfolio_risk_report


DATASET_ROOT = PROJECT_ROOT / "exports" / "signal_enhancement"
OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
DEFAULT_SCORE_COLS = ["bs_model_rank_score", "bs_consensus_score", "bs_research_score", "bs_score_v2", "score"]


def _latest_dataset_dir() -> Path:
    candidates = sorted([p for p in DATASET_ROOT.glob("20*") if p.is_dir()])
    if not candidates:
        raise FileNotFoundError("No signal enhancement dataset found.")
    return candidates[-1]


def _load_model_bundle(model_dir: Path | None) -> dict | None:
    model_path = None
    if model_dir:
        manifest = model_dir / "model_manifest.json"
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            model_path = Path(str(data.get("model_path", "")))
        if model_path is None or not model_path.exists():
            candidates = sorted(model_dir.glob("*_hit_20_10pct.joblib"))
            model_path = candidates[0] if candidates else None
    else:
        model_path = latest_model_path(DEFAULT_MODEL_ROOT)
    if model_path is None or not model_path.exists():
        return None
    bundle = joblib.load(model_path)
    if not isinstance(bundle, dict):
        return None
    bundle = dict(bundle)
    bundle["model_path"] = str(model_path)
    bundle.setdefault("version", model_path.parent.name)
    return bundle


def build_report(
    dataset_dir: Path,
    horizon: int = 20,
    top_n: int = 10,
    weight_modes: tuple[str, ...] = ("equal", "probability", "risk_adjusted"),
    max_position_weight: float = 0.20,
    max_industry_weight: float = 0.40,
    cost_bps: float = 20.0,
    slippage_bps: float = 15.0,
    capital: float | None = None,
    capacity_ratio: float = 0.02,
    model_dir: Path | None = None,
) -> dict:
    events_path = dataset_dir / "first_buy_events_labeled.csv"
    if not events_path.exists():
        raise FileNotFoundError(events_path)
    events = pd.read_csv(events_path, dtype={"symbol": str}, low_memory=False)
    model_bundle = _load_model_bundle(model_dir)
    if model_bundle:
        events = apply_bs_model_scores(events, model_bundle)
    score_cols = [c for c in DEFAULT_SCORE_COLS if c in events.columns]
    reports = []
    for score_col in score_cols:
        for mode in weight_modes:
            item = portfolio_risk_report(
                events,
                score_col=score_col,
                horizon=horizon,
                top_n=top_n,
                weight_mode=mode,
                max_position_weight=max_position_weight,
                max_industry_weight=max_industry_weight,
                cost_bps=cost_bps,
                slippage_bps=slippage_bps,
                capital=capital,
                max_avg_amount_ratio=capacity_ratio,
            )
            item.pop("portfolios", None)
            reports.append(item)
    ranked = sorted(reports, key=lambda x: (x.get("avg_net_ret") is not None, x.get("avg_net_ret") or -999), reverse=True)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "horizon": horizon,
        "top_n": top_n,
        "max_position_weight": max_position_weight,
        "max_industry_weight": max_industry_weight,
        "cost_bps": cost_bps,
        "slippage_bps": slippage_bps,
        "capital": capital,
        "capacity_ratio": capacity_ratio,
        "model_dir": str(Path(str(model_bundle["model_path"])).parent) if model_bundle and model_bundle.get("model_path") else None,
        "model_kind": model_bundle.get("model_kind") if model_bundle else None,
        "reports": ranked,
    }


def _write_report(report: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "bs_portfolio_risk_report.json"
    md_path = out_dir / "bs_portfolio_risk_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = pd.DataFrame(report.get("reports", []))
    lines = [
        "# B点组合风控评估",
        "",
        f"- 数据目录：`{report['dataset_dir']}`",
        f"- Horizon：{report['horizon']}",
        f"- TopN：{report['top_n']}",
        "",
        rows.to_markdown(index=False) if not rows.empty else "_无可用组合评估_",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(md_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate B-signal portfolio-level risk constraints.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--max-position-weight", type=float, default=0.20)
    parser.add_argument("--max-industry-weight", type=float, default=0.40)
    parser.add_argument("--cost-bps", type=float, default=20.0)
    parser.add_argument("--slippage-bps", type=float, default=15.0)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--capacity-ratio", type=float, default=0.02)
    parser.add_argument("--model-dir", type=Path, default=None, help="Optional trained model directory used to score historical events.")
    parser.add_argument("--write", action="store_true", help="Write JSON and Markdown report under exports/signal_research.")
    args = parser.parse_args()
    dataset_dir = args.dataset_dir or _latest_dataset_dir()
    report = build_report(
        dataset_dir,
        horizon=args.horizon,
        top_n=args.top_n,
        max_position_weight=args.max_position_weight,
        max_industry_weight=args.max_industry_weight,
        cost_bps=args.cost_bps,
        slippage_bps=args.slippage_bps,
        capital=args.capital,
        capacity_ratio=args.capacity_ratio,
        model_dir=args.model_dir,
    )
    if args.write:
        stamp_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_portfolio")
        report["files"] = _write_report(report, stamp_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
