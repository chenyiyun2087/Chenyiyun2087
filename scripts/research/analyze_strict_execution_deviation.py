"""Attribute strict fixed-share cash residual and weight drift without tuning strategy parameters."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd

STRICT = "production_governed_vol_position_v1_2b_strict_precommit_uplift"


def run(trades_path: Path, output_dir: Path) -> dict:
    trades = pd.read_csv(trades_path)
    d = trades[trades["strategy"].eq(STRICT)].copy()
    for column in ("planned_notional", "gross_amount", "planned_shares", "filled_shares", "planned_price", "filled_price"):
        d[column] = pd.to_numeric(d.get(column), errors="coerce").fillna(0.0)
    d["lot_rounding_residual"] = (d["planned_shares"] - d["planned_shares"].round()).abs() * d["planned_price"]
    d["price_buffer_residual"] = (d["planned_price"] - d["filled_price"]).clip(lower=0) * d["filled_shares"]
    d["t1_reject_residual"] = d["planned_notional"].where(d.get("reject_reason", "").eq("t1_not_tradable"), 0.0)
    d["limit_block_residual"] = d["planned_notional"].where(d.get("reject_reason", "").eq("limit_block"), 0.0)
    d["partial_fill_residual"] = (d["planned_notional"] - d["gross_amount"]).clip(lower=0).where(d.get("order_status", "").eq("PARTIAL_FILL"), 0.0)
    d["unexpected_execution_residual"] = d[["t1_reject_residual", "limit_block_residual", "partial_fill_residual"]].sum(axis=1)
    d["portfolio_weight_drift_bps"] = pd.to_numeric(d.get("open_weight_drift_bps"), errors="coerce")
    output_dir.mkdir(parents=True, exist_ok=True)
    d.to_csv(output_dir / "strict_execution_deviation_orders.csv", index=False)
    result = {"order_count": int(len(d)), "unexpected_execution_residual": float(d["unexpected_execution_residual"].sum()),
              "price_buffer_residual": float(d["price_buffer_residual"].sum()), "limit_block_residual": float(d["limit_block_residual"].sum()),
              "t1_reject_residual": float(d["t1_reject_residual"].sum()), "p95_weight_drift_bps": float(d["portfolio_weight_drift_bps"].abs().quantile(.95)) if not d.empty else None}
    (output_dir / "strict_execution_deviation_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--trades", type=Path, required=True); p.add_argument("--output-dir", type=Path, required=True)
    args=p.parse_args(); print(json.dumps(run(args.trades, args.output_dir), ensure_ascii=False, indent=2))
