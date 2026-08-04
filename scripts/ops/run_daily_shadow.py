"""Daily alpha-challenger shadow recording (true forward blind).

v5.4.1 evidence-repair status:
  The pre-v5.5 shadow (2026-08-03..04) is INVALIDATED_FOR_SELECTION and
  isolated under exports/forward_shadow_smoke_tests/20260803/.  This task
  is DISABLED in task_registry/pipeline.yaml until Forward Shadow Engine
  v2 (v5.5) passes its integration suite.  When re-enabled, the fixes
  below are in force:

  - T+1 resolution: execution_date = trade_calendar.next_open_day(signal_date)
    (never the max score date — Defect 3 fixed).
  - Directional gates: BUY blocks limit-UP (not limit-DOWN); SELL blocks
    limit-DOWN (not limit-UP).  Delegates to the canonical
    scripts/research/execution_market_rules.py (can_buy_at_open /
    can_sell_at_open) — Defect 4 fixed.
  - True-blind gate: recording refuses dates before
    config/oos_registry.yaml true_forward_blind.start (2026-08-05).
  - Reconcile uses the execution date given by the calendar, not
    "latest score date" — Defect 3.

Output: exports/formal_evidence/alpha_challengers/shadow/daily_log.parquet
(appended daily) plus a running status JSON.  E4 shadow gate: >= 60 trading
days AND >= 30 round trips.

Usage (scheduled by web/app.py task alpha_challenger_shadow):
  python scripts/ops/run_daily_shadow.py --mode record|reconcile
"""

from __future__ import annotations

import argparse
import importlib.util as _iu
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

# True-blind start (config/oos_registry.yaml true_forward_blind.start).
# Recording before this date is refused — those records cannot be shadow
# evidence.
TRUE_BLIND_START = "2026-08-05"

# Inline import of the canonical execution-market rules.
_mr = _iu.spec_from_file_location(
    "execution_market_rules",
    PROJECT_ROOT / "scripts/research/execution_market_rules.py")
_MR = _iu.module_from_spec(_mr)
_mr.loader.exec_module(_MR)


def load_trade_calendar() -> pd.DataFrame:
    """Canonical trade calendar (SSE) from the F1 challenger snapshot."""
    cal_path = CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "trade_calendar.csv"
    if not cal_path.exists():
        raise RuntimeError(
            f"shadow_blocked: trade calendar missing at {cal_path} — "
            "run the F1 challenger pipeline first")
    cal = pd.read_csv(cal_path)
    cal["cal_date"] = cal["cal_date"].astype(str)
    return cal[cal["is_open"] == 1]["cal_date"].sort_values().tolist()


def next_open_day(open_days: list[str], signal_date: str) -> str:
    """First trading day strictly AFTER signal_date (T+1).

    Raises ValueError when the signal date has no successor (calendar
    exhausted) — fail-closed, never silent fallback to the signal date.
    """
    future = [d for d in open_days if d > signal_date]
    if not future:
        raise ValueError(
            f"shadow_blocked: no trading day after {signal_date} in the "
            "trade calendar")
    return future[0]


def _signal_date_from_scores() -> str:
    """Latest signal date available in the F1 formal scores snapshot.

    Kept only as a fallback when the caller does not pass --date.  The
    RECORD mode requires a real signal date; execution_date is ALWAYS
    derived from the trade calendar, never from score timestamps.
    """
    scores_path = CHALLENGER_ROOT / "f1_no_value" / "scores" / "formal_scores.parquet"
    if not scores_path.exists():
        raise RuntimeError("shadow_blocked: F1 scores snapshot missing")
    scores = pd.read_parquet(scores_path, columns=["trade_date"])
    return str(scores["trade_date"].max())


def _check_true_blind(signal_date: str) -> None:
    """Refuse recording before the declared true-blind start."""
    if signal_date < TRUE_BLIND_START:
        raise RuntimeError(
            f"shadow_blocked: {signal_date} precedes true_forward_blind.start "
            f"({TRUE_BLIND_START}) — pre-blind records cannot be shadow "
            "evidence (see exports/forward_shadow_smoke_tests/20260803/)")


