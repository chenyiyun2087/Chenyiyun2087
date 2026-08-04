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
# WITHDRAWN 2026-08-05 (v5.5.1 prestart): the 2026-08-05 start was
# declared before the C2/C3/SCD/PIT-lineage correctness fixes; the 08-04
# SEALED package is KNOWN_DEFECT_PRESTART_PACKAGE.  When start is null
# (NOT_STARTED) EVERY date is refused — recording is fail-closed until
# the start is formally re-declared in config/oos_registry.yaml.
TRUE_BLIND_START = None  # null = NOT_STARTED (re-declare via oos_registry.yaml)

# Inline import of the canonical execution-market rules.
_mr = _iu.spec_from_file_location(
    "execution_market_rules",
    PROJECT_ROOT / "scripts/research/execution_market_rules.py")
_MR = _iu.module_from_spec(_mr)
_mr.loader.exec_module(_MR)


def load_trade_calendar(need_date: str | None = None) -> list[str]:
    """Canonical SSE open days (sorted 'YYYY-MM-DD').

    v5.5 live extension: when ``need_date`` is beyond the PIT snapshot
    calendar (2026-07-31), open days are merged from the live
    ``chenyiyun.dim_trade_cal``.  Fail-closed: a live day whose calendar
    cannot be read, or which the DB does not know as open, raises — the
    calendar is never fabricated.
    """
    cal_path = CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "trade_calendar.csv"
    if not cal_path.exists():
        raise RuntimeError(
            f"shadow_blocked: trade calendar missing at {cal_path} — "
            "run the F1 challenger pipeline first")
    cal = pd.read_csv(cal_path)
    cal["cal_date"] = cal["cal_date"].astype(str)
    base = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].tolist())
    if need_date and base and need_date > base[-1]:
        live = _live_open_days(base[-1])
        if not live:
            raise RuntimeError(
                f"shadow_blocked: {need_date} is beyond the snapshot "
                f"calendar ({base[-1]}) and chenyiyun.dim_trade_cal has no "
                "open days after it — live calendar unavailable")
        base = sorted(set(base) | set(live))
    return base


def _live_open_days(after_date: str) -> list[str]:
    """Open SSE trading days after ``after_date`` from chenyiyun.dim_trade_cal.

    Raises RuntimeError on any DB failure — a live execution day must
    block, never silently substitute the snapshot calendar.
    """
    import os
    import pymysql
    try:
        conn = pymysql.connect(
            host="localhost", user="root",
            password=os.environ.get("CHENYIYUN_DB_PASSWORD", ""),
            database="chenyiyun", charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception as exc:
        raise RuntimeError(
            f"shadow_blocked: live calendar unavailable: {exc}") from exc
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cal_date FROM dim_trade_cal "
                "WHERE exchange = 'SSE' AND is_open = 1 AND cal_date > %s "
                "ORDER BY cal_date",
                (after_date,))
            rows = cur.fetchall()
    except Exception as exc:
        raise RuntimeError(
            f"shadow_blocked: live calendar query failed: {exc}") from exc
    finally:
        conn.close()
    out: list[str] = []
    for row in rows:
        value = str(row["cal_date"])
        # normalize 20260805 -> 2026-08-05
        if len(value) == 8 and value.isdigit():
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        out.append(value)
    return out


# v5.5: execution prices — immutable PIT snapshot for historical days,
# dwd_stock_daily_standard for live days beyond the snapshot end.
PIT_PRICES_PATH = CHALLENGER_ROOT / "f1_no_value" / "snapshots" / "prices.parquet"


def _pit_prices_max_date() -> str | None:
    """Last trade_date covered by the immutable PIT snapshot prices."""
    if not PIT_PRICES_PATH.exists():
        return None
    try:
        dates = pd.to_datetime(
            pd.read_parquet(PIT_PRICES_PATH, columns=["trade_date"])["trade_date"],
            errors="coerce").dropna()
        return str(dates.max().date()) if not dates.empty else None
    except Exception:
        return None


