"""Classify strict post-event risk labels before any cap parameter research."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd

STRICT = "production_governed_vol_position_v1_2b_strict_precommit_uplift"

def run(trades_path: Path, output_dir: Path) -> dict:
    d = pd.read_csv(trades_path); d = d[(d["strategy"].eq(STRICT)) & (pd.to_numeric(d.get("risk_event_triggered"), errors="coerce").fillna(0).eq(1))].copy()
    def classify(row):
        reason, level = str(row.get("reject_reason") or ""), str(row.get("precommit_uplift_risk_level") or "")
        if reason in {"t1_not_tradable", "limit_block", "missing_t1_execution_price"}: return "D_execution_block"
        if level in {"no_incremental_uplift", "no_signal"}: return "B_no_incremental_uplift"
        if level in {"high", "extreme", "data_missing_fallback_to_v1"}: return "E_posthoc_tail_or_already_covered"
        return "A_preventable_by_cap"
    d["root_cause"] = d.apply(classify, axis=1)
    d["preventable_by_cap"] = d["root_cause"].eq("A_preventable_by_cap")
    keep=[c for c in ("symbol","signal_date","execution_date","trade_date","risk_event_types","precommit_uplift_risk_level","planned_shares","filled_shares","filled_price","reject_reason","root_cause","preventable_by_cap") if c in d]
    d=d[keep]; output_dir.mkdir(parents=True, exist_ok=True); d.to_csv(output_dir / "strict_missed_risk_events.csv", index=False)
    result={"event_count":int(len(d)),"preventable_by_cap_count":int(d["preventable_by_cap"].sum()) if not d.empty else 0,"output":str(output_dir / "strict_missed_risk_events.csv")}
    (output_dir / "strict_missed_risk_events_report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); return result
if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--trades",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);args=p.parse_args();print(json.dumps(run(args.trades,args.output_dir),ensure_ascii=False,indent=2))
