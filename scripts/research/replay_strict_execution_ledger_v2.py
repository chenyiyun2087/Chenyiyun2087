"""Independent per-order execution audit for strict-ledger exports.

No imports from the simulator or ``ExecutionLedger`` are allowed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _n(value, default=0.0):
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) else default


def _strict(frame: pd.DataFrame) -> pd.DataFrame:
    if "strategy" not in frame:
        return frame
    return frame[frame["strategy"].astype(str).str.contains("strict_precommit", na=False)].copy()

def _limit_ratio(symbol, is_st):
    code=str(symbol).zfill(6)
    if bool(_n(is_st)): return .05
    if code.startswith(("300","301","688","689")): return .20
    if code.startswith(("4","8","9")): return .30
    return .10

def _gate(snap):
    op, prev = _n(getattr(snap,"raw_open"), float("nan")), _n(getattr(snap,"prev_raw_close"), float("nan"))
    if not bool(_n(getattr(snap,"execution_tradable"))) or bool(_n(getattr(snap,"is_suspended"))) or not bool(_n(getattr(snap,"is_listed"))): return False, "t1_not_tradable"
    if not pd.notna(op) or op<=0 or not pd.notna(prev) or prev<=0: return False,"missing_t1_execution_price"
    ratio=_limit_ratio(getattr(snap,"symbol"),getattr(snap,"is_st")); side=str(getattr(snap,"side")); tick=_n(getattr(snap,"price_tick",.01),.01)
    upper=round(prev*(1+ratio)/tick)*tick; lower=round(prev*(1-ratio)/tick)*tick
    if side=="BUY" and op>=upper: return False,"limit_block"
    if side=="SELL" and op<=lower: return False,"limit_block"
    return True,""


def audit(events_path: Path, snapshot_path: Path, output_dir: Path, tolerance: float = 0.01) -> dict:
    events, snapshots = _strict(pd.read_csv(events_path).fillna("")), _strict(pd.read_csv(snapshot_path).fillna(""))
    required = {"order_id", "event_timestamp", "order_status", "filled_shares", "filled_notional", "fee", "mark_price_basis"}
    if missing := sorted(required - set(events.columns)):
        raise RuntimeError(f"events missing: {missing}")
    if missing := sorted({"order_id", "raw_open", "prev_raw_close", "execution_tradable", "is_suspended", "is_listed", "is_st", "side", "symbol", "cost_rate"} - set(snapshots.columns)):
        raise RuntimeError(f"execution snapshot missing: {missing}")
    snapshot_by_order = {str(row.order_id): row for row in snapshots.itertuples()}
    findings, violations = [], []
    events = events[events["event_type"].eq("order") & events["order_id"].astype(str).ne("")].copy()
    for order_id, rows in events.groupby("order_id", dropna=True):
        order_id = str(order_id)
        if not order_id:
            continue
        snap = snapshot_by_order.get(order_id)
        if snap is None:
            violations.append(f"missing_snapshot:{order_id}"); continue
        gate_pass, gate_reason = _gate(snap)
        ordered = rows.sort_values("event_timestamp")
        statuses = ordered["order_status"].astype(str).tolist()
        times = pd.to_datetime(ordered["event_timestamp"], errors="coerce")
        if times.isna().any() or not times.is_monotonic_increasing:
            violations.append(f"timestamp_order:{order_id}")
        plan = ordered[ordered["order_status"].eq("PLANNED")]
        fills = ordered[ordered["order_status"].isin(["FILLED", "PARTIAL_FILL"])]
        rejects = ordered[ordered["order_status"].isin(["REJECTED_T1_NOT_TRADABLE", "REJECTED_LIMIT_BLOCK"])]
        cancels = ordered[ordered["order_status"].eq("CANCELLED_T1_CLOSE")]
        price_mismatch = fee_mismatch = gate_mismatch = 0
        for _, fill in fills.iterrows():
            expected_notional = _n(fill.get("filled_shares")) * _n(getattr(snap, "raw_open"))
            if abs(_n(fill.get("filled_notional")) - expected_notional) > tolerance:
                price_mismatch += 1
            expected_fee = _n(fill.get("filled_notional")) * _n(getattr(snap, "cost_rate"))
            if abs(_n(fill.get("fee")) - expected_fee) > tolerance:
                fee_mismatch += 1
            if not gate_pass:
                gate_mismatch += 1
        for _, reject in rejects.iterrows():
            if _n(reject.get("filled_shares")) != 0 or gate_pass:
                gate_mismatch += 1
        planned = int(_n(plan["planned_shares"].iloc[0])) if not plan.empty else 0
        filled = int(sum(_n(row.get("filled_shares")) for _, row in fills.iterrows()))
        cancelled = int(sum(_n(row.get("cancelled_shares")) for _, row in cancels.iterrows()))
        conservation_ok = planned == filled + cancelled
        if not conservation_ok: violations.append(f"order_conservation:{order_id}")
        if price_mismatch: violations.append(f"price_mismatch:{order_id}")
        if fee_mismatch: violations.append(f"fee_mismatch:{order_id}")
        if gate_mismatch: violations.append(f"gate_mismatch:{order_id}")
        if any(str(row.get("mark_price_basis")) != "raw" for _, row in ordered.iterrows()):
            violations.append(f"non_raw_basis:{order_id}")
        findings.append({"order_id": order_id, "planned_shares": planned, "filled_shares": filled, "cancelled_shares": cancelled,
                         "conservation_ok": conservation_ok, "price_mismatch_count": price_mismatch,
                         "fee_mismatch_count": fee_mismatch, "gate_mismatch_count": gate_mismatch,
                         "statuses": ";".join(statuses), "independent_gate_reason": gate_reason})
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(findings).to_csv(output_dir / "strict_execution_replay_orders.csv", index=False)
    report = {"execution_replay_pass": not violations, "order_count": len(findings), "violation_count": len(violations),
              "violations": sorted(set(violations)), "price_mismatch_count": sum(row["price_mismatch_count"] for row in findings),
              "fee_mismatch_count": sum(row["fee_mismatch_count"] for row in findings), "gate_mismatch_count": sum(row["gate_mismatch_count"] for row in findings),
              "timestamp_order_violation_count": sum(item.startswith("timestamp_order") for item in violations),
              "output": str(output_dir / "strict_execution_replay_orders.csv")}
    (output_dir / "strict_execution_replay_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Independently audit strict T+1 executions.")
    parser.add_argument("--ledger-events", type=Path, required=True); parser.add_argument("--execution-snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--tolerance", type=float, default=0.01)
    args = parser.parse_args()
    print(json.dumps(audit(args.ledger_events, args.execution_snapshot, args.output_dir, args.tolerance), ensure_ascii=False, indent=2))