def _live_bars(date_str: str) -> pd.DataFrame:
    """Bars for one live date from tushare_stock.dwd_stock_daily_standard.

    Columns: trade_date, symbol, raw_open, raw_pre_close, raw_close
    (aliased for the snapshot-compatible downstream).  Raises RuntimeError
    on DB failure; returns an EMPTY frame when the date has no bars —
    per-order NO_OPEN is then the honest fill outcome.
    """
    import os
    import pymysql
    try:
        conn = pymysql.connect(
            host="localhost", user="root",
            password=os.environ.get("CHENYIYUN_DB_PASSWORD", ""),
            database="tushare_stock", charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception as exc:
        raise RuntimeError(
            f"shadow_blocked: live bars unavailable: {exc}") from exc
    try:
        date_int = int(date_str.replace("-", ""))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, ts_code, open, pre_close, close "
                "FROM dwd_stock_daily_standard WHERE trade_date = %s",
                (date_int,))
            rows = cur.fetchall()
    except Exception as exc:
        raise RuntimeError(
            f"shadow_blocked: live bars query failed: {exc}") from exc
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["symbol"] = df["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6)
    df["raw_open"] = pd.to_numeric(df["open"], errors="coerce")
    df["raw_pre_close"] = pd.to_numeric(df["pre_close"], errors="coerce")
    df["raw_close"] = pd.to_numeric(df["close"], errors="coerce")
    df["trade_date"] = date_str
    return df[["trade_date", "symbol", "raw_open", "raw_pre_close", "raw_close"]]


def _load_execution_prices(execution_date: str,
                           prices_path: Path | None = None) -> pd.DataFrame:
    """Execution-day bars for the reconcile gates.

    ``prices_path`` (tests) wins; otherwise the immutable PIT snapshot is
    authoritative for historical days and the live DB serves days beyond
    the snapshot end.  A missing snapshot is fail-closed (raise), never
    silently replaced by live data.
    """
    if prices_path is not None:
        if not prices_path.exists():
            return pd.DataFrame()
        prices = pd.read_parquet(prices_path)
        prices["trade_date"] = prices["trade_date"].astype(str)
        prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
        return prices[prices["trade_date"] == execution_date]
    pit_max = _pit_prices_max_date()
    if pit_max is None:
        raise RuntimeError(
            "shadow_blocked: PIT snapshot prices missing — cannot reconcile")
    if execution_date > pit_max:
        return _live_bars(execution_date)
    prices = pd.read_parquet(PIT_PRICES_PATH)
    prices["trade_date"] = prices["trade_date"].astype(str)
    prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
    return prices[prices["trade_date"] == execution_date]


def _flag(series: pd.Series, symbol: str) -> float | None:
    """Read a per-symbol flag from a package-universe series (None if absent)."""
    if series is None or series.empty or symbol not in series.index:
        return None
    value = series.get(symbol)
    return float(value) if pd.notna(value) else None


def _t_close_map(signal_date: str, prices_path: Path | None = None) -> pd.Series:
    """T-day close (raw_close) map for precommit reference prices."""
    if prices_path is not None:
        if not prices_path.exists():
            return pd.Series(dtype=float)
        prices = pd.read_parquet(prices_path, columns=["trade_date", "symbol",
                                                       "raw_close"])
        prices["trade_date"] = prices["trade_date"].astype(str)
        prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
        return (prices[prices["trade_date"] == signal_date]
                .set_index("symbol")["raw_close"])
    pit_max = _pit_prices_max_date()
    if pit_max is None:
        raise RuntimeError(
            "shadow_blocked: PIT snapshot prices missing — cannot precommit")
    if signal_date > pit_max:
        frame = _live_bars(signal_date)
        return (frame.set_index("symbol")["raw_close"] if not frame.empty
                else pd.Series(dtype=float))
    prices = pd.read_parquet(PIT_PRICES_PATH, columns=["trade_date", "symbol",
                                                       "raw_close"])
    prices["trade_date"] = prices["trade_date"].astype(str)
    prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
    return (prices[prices["trade_date"] == signal_date]
            .set_index("symbol")["raw_close"])


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
    """Refuse recording when the true-blind start is not declared.

    v5.5.1 (2026-08-05): start is null (NOT_STARTED) — every date is
    refused.  When re-declared in config/oos_registry.yaml, dates before
    the start remain refused.
    """
    if TRUE_BLIND_START is None:
        raise RuntimeError(
            f"shadow_blocked: true_forward_blind.start is NOT_STARTED "
            "(withdrawn 2026-08-05, v5.5.1 prestart) — {signal_date} "
            "cannot be shadow evidence until the start is re-declared in "
            "config/oos_registry.yaml")
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


# ══════════════════════════════════════════════════════════════════
# v5.5 execution zone — precommit + reconcile from SEALED packages
# ══════════════════════════════════════════════════════════════════

EXECUTION_ZONE = PROJECT_ROOT / "exports" / "forward_shadow_evidence" / "execution"
PACKAGES_ZONE = PROJECT_ROOT / "exports" / "forward_shadow_evidence" / "packages"


def _latest_package_for_execution(execution_date: str,
                                  packages_zone: Path | None = None) -> Path | None:
    """The SEALED package whose execution_date matches (v5.5 flow)."""
    zone = packages_zone or PACKAGES_ZONE
    if not zone.exists():
        return None
    for pkg_dir in sorted(zone.iterdir(), reverse=True):
        if not pkg_dir.is_dir():
            continue
        manifest_path = pkg_dir / "signal_package_manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("execution_date") == execution_date:
            return pkg_dir
    return None


def _orders_path(execution_date: str, execution_zone: Path | None = None) -> Path:
    zone = execution_zone or EXECUTION_ZONE
    return zone / execution_date / "orders.json"


def precommit(execution_date: str | None = None,
              packages_zone: Path | None = None,
              execution_zone: Path | None = None,
              prices_path: Path | None = None) -> dict:
    """T+1 09:25 — materialize BUY orders from the SEALED package.

    Orders start at SIGNAL_CREATED, pass TARGET_PORTFOLIO_SEALED and end
    at ORDER_PRECOMMITTED with precommit_price = T-day close (reference).
    Target shares are lot-adjusted (100-share A-share lots).  The orders
    file is append-only per execution date.
    """
    today = datetime.now().date().isoformat()
    open_days = load_trade_calendar(need_date=execution_date or today)
    if execution_date is None:
        execution_date = open_days[-1] if open_days else None
    if execution_date not in open_days:
        raise RuntimeError(
            f"shadow_blocked: {execution_date} is not an open trading day")
    pkg = _latest_package_for_execution(execution_date, packages_zone)
    if pkg is None:
        raise RuntimeError(
            f"shadow_blocked: no SEALED package for execution_date "
            f"{execution_date} — seal the T-day package first")
    manifest = json.loads((pkg / "signal_package_manifest.json").read_text())
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    close_map = _t_close_map(manifest["signal_date"], prices_path)

    orders_path = _orders_path(execution_date, execution_zone)
    existing = []
    if orders_path.exists():
        existing = json.loads(orders_path.read_text(encoding="utf-8"))
        if any(o.get("state") != "ORDER_PRECOMMITTED" for o in existing):
            raise RuntimeError(
                f"shadow_blocked: orders for {execution_date} already "
                "reconciled — precommit is append-only before fills")

    orders = list(existing)
    for _, row in portfolios.iterrows():
        symbol = str(row["symbol"])
        precommit_price = float(close_map.get(symbol, float("nan")))
        notional = float(row["target_weight"]) * 500_000.0
        shares = int(notional / precommit_price // 100 * 100) \
            if pd.notna(precommit_price) and precommit_price > 0 else 0
        orders.append({
            "signal_date": manifest["signal_date"],
            "execution_date": execution_date,
            "challenger_id": str(row.get("candidate_id", "")),
            "symbol": symbol,
            "side": "BUY",
            "target_weight": float(row["target_weight"]),
            "target_shares": shares,
            "lot_adjusted_shares": shares,
            "precommit_price": precommit_price,
            "fill_price": None, "fill_status": None,
            "slippage_bps": None, "rejection_reason": None,
            "state": "ORDER_PRECOMMITTED",
            "precommitted_at": datetime.now().isoformat(timespec="seconds"),
        })
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    orders_path.write_text(json.dumps(orders, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return {"precommitted": len(orders), "execution_date": execution_date,
            "package": str(pkg.name), "signal_date": manifest["signal_date"]}


def reconcile_from_package(execution_date: str | None = None,
                           execution_zone: Path | None = None,
                           prices_path: Path | None = None,
                           packages_zone: Path | None = None) -> dict:
    """T+1 09:35 — fill PRECOMMITTED orders at open with directional gates.

    BUY blocks limit-UP (can_buy_at_open); suspension / not-listed / no
    open price block.  A completed BUY_FILLED opens a HOLDING; round
    trips are counted only after a later SELL_FILLED (state machine).

    v5.5 live: prices come from the PIT snapshot for historical days and
    from dwd_stock_daily_standard beyond the snapshot end; ST / listing /
    suspension flags come from the SEALED package universe (T-day status).
    """
    zone = execution_zone or EXECUTION_ZONE
    today = datetime.now().date().isoformat()
    open_days = load_trade_calendar(need_date=execution_date or today)
    if execution_date is None:
        # Morning: the next open day after the latest precommitted signal.
        candidates = sorted(d.name for d in zone.iterdir() if d.is_dir())
        if not candidates:
            return {"reconciled": 0, "error": "no_precommitted_orders"}
        latest_signal = None
        for c in reversed(candidates):
            orders_file = zone / c / "orders.json"
            if orders_file.exists():
                data = json.loads(orders_file.read_text(encoding="utf-8"))
                if data:
                    latest_signal = data[0]["signal_date"]
                    break
        if latest_signal is None:
            return {"reconciled": 0, "error": "no_precommitted_orders"}
        execution_date = next_open_day(open_days, latest_signal)
    if execution_date not in open_days:
        raise RuntimeError(
            f"shadow_blocked: {execution_date} is not an open trading day")

    orders_path = _orders_path(execution_date, zone)
    if not orders_path.exists():
        return {"reconciled": 0, "error": "no_orders_for_execution_date"}
    orders = json.loads(orders_path.read_text(encoding="utf-8"))
    pending = [o for o in orders if o.get("state") == "ORDER_PRECOMMITTED"]
    if not pending:
        return {"reconciled": 0, "failed": 0,
                "status": {"state": "already_reconciled"}}

    prices = _load_execution_prices(execution_date, prices_path)
    day_prices = prices.set_index("symbol") if not prices.empty else prices

    # T-day status flags from the SEALED package universe (PIT contract);
    # absent universe -> flags stay None (gates treat unknown as blocked).
    st_map = listed_map = suspended_map = pd.Series(dtype=float)
    pkg = _latest_package_for_execution(
        execution_date, packages_zone or PACKAGES_ZONE)
    if pkg is not None and (pkg / "universe.parquet").exists():
        uni = pd.read_parquet(pkg / "universe.parquet")
        uni["symbol"] = uni["symbol"].astype(str).str.zfill(6)
        uni = uni.set_index("symbol")
        st_map = pd.to_numeric(uni.get("is_st", 0), errors="coerce")
        if "is_listed" in uni.columns:
            listed_map = pd.to_numeric(uni["is_listed"], errors="coerce")
        if "is_suspended" in uni.columns:
            suspended_map = pd.to_numeric(uni["is_suspended"], errors="coerce")

    filled, failed = 0, 0
    for o in pending:
        symbol = str(o["symbol"])
        info = day_prices.loc[symbol] if symbol in day_prices.index else None
        open_price = float(info["raw_open"] or info["open"] or float("nan")) \
            if info is not None else float("nan")
        prev_close = float(info.get("raw_pre_close") or float("nan")) \
            if info is not None else float("nan")
        if not pd.notna(open_price) or open_price <= 0:
            o["fill_status"] = "NO_OPEN"
            o["rejection_reason"] = "no_open_price"
            o["state"] = "BUY_REJECTED"
            failed += 1
            continue
        if not pd.notna(prev_close) or prev_close <= 0:
            o["fill_status"] = "NO_PREV_CLOSE"
            o["rejection_reason"] = "missing_prev_close_limit_unknown"
            o["state"] = "BUY_REJECTED"
            failed += 1
            continue
        is_st = float(st_map.get(symbol)) if (
            symbol in st_map.index and pd.notna(st_map.get(symbol))) else 0.0
        allowed, reason = _MR.can_buy_at_open(
            open_price, prev_close, symbol, is_st,
            is_listed=_flag(listed_map, symbol),
            is_suspended=_flag(suspended_map, symbol))
        if not allowed:
            o["fill_status"] = "BLOCKED"
            o["rejection_reason"] = reason or "gate_blocked"
            o["state"] = "BUY_REJECTED"
            failed += 1
            continue
        o["fill_price"] = float(open_price)
        o["slippage_bps"] = round(
            (open_price / float(o["precommit_price"]) - 1.0) * 1e4, 2) \
            if o.get("precommit_price") else None
        o["fill_status"] = "FILLED"
        o["state"] = "BUY_FILLED"
        o["filled_at"] = datetime.now().isoformat(timespec="seconds")
        filled += 1
    orders_path.write_text(json.dumps(orders, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return {"reconciled": filled, "failed": failed,
            "execution_date": execution_date,
            "status": {"buy_filled": filled, "buy_rejected": failed}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["record", "precommit", "reconcile",
                                           "status"],
                        default="record")
    parser.add_argument("--date", default=None, help="signal/execution date YYYY-MM-DD")
    args = parser.parse_args()
    try:
        if args.mode == "record":
            print(json.dumps(record(args.date), ensure_ascii=False))
        elif args.mode == "precommit":
            print(json.dumps(precommit(args.date), ensure_ascii=False))
        elif args.mode == "reconcile":
            # v5.5: fill from SEALED-package precommitted orders first;
            # legacy log-based reconcile remains for the disabled path.
            try:
                print(json.dumps(reconcile_from_package(args.date),
                                 ensure_ascii=False))
            except RuntimeError:
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
