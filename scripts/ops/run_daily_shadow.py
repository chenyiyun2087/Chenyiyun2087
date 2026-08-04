"""Daily alpha-challenger shadow recording (true forward blind, 2026-08-05+).

Per config/oos_registry.yaml true_forward_blind: from 2026-08-05 the ONLY
unseen data accumulates via shadow.  This task records, per trading day:

  T 15:30 : candidate signals (challenger scores -> TopN), expected entry
            prices (T close), intended weights.
  T+1 09:35 : actual open prices, fill simulation (limit-up/down gate,
            min-lot, ADV capacity), realized entry cost.

Output: exports/formal_evidence/alpha_challengers/shadow/daily_log.parquet
(appended daily) plus a running status JSON (days completed, round trips,
failed fills).  E4 shadow gate: >= 60 trading days AND >= 30 round trips.

Usage (scheduled by web/app.py task alpha_challenger_shadow):
  python scripts/ops/run_daily_shadow.py --mode record|reconcile
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHADOW_ROOT = PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers" / "shadow"
LOG_PATH = SHADOW_ROOT / "daily_log.parquet"
STATUS_PATH = SHADOW_ROOT / "shadow_status.json"

CHALLENGER_ROOT = PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers"
ACTIVE_CHALLENGERS = (
    "f1_no_value", "f1p1_top20_diversified",
    "f2_liquidity_clipped", "f3_vol_risk_penalty",
    "p1_top20_diversified", "p2_style_constrained", "p3_covariance_sizing",
    "r1_market_regime", "r2_crowding_control",
)


def _trade_day() -> str:
    """Latest signal date available in the F1 formal scores snapshot.

    Uses the max trade_date from f1_no_value/scores/formal_scores.parquet,
    which is kept current by the daily VLS score pipeline
    (scripts/ops/compute_daily_vls_scores.py).  Falls back to the SSE
    trade calendar when the scores file is missing.
    """
    scores_path = CHALLENGER_ROOT / "f1_no_value" / "scores" / "formal_scores.parquet"
    if scores_path.exists():
        scores = pd.read_parquet(scores_path, columns=["trade_date"])
        if not scores.empty:
            return str(scores["trade_date"].max())
    # Fallback: calendar-derived next trading day
    cal_path = CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "trade_calendar.csv"
    if not cal_path.exists():
        raise RuntimeError(
            f"shadow_blocked: trade calendar missing at {cal_path} — "
            "run the F1 challenger pipeline first")
    cal = pd.read_csv(cal_path)
    today = datetime.now().strftime("%Y-%m-%d")
    open_days = cal[cal["is_open"] == 1]["cal_date"].astype(str)
    future = open_days[open_days >= today]
    return str(future.iloc[0]) if not future.empty else today


def _load_scores(challenger_id: str, signal_date: str) -> pd.DataFrame:
    path = CHALLENGER_ROOT / challenger_id / "scores" / "formal_scores.parquet"
    if not path.exists():
        return pd.DataFrame()
    scores = pd.read_parquet(path)
    scores["trade_date"] = scores["trade_date"].astype(str)
    day = scores[scores["trade_date"] == signal_date].copy()
    if day.empty:
        return day
    eligible = day.get("eligible_universe", pd.Series(True, index=day.index))
    day = day[eligible.fillna(True).astype(bool)]
    day = day.sort_values("score", ascending=False).head(10)
    # T-close reference price comes from the F1 challenger's prices snapshot
    # (prices are strategy-independent — same release for every challenger;
    # P2/P3/R1/R2 may not have staged snapshots yet).  T-day 15:30 visible.
    prices_path = CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "prices.parquet"
    if prices_path.exists():
        prices = pd.read_parquet(prices_path, columns=["trade_date", "symbol", "raw_close"])
        prices["trade_date"] = prices["trade_date"].astype(str)
        prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
        day["symbol"] = day["symbol"].astype(str).str.zfill(6)
        close_map = (prices[prices["trade_date"] == signal_date]
                     .set_index("symbol")["raw_close"])
        day["reference_price_t_close"] = day["symbol"].map(close_map)
    return day


def record(signal_date: str | None = None) -> dict:
    """Record T-day candidate signals with T-close reference prices."""
    if signal_date is None:
        signal_date = _trade_day()
    rows = []
    for challenger_id in ACTIVE_CHALLENGERS:
        day = _load_scores(challenger_id, signal_date)
        for _, row in day.iterrows():
            rows.append({
                "signal_date": signal_date,
                "challenger_id": challenger_id,
                "symbol": str(row.get("symbol")),
                "score": float(row.get("score", float("nan"))),
                "formal_rank": float(row.get("formal_rank", float("nan"))),
                "target_weight": 1.0 / max(1, len(day)),
                "reference_price_t_close": float(
                    row.get("reference_price_t_close", float("nan"))),
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
            })
    frame = pd.DataFrame(rows)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists() and not frame.empty:
        existing = pd.read_parquet(LOG_PATH)
        frame = pd.concat([existing, frame], ignore_index=True) \
            .drop_duplicates(["signal_date", "challenger_id", "symbol"], keep="last")
    frame.to_parquet(LOG_PATH, index=False, compression="zstd")
    return {"recorded": len(rows), "signal_date": signal_date,
            "total_rows": len(frame)}


def reconcile(execution_date: str | None = None) -> dict:
    """Fill simulation for prior-day signals: open price, gates, fills."""
    log = pd.read_parquet(LOG_PATH) if LOG_PATH.exists() else pd.DataFrame()
    if log.empty:
        return {"reconciled": 0, "error": "empty_log"}
    pending = log[log["fill_price_t1_open"].isna()]
    if execution_date is None:
        execution_date = _trade_day()
    # Prices for T+1 open come from the challenger snapshots (real data).
    prices = pd.read_parquet(
        CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "prices.parquet",
        columns=["trade_date", "symbol", "open", "raw_open", "limit_status"])
    prices["trade_date"] = prices["trade_date"].astype(str)
    day = prices[prices["trade_date"] == execution_date].set_index("symbol")
    filled = 0
    failed = 0
    for idx, row in pending.iterrows():
        info = day.loc[row["symbol"]] if row["symbol"] in day.index else None
        if info is None:
            log.at[idx, "fill_status"] = "NO_PRICE"
            failed += 1
            continue
        open_price = float(info.get("raw_open") or info.get("open") or float("nan"))
        limit_status = str(info.get("limit_status") or "")
        if not pd.notna(open_price) or open_price <= 0:
            log.at[idx, "fill_status"] = "NO_OPEN"
            failed += 1
            continue
        if "D" in limit_status or "limit_down" in limit_status:
            log.at[idx, "fill_status"] = "LIMIT_DOWN_NO_FILL"
            failed += 1
            continue
        log.at[idx, "fill_price_t1_open"] = float(open_price)
        log.at[idx, "fill_status"] = "FILLED"
        log.at[idx, "execution_date"] = execution_date
        filled += 1
    log.to_parquet(LOG_PATH, index=False, compression="zstd")

    days = log.dropna(subset=["fill_price_t1_open"])
    round_trips = int(days.groupby(["challenger_id", "symbol"]).size().ge(2).sum()) \
        if not days.empty else 0
    status = {
        "shadow_days": int(log["signal_date"].nunique()),
        "round_trips": round_trips,
        "failed_fills": int((log["fill_status"].fillna("PENDING") != "FILLED").sum()),
        "e4_gate_met": log["signal_date"].nunique() >= 60 and round_trips >= 30,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return {"reconciled": filled, "failed": failed, "status": status}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["record", "reconcile", "status"],
                        default="record")
    parser.add_argument("--date", default=None, help="signal/execution date YYYY-MM-DD")
    args = parser.parse_args()
    if args.mode == "record":
        print(json.dumps(record(args.date), ensure_ascii=False))
    elif args.mode == "reconcile":
        print(json.dumps(reconcile(args.date), ensure_ascii=False))
    else:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8")) \
            if STATUS_PATH.exists() else {"shadow_days": 0, "round_trips": 0}
        print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