def _load_universe(signal_date: str) -> pd.DataFrame:
    """PIT tradable universe for the signal date (is_listed/is_st/
    is_suspended), from the F1 challenger snapshot."""
    uni_path = CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "tradable_universe.parquet"
    if not uni_path.exists():
        return pd.DataFrame()
    uni = pd.read_parquet(uni_path)
    uni["trade_date"] = uni["trade_date"].astype(str)
    uni["symbol"] = uni["symbol"].astype(str).str.zfill(6)
    return uni[uni["trade_date"] == signal_date].set_index("symbol")


def _load_scores(challenger_id: str, signal_date: str) -> pd.DataFrame:
    path = CHALLENGER_ROOT / challenger_id / "scores" / "formal_scores.parquet"
    if not path.exists():
        return pd.DataFrame()
    scores = pd.read_parquet(path)
    scores["trade_date"] = scores["trade_date"].astype(str)
    day = scores[scores["trade_date"] == signal_date].copy()
    if day.empty:
        return day
    day["symbol"] = day["symbol"].astype(str).str.zfill(6)
    # eligible_universe must come from the PIT tradable universe, not from
    # a column that defaults to True everywhere.  If the universe snapshot
    # is missing, the candidate is dropped (fail-closed) — never assumed
    # eligible.
    uni = _load_universe(signal_date)
    if uni.empty:
        raise RuntimeError(
            f"shadow_blocked: tradable universe missing for {signal_date} — "
            "no candidate may be assumed eligible")
    day["is_listed"] = day["symbol"].map(uni["is_listed"])
    day["is_st"] = day["symbol"].map(uni["is_st"])
    day["is_suspended"] = day["symbol"].map(uni["is_suspended"])
    day = day[
        day["is_listed"].eq(1)
        & day["is_suspended"].eq(0)
        & day["is_st"].notna()
    ].copy()
    day = day.sort_values("score", ascending=False).head(10)
    return day


def _t_close_prices(signal_date: str) -> pd.Series:
    """T-day close prices (raw_close) for reference."""
    prices_path = CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "prices.parquet"
    if not prices_path.exists():
        return pd.Series(dtype=float)
    prices = pd.read_parquet(prices_path, columns=["trade_date", "symbol", "raw_close"])
    prices["trade_date"] = prices["trade_date"].astype(str)
    prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
    return (prices[prices["trade_date"] == signal_date]
            .set_index("symbol")["raw_close"])


def record(signal_date: str | None = None) -> dict:
    """Record T-day candidate signals with T-close reference prices."""
    if signal_date is None:
        signal_date = _signal_date_from_scores()
    _check_true_blind(signal_date)
    open_days = load_trade_calendar()
    execution_date = next_open_day(open_days, signal_date)
    close_map = _t_close_prices(signal_date)
    rows = []
    for challenger_id in ACTIVE_CHALLENGERS:
        day = _load_scores(challenger_id, signal_date)
        for _, row in day.iterrows():
            rows.append({
                "signal_date": signal_date,
                "execution_date": execution_date,
                "challenger_id": challenger_id,
                "symbol": str(row.get("symbol")),
                "score": float(row.get("score", float("nan"))),
                "formal_rank": float(row.get("formal_rank", float("nan"))),
                "target_weight": 1.0 / max(1, len(day)),
                "reference_price_t_close": float(
                    close_map.get(str(row.get("symbol")), float("nan"))),
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
            })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"recorded": 0, "signal_date": signal_date,
                "execution_date": execution_date, "total_rows": 0}
    required_cols = ["signal_date", "execution_date", "challenger_id",
                     "symbol", "score", "formal_rank", "target_weight",
                     "reference_price_t_close", "recorded_at",
                     "side", "precommit_price", "fill_price",
                     "fill_status", "slippage_bps", "rejection_reason"]
    for c in required_cols:
        if c not in frame.columns:
            frame[c] = pd.NA
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists():
        existing = pd.read_parquet(LOG_PATH)
        frame = pd.concat([existing, frame], ignore_index=True) \
            .drop_duplicates(["signal_date", "challenger_id", "symbol"], keep="last")
    frame.to_parquet(LOG_PATH, index=False, compression="zstd")
    return {"recorded": len(rows), "signal_date": signal_date,
            "execution_date": execution_date, "total_rows": len(frame)}


