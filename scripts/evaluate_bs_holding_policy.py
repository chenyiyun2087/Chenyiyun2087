from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
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


def _num(frame: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)


def assign_holding_actions(frame: pd.DataFrame) -> pd.Series:
    gate = frame.get("bs_gate_label", pd.Series("", index=frame.index)).fillna("").astype(str)
    consensus = _num(frame, "bs_consensus_score")
    model_rank = _num(frame, "bs_model_rank_score")
    ret_3 = _num(frame, "ret_3")
    ret_5 = _num(frame, "ret_5")
    mdd_10 = _num(frame, "mdd_10")
    hit_5 = _num(frame, "hit_5_10pct")

    exit_rule = gate.eq("过滤") | (mdd_10 <= -0.06) | (ret_5 <= -0.03)
    add_rule = ~exit_rule & ((hit_5 >= 1.0) | ((consensus >= 60.0) & (model_rank >= 55.0) & (ret_3 >= 0.0)))
    reduce_rule = ~exit_rule & ~add_rule & ((mdd_10 <= -0.04) | ((consensus < 48.0) & (model_rank < 48.0)))

    out = pd.Series("HOLD", index=frame.index, dtype=object)
    out.loc[reduce_rule] = "REDUCE"
    out.loc[add_rule] = "ADD"
    out.loc[exit_rule] = "EXIT"
    return out


def evaluate_holding_policy(frame: pd.DataFrame, horizon: int = 20) -> dict:
    if frame.empty:
        return {"rows": 0, "actions": []}
    d = frame.copy()
    d["action"] = assign_holding_actions(d)
    ret_col = f"ret_{horizon}"
    max_ret_col = f"max_ret_{horizon}"
    mdd_col = f"mdd_{horizon}"
    hit_col = f"hit_{horizon}_10pct"

    rows = []
    for action, group in d.groupby("action"):
        item = {
            "action": action,
            "rows": int(len(group)),
            "share": round(float(len(group) / len(d)), 6) if len(d) else 0.0,
        }
        if hit_col in group:
            item["hit_rate"] = round(float(pd.to_numeric(group[hit_col], errors="coerce").mean()), 6)
        if ret_col in group:
            item["avg_ret"] = round(float(pd.to_numeric(group[ret_col], errors="coerce").mean()), 6)
        if max_ret_col in group:
            item["avg_max_ret"] = round(float(pd.to_numeric(group[max_ret_col], errors="coerce").mean()), 6)
        if mdd_col in group:
            item["avg_mdd"] = round(float(pd.to_numeric(group[mdd_col], errors="coerce").mean()), 6)
        rows.append(item)

    action_order = {"ADD": 0, "HOLD": 1, "REDUCE": 2, "EXIT": 3}
    rows.sort(key=lambda x: action_order.get(str(x["action"]), 99))
    requested_metrics = [hit_col, ret_col, max_ret_col, mdd_col]
    return {
        "rows": int(len(d)),
        "horizon": horizon,
        "available_metric_columns": [c for c in requested_metrics if c in d.columns],
        "missing_metric_columns": [c for c in requested_metrics if c not in d.columns],
        "actions": rows,
        "action_counts": d["action"].value_counts().to_dict(),
    }


def build_report(dataset_dir: Path, horizon: int = 20) -> dict:
    panel_path = dataset_dir / "active_b_daily_panel_labeled.csv"
    if not panel_path.exists():
        raise FileNotFoundError(panel_path)
    panel = pd.read_csv(panel_path, dtype={"symbol": str}, low_memory=False)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        **evaluate_holding_policy(panel, horizon=horizon),
    }


def _write_report(report: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "bs_holding_policy_report.json"
    md_path = out_dir / "bs_holding_policy_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = pd.DataFrame(report.get("actions", []))
    lines = [
        "# B点持仓/加减仓原型评估",
        "",
        f"- 数据目录：`{report['dataset_dir']}`",
        f"- Horizon：{report['horizon']}",
        f"- 样本行数：{report['rows']}",
        "",
        rows.to_markdown(index=False) if not rows.empty else "_无可用动作评估_",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(md_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate prototype B-signal holding/add/reduce policy.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--write", action="store_true", help="Write JSON and Markdown report under exports/signal_research.")
    args = parser.parse_args()
    dataset_dir = args.dataset_dir or _latest_dataset_dir()
    report = build_report(dataset_dir, horizon=args.horizon)
    if args.write:
        stamp_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_holding")
        report["files"] = _write_report(report, stamp_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
