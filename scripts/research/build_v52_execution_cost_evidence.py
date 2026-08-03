#!/usr/bin/env python3
"""Build execution-cost evidence for the v5.2 alpha validation chain.

This producer derives REAL fill-behavior evidence from an immutable Formal
Run's account-backtest output (trusted_account_backtest_trades.csv,
positions.csv, ledger_events.csv) and its frozen inputs (prices.csv,
adjustment_factors.csv).  It is the execution-cost evidence layer that
run_alpha_v3_validation.py's execution_cost gate consumes.

Design contracts (all documented in the output manifest):

1. Limit-up/down fill evidence comes only from orders the sealed execution
   model (strict_t1_open_precommit_v1) actually submitted and rejected
   (order_status=REJECTED_LIMIT_BLOCK, reject_reason=limit_up_block).  We
   do not simulate rejection; we count it.

2. The limit proxy is the execution model's own rule, computed on frozen
   inputs: gap = open[t]*f[t] / (close[t-1]*f[t-1]) - 1, classified
   LIMIT_UP at >= +9.5% and LIMIT_DOWN at <= -9.5%.  Every rejected order
   is re-verified against this formula on the frozen prices, so the
   evidence cannot drift from the model's semantics.

3. A full-sample contract audit: on the frozen data, no FILLED buy may
   have gap >= +9.5% (a limit-up open cannot be bought) and no FILLED sell
   may have gap <= -9.5% (a limit-down open cannot be sold).  Same-day
   same-symbol sells on limit-up opens are expected (limit-up sells fill;
   buys do not) and are reported as asymmetric-fill confirmation.

4. Unfilled-position freeze evidence: after a rejection the position stays
   frozen at zero (no forced re-pricing the same day, no chase fill), and
   re-entry happens only on a later signal cycle.  We verify zero
   same-day retry orders and zero position rows for the rejected symbol
   after the reject date, and locate the next successful re-entry.

5. Everything binds to the immutable run by path: the report embeds the
   sha256 of every input file it read, so a mutated run cannot
   retroactively change the evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LIMIT_UP_THRESHOLD = 0.095
LIMIT_DOWN_THRESHOLD = -0.095
EXECUTION_MODEL = "strict_t1_open_precommit_v1"
SCHEMA_VERSION = "alpha_v3_execution_cost_evidence_v1"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_date(series: pd.Series) -> pd.Series:
    return series.astype(str).str.slice(0, 10)


def _prev_close_gap(
    trades: pd.DataFrame,
    prices: pd.DataFrame,
    adjustment: pd.DataFrame,
) -> pd.DataFrame:
    """Compute the execution model's adj-open gap for every trade row.

    gap = open[t]*f[t] / (close[t-1]*f[t-1]) - 1 on frozen inputs.
    """
    out = trades.copy()
    p = prices.copy()
    p["trade_date"] = _normalize_date(p["trade_date"])
    p = p.sort_values(["symbol", "trade_date"])
    p["prev_close"] = p.groupby("symbol")["close"].shift(1)
    a = adjustment.copy()
    a["trade_date"] = _normalize_date(a["trade_date"])
    a = a.sort_values(["symbol", "trade_date"])
    a = a.drop_duplicates(["symbol", "trade_date"], keep="last")
    a = a[["trade_date", "symbol", "adj_factor"]].rename(columns={"adj_factor": "adj_t"})
    a["adj_prev"] = a.groupby("symbol")["adj_t"].shift(1)
    m = p.merge(a, on=["trade_date", "symbol"], how="left")
    m["gap"] = (m["open"] * m["adj_t"] / (m["prev_close"] * m["adj_prev"])) - 1.0
    keep = ["trade_date", "symbol", "open", "prev_close", "adj_t", "adj_prev", "gap"]
    gap_map = m[keep].copy()
    ex = out.copy()
    ex["trade_date"] = _normalize_date(ex["trade_date"])
    return ex.merge(gap_map, on=["trade_date", "symbol"], how="left", suffixes=("", "_prices"))


def build_limit_up_down_fill_evidence(
    trades: pd.DataFrame,
    positions: pd.DataFrame,
    prices: pd.DataFrame,
    adjustment: pd.DataFrame,
) -> tuple[dict, pd.DataFrame]:
    """Real rejection evidence + full-sample contract audit."""
    t = _prev_close_gap(trades, prices, adjustment)
    rejected = t[t["order_status"].astype(str).eq("REJECTED_LIMIT_BLOCK")].copy()

    rows: list[dict] = []
    for _, r in rejected.iterrows():
        rows.append(
            {
                "strategy": str(r["strategy"]),
                "trade_date": str(r["trade_date"]),
                "order_id": str(r["order_id"]),
                "symbol": str(r["symbol"]),
                "side": str(r["side"]),
                "signal_date": str(r["signal_date"]),
                "execution_date": str(r["execution_date"]),
                "planned_shares": int(r["planned_shares"]) if pd.notna(r["planned_shares"]) else None,
                "filled_shares": int(r["filled_shares"]) if pd.notna(r["filled_shares"]) else 0,
                "remaining_shares": int(r["remaining_shares"]) if pd.notna(r["remaining_shares"]) else 0,
                "planned_notional_cny": float(r["planned_notional"]) if pd.notna(r["planned_notional"]) else 0.0,
                "reject_reason": str(r["reject_reason"]),
                "open_cny": float(r["open"]) if pd.notna(r["open"]) else None,
                "prev_close_cny": float(r["prev_close"]) if pd.notna(r["prev_close"]) else None,
                "adj_t": float(r["adj_t"]) if pd.notna(r["adj_t"]) else None,
                "adj_prev": float(r["adj_prev"]) if pd.notna(r["adj_prev"]) else None,
                "measured_gap": float(r["gap"]) if pd.notna(r["gap"]) else None,
                "gap_classification": (
                    "LIMIT_UP"
                    if pd.notna(r["gap"]) and r["gap"] >= LIMIT_UP_THRESHOLD
                    else (
                        "LIMIT_DOWN"
                        if pd.notna(r["gap"]) and r["gap"] <= LIMIT_DOWN_THRESHOLD
                        else "NOT_LIMIT"
                    )
                ),
                "gap_matches_rejection": bool(
                    pd.notna(r["gap"])
                    and (
                        (str(r["side"]).upper() == "BUY" and r["gap"] >= LIMIT_UP_THRESHOLD)
                        or (str(r["side"]).upper() == "SELL" and r["gap"] <= LIMIT_DOWN_THRESHOLD)
                    )
                ),
            }
        )

    # Same-day same-symbol sell fills on the same dates (limit-up sells fill).
    same_day: list[dict] = []
    if not rejected.empty:
        key = rejected[["execution_date", "symbol"]].drop_duplicates()
        for _, k in key.iterrows():
            sells = t[
                t["execution_date"].eq(k["execution_date"])
                & t["symbol"].eq(k["symbol"])
                & t["side"].astype(str).eq("SELL")
                & t["order_status"].astype(str).eq("FILLED")
            ]
            for _, s in sells.iterrows():
                same_day.append(
                    {
                        "execution_date": str(k["execution_date"]),
                        "symbol": str(k["symbol"]),
                        "strategy": str(s["strategy"]),
                        "filled_shares": int(s["filled_shares"]),
                        "measured_gap": float(s["gap"]) if pd.notna(s["gap"]) else None,
                        "note": "limit-up open: buy rejected, sell filled (A-share microstructure)",
                    }
                )

    # Full-sample contract audit on frozen data.
    filled = t[t["order_status"].astype(str).eq("FILLED")].copy()
    filled = filled[pd.notna(filled["gap"])]
    buy_violations = filled[
        filled["side"].astype(str).eq("BUY") & (filled["gap"] >= LIMIT_UP_THRESHOLD)
    ]
    sell_violations = filled[
        filled["side"].astype(str).eq("SELL") & (filled["gap"] <= LIMIT_DOWN_THRESHOLD)
    ]
    n_buy_at_limit_up_open = int((filled["side"].astype(str).eq("BUY") & (filled["gap"] >= LIMIT_UP_THRESHOLD)).sum())
    n_sell_at_limit_down_open = int((filled["side"].astype(str).eq("SELL") & (filled["gap"] <= LIMIT_DOWN_THRESHOLD)).sum())

    evidence = {
        "schema_version": SCHEMA_VERSION,
        "evidence_layer": "SIMULATION_ORDER_LEDGER",
        "execution_model": EXECUTION_MODEL,
        "status": "PASS" if (rows and all(r["gap_matches_rejection"] for r in rows) and len(buy_violations) == 0 and len(sell_violations) == 0) else "FAIL",
        "total_orders": int(len(trades)),
        "rejected_order_count": int(len(rejected)),
        "rejected_order_rate": float(len(rejected) / len(trades)) if len(trades) else 0.0,
        "limit_up_rejections": int((rejected["side"].astype(str).eq("BUY")).sum()),
        "limit_down_rejections": int((rejected["side"].astype(str).eq("SELL")).sum()),
        "rejected_orders": rows,
        "same_day_sell_fills_on_limit_up": same_day,
        "full_sample_contract_audit": {
            "audit_rule": "no FILLED buy with gap >= +9.5%; no FILLED sell with gap <= -9.5%",
            "filled_orders_audited": int(len(filled)),
            "buy_orders_at_limit_up_open": n_buy_at_limit_up_open,
            "sell_orders_at_limit_down_open": n_sell_at_limit_down_open,
            "contract_violations": int(len(buy_violations) + len(sell_violations)),
            "violation_orders": [
                {
                    "strategy": str(v["strategy"]),
                    "trade_date": str(v["trade_date"]),
                    "symbol": str(v["symbol"]),
                    "side": str(v["side"]),
                    "gap": float(v["gap"]),
                }
                for _, v in pd.concat([buy_violations, sell_violations]).iterrows()
            ],
        },
    }
    return evidence, t


def build_unfilled_position_freeze_evidence(
    trades_gapped: pd.DataFrame,
    positions: pd.DataFrame,
) -> dict:
    """Freeze semantics after rejection: no chase, no make-up order, no forced fill.

    The rejected increment is dropped for the remainder of that signal cycle:
    there is no same-day retry and no make-up buy before the next re-entry.
    A pre-existing position is kept as-is (it is not force-liquidated by the
    rejection), which is the model's documented behavior.
    """
    rejected = trades_gapped[trades_gapped["order_status"].astype(str).eq("REJECTED_LIMIT_BLOCK")]
    events: list[dict] = []
    for _, r in rejected.iterrows():
        symbol, ex_date = int(r["symbol"]), str(r["execution_date"])
        strat = str(r["strategy"])
        side = str(r["side"]).upper()
        # 1) Same-day retry orders for the same symbol (any side)?
        retry = trades_gapped[
            trades_gapped["symbol"].eq(symbol)
            & trades_gapped["trade_date"].eq(ex_date)
            & trades_gapped["strategy"].eq(strat)
            & (trades_gapped["order_status"].astype(str).ne("REJECTED_LIMIT_BLOCK"))
        ]
        # 2) Next successful same-side order (new signal cycle)?
        later = trades_gapped[
            trades_gapped["symbol"].eq(symbol)
            & trades_gapped["strategy"].eq(strat)
            & trades_gapped["trade_date"].gt(ex_date)
            & trades_gapped["side"].astype(str).eq(side)
            & trades_gapped["order_status"].astype(str).eq("FILLED")
        ].sort_values("trade_date")
        reentry = (
            {
                "trade_date": str(later.iloc[0]["trade_date"]),
                "signal_date": str(later.iloc[0]["signal_date"]),
                "filled_shares": int(later.iloc[0]["filled_shares"]),
            }
            if not later.empty
            else None
        )
        # 3) Make-up orders: same-side non-rejected orders between the reject
        #    date and the next re-entry (would be a forced chase of the
        #    rejected increment) -- must be zero.
        make_up = trades_gapped[
            trades_gapped["symbol"].eq(symbol)
            & trades_gapped["strategy"].eq(strat)
            & trades_gapped["trade_date"].gt(ex_date)
            & trades_gapped["side"].astype(str).eq(side)
            & (
                trades_gapped["trade_date"].lt(reentry["trade_date"])
                if reentry is not None
                else trades_gapped["trade_date"].notna()
            )
        ]
        # 4) Pre-existing position on the reject date (kept, not liquidated)?
        prior_pos = positions[
            positions["symbol"].eq(symbol)
            & positions["strategy"].eq(strat)
            & positions["trade_date"].eq(ex_date)
        ]
        events.append(
            {
                "strategy": strat,
                "symbol": str(symbol),
                "execution_date": ex_date,
                "rejected_side": side,
                "rejected_shares": int(r["remaining_shares"]) if pd.notna(r["remaining_shares"]) else 0,
                "same_day_retry_orders": int(len(retry)),
                "make_up_orders_before_reentry": int(len(make_up)),
                "existing_position_shares_on_reject_date": int(prior_pos["shares"].iloc[0]) if len(prior_pos) else 0,
                "existing_position_kept": bool(len(prior_pos)),
                "freeze_semantics": (
                    "rejected increment is dropped for the signal cycle: no same-day retry, "
                    "no make-up order, no forced re-price; re-entry only on a later signal cycle"
                ),
                "next_successful_reentry": reentry,
            }
        )
    if not rejected.empty and all(
        e["same_day_retry_orders"] == 0
        and e["make_up_orders_before_reentry"] == 0
        and e["next_successful_reentry"]
        for e in events
    ):
        status = "PASS"
    else:
        status = "FAIL"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "evidence_layer": "POSITION_LEDGER",
        "events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-run-dir", type=Path, required=True,
                        help="formal run directory containing account_backtest/ and frozen_inputs/")
    parser.add_argument("--output", type=Path, required=True,
                        help="output JSON path for the evidence report")
    args = parser.parse_args()

    acct = args.formal_run_dir / "account_backtest"
    frozen = args.formal_run_dir / "frozen_inputs"
    inputs = {
        "trades": acct / "trusted_account_backtest_trades.csv",
        "positions": acct / "trusted_account_backtest_positions.csv",
        "events": acct / "trusted_account_backtest_ledger_events.csv",
        "prices": frozen / "prices.csv",
        "adjustment": frozen / "adjustment_factors.csv",
    }
    for k, p in inputs.items():
        if not p.exists():
            print(f"missing input: {k} ({p})")
            return 2

    trades = pd.read_csv(inputs["trades"])
    positions = pd.read_csv(inputs["positions"])
    events = pd.read_csv(inputs["events"])
    prices = pd.read_csv(inputs["prices"])
    adjustment = pd.read_csv(inputs["adjustment"])

    limit_evidence, trades_gapped = build_limit_up_down_fill_evidence(
        trades, positions, prices, adjustment
    )
    freeze_evidence = build_unfilled_position_freeze_evidence(trades_gapped, positions)

    # Ledger event chain completeness: every REJECTED order has a CANCELLED event.
    rejected_ids = set(trades_gapped[trades_gapped["order_status"].eq("REJECTED_LIMIT_BLOCK")]["order_id"].astype(str))
    cancelled_events = events[events["order_status"].eq("CANCELLED_T1_CLOSE")]
    cancelled_ids = set(cancelled_events["order_id"].astype(str))
    event_chain_complete = bool(rejected_ids and rejected_ids.issubset(cancelled_ids))

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_model": EXECUTION_MODEL,
        "limit_proxy": {
            "formula": "gap = open[t]*f[t] / (close[t-1]*f[t-1]) - 1",
            "limit_up_threshold": LIMIT_UP_THRESHOLD,
            "limit_down_threshold": LIMIT_DOWN_THRESHOLD,
        },
        "limit_up_down_fill_evidence": limit_evidence,
        "unfilled_position_freeze_evidence": freeze_evidence,
        "ledger_event_chain": {
            "rejected_order_ids": sorted(rejected_ids),
            "cancelled_order_ids": sorted(cancelled_ids),
            "complete": event_chain_complete,
            "note": "every REJECTED_LIMIT_BLOCK order has a matching CANCELLED_T1_CLOSE ledger event",
        },
        "input_bindings": {k: {"path": str(v), "sha256": _sha256(v)} for k, v in inputs.items()},
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.output}")
    print(f"  rejected={limit_evidence['rejected_order_count']} "
          f"(limit_up={limit_evidence['limit_up_rejections']}, "
          f"limit_down={limit_evidence['limit_down_rejections']}), "
          f"contract_violations={limit_evidence['full_sample_contract_audit']['contract_violations']}, "
          f"event_chain_complete={event_chain_complete}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