def reconcile(execution_date: str | None = None) -> dict:
    """T+1 fill simulation: canonical directional gates + min lot.

    BUY  gates: limit-UP at open blocks; suspension blocks; no open price
                blocks; not listed blocks (can_buy_at_open).
    SELL gates: limit-DOWN at open blocks; suspension blocks; no open
                price blocks (can_sell_at_open).

    Execution date comes from the caller (T+1 morning) — verified against
    the trade calendar; it is NEVER derived from "latest score date".
    """
    log = pd.read_parquet(LOG_PATH) if LOG_PATH.exists() else pd.DataFrame()
    if log.empty:
        return {"reconciled": 0, "error": "empty_log"}
    open_days = load_trade_calendar()
    if execution_date is None:
        # Morning task: T+1 for the latest recorded signal date.
        latest_signal = str(log["signal_date"].max())
        execution_date = next_open_day(open_days, latest_signal)
    if execution_date not in open_days:
        raise RuntimeError(
            f"shadow_blocked: {execution_date} is not an open trading day "
            "in the calendar")

    pending = log[log["fill_status"].isna()].copy()
    if pending.empty:
        return {"reconciled": 0, "failed": 0, "status": _status_from_log(log)}

    prices = pd.read_parquet(
        CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "prices.parquet",
        columns=["trade_date", "symbol", "open", "raw_open", "raw_pre_close"])
    prices["trade_date"] = prices["trade_date"].astype(str)
    prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
    day_prices = prices[prices["trade_date"] == execution_date].set_index("symbol")
    uni = _load_universe(execution_date)

    filled, failed = 0, 0
    for idx, row in pending.iterrows():
        symbol = str(row["symbol"])
        info = day_prices.loc[symbol] if symbol in day_prices.index else None
        uni_row = uni.loc[symbol] if not uni.empty and symbol in uni.index else None
        side = str(row.get("side") or "BUY").upper()
        open_price = None
        prev_close = None
        if info is not None:
            open_price = float(info.get("raw_open") or info.get("open") or float("nan"))
            prev_close = float(info.get("raw_pre_close") or float("nan"))
        if open_price is None or not pd.notna(open_price) or open_price <= 0:
            log.at[idx, "fill_status"] = "NO_OPEN"
            log.at[idx, "rejection_reason"] = "no_open_price"
            failed += 1
            continue
        if prev_close is None or not pd.notna(prev_close) or prev_close <= 0:
            log.at[idx, "fill_status"] = "NO_PREV_CLOSE"
            log.at[idx, "rejection_reason"] = "missing_prev_close_limit_unknown"
            failed += 1
            continue
        is_listed = None if uni_row is None else float(uni_row.get("is_listed"))
        is_suspended = None if uni_row is None else float(uni_row.get("is_suspended"))
        is_st = None if uni_row is None else float(uni_row.get("is_st"))
        if side == "SELL":
            allowed, reason = _MR.can_sell_at_open(
                open_price, prev_close, symbol, is_st,
                is_listed=is_listed, is_suspended=is_suspended)
        else:
            allowed, reason = _MR.can_buy_at_open(
                open_price, prev_close, symbol, is_st,
                is_listed=is_listed, is_suspended=is_suspended)
        if not allowed:
            log.at[idx, "fill_status"] = "BLOCKED"
            log.at[idx, "rejection_reason"] = reason or "gate_blocked"
            failed += 1
            continue
        log.at[idx, "fill_price"] = float(open_price)
        log.at[idx, "fill_status"] = "FILLED"
        log.at[idx, "execution_date"] = execution_date
        log.at[idx, "side"] = side
        filled += 1
    log.to_parquet(LOG_PATH, index=False, compression="zstd")
    return {"reconciled": filled, "failed": failed,
            "execution_date": execution_date,
            "status": _status_from_log(log)}


def _status_from_log(log: pd.DataFrame) -> dict:
    days = log.dropna(subset=["fill_price"])
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
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["record", "reconcile", "status"],
                        default="record")
    parser.add_argument("--date", default=None, help="signal/execution date YYYY-MM-DD")
    args = parser.parse_args()
    try:
        if args.mode == "record":
            print(json.dumps(record(args.date), ensure_ascii=False))
        elif args.mode == "reconcile":
            print(json.dumps(reconcile(args.date), ensure_ascii=False))
        else:
            status = json.loads(STATUS_PATH.read_text(encoding="utf-8")) \
                if STATUS_PATH.exists() else {"shadow_days": 0, "round_trips": 0}
            print(json.dumps(status, ensure_ascii=False, indent=2))
    except RuntimeError as exc:
        print(json.dumps({"shadow_blocked": str(exc)}, ensure_ascii=False))
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
