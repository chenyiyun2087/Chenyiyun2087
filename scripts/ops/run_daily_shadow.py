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
  - Forward-epoch gate: recording resolves the start only from
    config/forward_epochs.yaml; no date is embedded in runtime code.
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
import hashlib
import importlib.util as _iu
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = Path(
    os.environ.get("CHENYIYUN_SOURCE_REPO") or PROJECT_ROOT
).expanduser().resolve()
SHADOW_ROOT = EVIDENCE_ROOT / "exports" / "formal_evidence" / "alpha_challengers" / "shadow"
LOG_PATH = SHADOW_ROOT / "daily_log.parquet"
STATUS_PATH = SHADOW_ROOT / "shadow_status.json"

# v5.5.2 execution loop: the event ledger is the durable truth, the state
# machine is reconstructed from it (replay), orders.json is a projection.
from runtime.shadow_events import (  # noqa: E402
    BUY_FILLED,
    BUY_REJECTED,
    NAV_SNAPSHOT,
    ORDER_PRECOMMITTED,
    SELL_FILLED,
    SELL_PRECOMMITTED,
    append_event,
    event_log_path,
    existing_identities,
    exported_orders,
    iter_all_events,
    replay,
    replay_all,
)
from runtime.shadow_execution_state import ROUND_TRIP_COMPLETED  # noqa: E402
from runtime.shadow_virtual_account import (  # noqa: E402
    AccountConservationError,
    VirtualAccount,
)
from runtime.epoch_governance import (  # noqa: E402
    DEFAULT_FORWARD_EPOCHS_PATH,
    load_forward_epoch_manifest,
)

CHALLENGER_ROOT = EVIDENCE_ROOT / "exports" / "formal_evidence" / "alpha_challengers"
ACTIVE_CHALLENGERS = (
    "f1_no_value", "f1p1_top20_diversified",
    "f2_liquidity_clipped", "f3_vol_risk_penalty",
    "p1_top20_diversified", "p2_style_constrained", "p3_covariance_sizing",
    "r1_market_regime", "r2_crowding_control",
)

# Forward dates are resolved from config/forward_epochs.yaml.  There is no
# hard-coded start date: until a formal epoch is frozen, all rows remain
# engineering soak and are invalid for E4/selection.

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
        # normalize compact YYYYMMDD values to ISO dates
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
    """Raw bars for one live date from tushare_stock.ods_daily.

    Columns: trade_date, symbol, raw_open, raw_pre_close, raw_close
    (aliased for the snapshot-compatible downstream).

    The previous implementation read
    dwd_stock_daily_standard, which carries ONLY ADJUSTED prices
    (adj_open/adj_high/adj_low/adj_close — no open/pre_close columns at
    all; the first live run crashed with Unknown column 'open').  Raw
    prices must NEVER be aliased from adjusted ones: limit up/down bands
    break on every ex-date.  ods_daily is the raw canonical table and is
    loaded at ~17:00 T (measured 2026-08-04 17:00:03) — in time for the
    T 21:35 sell and the T+1 morning precommit.  Raises RuntimeError on
    DB failure; returns an EMPTY frame when the date has no bars —
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
                "FROM ods_daily WHERE trade_date = %s",
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
    """Refuse recording until the canonical manifest declares a formal epoch."""
    manifest = load_forward_epoch_manifest(DEFAULT_FORWARD_EPOCHS_PATH)
    epoch = manifest.formal_epoch
    if epoch is None or not epoch.start:
        raise RuntimeError(
            "shadow_blocked: no formal forward epoch declared; active "
            "engineering soak is invalid for E4 and selection")
    if signal_date < epoch.start:
        raise RuntimeError(
            f"shadow_blocked: {signal_date} precedes formal epoch start "
            f"({epoch.start}) — pre-epoch records cannot be shadow evidence")


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
                "execution_date": execution_date, "total_rows": 0,
                "capital_authority": False, "allowed_new_capital_cny": 0}
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
            "execution_date": execution_date, "total_rows": len(frame),
            "capital_authority": False, "allowed_new_capital_cny": 0}


def reconcile(execution_date: str | None = None,
              log_path: Path | None = None,
              write_back: bool = True,
              status_path: Path | None = None) -> dict:
    """T+1 fill simulation: canonical directional gates + min lot.

    BUY  gates: limit-UP at open blocks; suspension blocks; no open price
                blocks; not listed blocks (can_buy_at_open).
    SELL gates: limit-DOWN at open blocks; suspension blocks; no open
                price blocks (can_sell_at_open).

    Execution date comes from the caller (T+1 morning) — verified against
    the trade calendar; it is NEVER derived from "latest score date".

    v5.5.1: the legacy path is ENGINEERING-ONLY.  `--mode
    legacy-reconcile-audit` reads the legacy log but never writes it back
    (write_back=False) and never touches the formal status file — its
    output is smoke-test zone with evidence_eligible: false.
    """
    log_path = log_path or LOG_PATH
    status_path = status_path or STATUS_PATH
    log = pd.read_parquet(log_path) if log_path.exists() else pd.DataFrame()
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
    if write_back:
        log.to_parquet(log_path, index=False, compression="zstd")
        status = _status_from_log(log, status_path)
    else:
        # Audit mode: never write the fill back, never touch the formal
        # status file — compute a read-only status for the report.
        status = _status_from_log(log, status_path, write_back=False)
    return {"reconciled": filled, "failed": failed,
            "execution_date": execution_date,
            "status": status}


def _status_from_log(log: pd.DataFrame,
                     status_path: Path | None = None,
                     write_back: bool = True) -> dict:
    days = log.dropna(subset=["fill_price"])
    round_trips = int(days.groupby(["challenger_id", "symbol"]).size().ge(2).sum()) \
        if not days.empty else 0
    try:
        formal_epoch = load_forward_epoch_manifest(DEFAULT_FORWARD_EPOCHS_PATH).formal_epoch
    except (FileNotFoundError, ValueError):
        formal_epoch = None
    valid_dates = log["signal_date"].astype(str).unique().tolist() if "signal_date" in log.columns else []
    if formal_epoch and formal_epoch.start:
        valid_dates = [value for value in valid_dates if value >= formal_epoch.start]
    else:
        valid_dates = []
    status = {
        "shadow_days": int(log["signal_date"].nunique()),
        "round_trips": round_trips,
        "failed_fills": int((log["fill_status"].fillna("PENDING") != "FILLED").sum()),
        "formal_epoch_id": formal_epoch.epoch_id if formal_epoch else None,
        "formal_epoch_days": len(valid_dates),
        "e4_gate_met": bool(formal_epoch and len(valid_dates) >= 60 and round_trips >= 30),
        "capital_authority": False,
        "allowed_new_capital_cny": 0,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if write_back:
        (status_path or STATUS_PATH).write_text(
            json.dumps(status, ensure_ascii=False, indent=2),
            encoding="utf-8")
    return status


# ══════════════════════════════════════════════════════════════════
# v5.5 execution zone — precommit + reconcile from SEALED packages
# ══════════════════════════════════════════════════════════════════

EXECUTION_ZONE = EVIDENCE_ROOT / "exports" / "forward_shadow_evidence" / "execution"
PACKAGES_ZONE = EVIDENCE_ROOT / "exports" / "forward_shadow_evidence" / "packages"


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


def _package_execution_eligible(pkg_dir: Path) -> tuple[bool, str]:
    """classification.json gate: absent = eligible; present with
    execution_eligible=False = REFUSE.

    Legacy prestart packages may be SEALED but carry
    KNOWN_DEFECT_PRESTART_PACKAGE (execution_eligible: false).  Without
    this gate a re-run could write orders against a defective package — the
    execution chain must never consume a package its classification forbids.
    """
    cls_path = pkg_dir / "classification.json"
    if not cls_path.exists():
        # A package carrying an explicitly legacy/soak epoch is never
        # execution/E4 eligible, even when its classification file is absent.
        manifest_path = pkg_dir / "signal_package_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                epoch_id = manifest.get("epoch_id")
                if epoch_id:
                    epoch = load_forward_epoch_manifest(DEFAULT_FORWARD_EPOCHS_PATH).active_epoch
                    if epoch is None or epoch.epoch_id != str(epoch_id) or not epoch.formal:
                        return False, "epoch_not_formal_or_not_active"
            except (ValueError, OSError, json.JSONDecodeError):
                return False, "signal_package_manifest_corrupt"
        return True, "no classification.json"
    try:
        cls = json.loads(cls_path.read_text(encoding="utf-8"))
    except ValueError:
        return False, "classification.json corrupt"
    if cls.get("execution_eligible") is False:
        return False, f"classification={cls.get('classification')!r}"
    manifest_path = pkg_dir / "signal_package_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            epoch_id = manifest.get("epoch_id")
            if epoch_id:
                epoch = load_forward_epoch_manifest(DEFAULT_FORWARD_EPOCHS_PATH).active_epoch
                if epoch is None or epoch.epoch_id != str(epoch_id) or not epoch.formal:
                    return False, "epoch_not_formal_or_not_active"
        except (ValueError, OSError, json.JSONDecodeError):
            return False, "signal_package_manifest_corrupt"
    return True, ""
    return None


def _latest_sealed_package(packages_zone: Path | None = None,
                           as_of_date: str | None = None):
    """(pkg_dir, manifest) of the newest SEALED package by signal date.

    Production sell/precommit tasks run WITHOUT an explicit execution date
    and must resolve it from the latest
    SEALED package — never from open_days[-1] (the snapshot calendar
    extends to 2026-12-31, so a date-less run used to resolve to year-end
    and crash with "no SEALED package for execution_date 2026-12-31").
    """
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
        if manifest.get("package_status") == "SEALED":
            if as_of_date and str(manifest.get("signal_date") or "") > as_of_date:
                continue
            return pkg_dir, manifest
    return None


def _orders_path(execution_date: str, execution_zone: Path | None = None) -> Path:
    zone = execution_zone or EXECUTION_ZONE
    return zone / execution_date / "orders.json"


def compute_order_id(package_sha: str, candidate_id: str,
                     execution_date: str, symbol: str, side: str,
                     rebalance_sequence: int = 1) -> str:
    """Deterministic order identity (v5.5.1 idempotency contract).

    The same (package, candidate, execution date, symbol, side, sequence)
    MUST hash to the same id so a repeated precommit is a no-op instead of
    a double-append.
    """
    payload = "|".join([package_sha, candidate_id, execution_date,
                        symbol, side, str(rebalance_sequence)])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── v5.5.2 execution loop ────────────────────────────────────────────

EXECUTION_CONTRACT_PATH = (PROJECT_ROOT / "config" / "strategy_runtime"
                           / "forward_shadow_v2.yaml")


def _candidate_execution_config() -> dict[str, dict]:
    """Per-candidate frozen execution contracts (forward_shadow_v2.yaml).

    Keyed by the CANDIDATE id (C0/C1/C2/C3/RND — the id that packages,
    orders and accounts carry).  v5.5.3: keyed by candidate id, NOT by
    challenger_id ('baseline_vls_frozen' / 'f1_no_value' / ...) — the
    two had silently diverged and every config.get() on a real order
    missed, which precommit/sell/NAV must never do.  Missing file or
    missing candidate -> fail-closed raise: the engines must NEVER run
    on guessed parameters.
    """
    if not EXECUTION_CONTRACT_PATH.exists():
        raise RuntimeError(
            "shadow_blocked: forward_shadow_v2.yaml missing — execution "
            "contracts unavailable")
    cfg = yaml.safe_load(EXECUTION_CONTRACT_PATH.read_text(encoding="utf-8"))
    out = {}
    for cand, meta in (cfg.get("candidates") or {}).items():
        if not meta:
            continue
        ex = meta.get("execution") or {}
        out[cand] = {
            "challenger_id": meta.get("challenger_id"),
            "hold_days": int(ex.get("hold_days", 20)),
            "rebalance_score_buffer": float(
                ex.get("rebalance_score_buffer", 0.10)),
            "weight_drift_band": float(ex.get("weight_drift_band", 0.0)),
            "cost_rate": float(ex.get("cost_rate", 0.00075)),
            "slippage_bps": float(ex.get("slippage_bps", 10.0)),
            "initial_cash_cny": float(ex.get("initial_cash_cny", 500000.0)),
        }
    if not out:
        raise RuntimeError(
            "shadow_blocked: no candidate execution contracts found in "
            "forward_shadow_v2.yaml")
    return out


def _target_map(pkg_dir: Path) -> dict[tuple[str, str], float]:
    """{(candidate_id, symbol): target_weight} from the SEALED package."""
    portfolios_path = pkg_dir / "target_portfolios.parquet"
    if not portfolios_path.exists():
        raise RuntimeError(
            f"shadow_blocked: {pkg_dir.name} has no target_portfolios.parquet "
            "— sell engine cannot rebalance against the package")
    frame = pd.read_parquet(portfolios_path)
    out = {}
    for _, row in frame.iterrows():
        cand = str(row.get("candidate_id", ""))
        sym = str(row["symbol"]).zfill(6)
        out[(cand, sym)] = float(row.get("target_weight", 0.0))
    return out


def _held_positions(machine):
    """Open HOLDING positions as {key: (position, buy_order)}."""
    out = {}
    for key, pos in machine.positions.items():
        if pos.state == "HOLDING":
            out[key] = (pos, pos.buy_order)
    return out


def _orders_for_date(execution_date: str,
                     execution_zone: Path | None = None) -> list[dict]:
    """orders.json for one execution date (empty list when absent)."""
    path = _orders_path(execution_date, execution_zone)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def sell_precommit(execution_date: str | None = None,
                   execution_zone: Path | None = None,
                   packages_zone: Path | None = None,
                   prices_path: Path | None = None,
                   business_date: str | None = None) -> dict:
    """T 17:00 — decide which HOLDING positions to SELL on T+1.

    Per the frozen contracts (hold_days / rebalance_score_buffer /
    weight_drift_band from forward_shadow_v2.yaml):

      - before hold expiry: a position is left untouched even if the
        latest package no longer holds the symbol (hold-period rule)
      - on/after hold expiry: a position whose symbol is NOT in the
        latest target portfolio is exited (rebalance_exit); one still in
        the portfolio is kept (score-buffer rule: it survived the top-N
        cut) unless its target weight shrank beyond the drift band
      - target-weight shrinkage beyond the band triggers a partial
        reduction at ANY time (risk_reduction — C2's R2 overlay scales
        weights daily; C0/C1/C3 equal-weight so this never fires)

    SELL orders are precommitted at the T-day close and executed at T+1
    open (same T+1 semantics as BUY).  Idempotency mirrors BUY: the same
    (challenger, symbol, execution date) has at most one pending SELL.
    """
    zone = execution_zone or EXECUTION_ZONE
    today = datetime.now().date().isoformat()
    logical_date = business_date or today
    if execution_date is None and business_date is None:
        # Queue workers persist the business date in the subprocess
        # environment.  Prefer that identity over wall-clock time so a retry
        # cannot cross midnight and bind the sell decision to the wrong day.
        task_date = str(os.environ.get("CHENYIYUN_TASK_BUSINESS_DATE") or "").strip()
        if task_date:
            if len(task_date) == 8 and task_date.isdigit():
                task_date = f"{task_date[:4]}-{task_date[4:6]}-{task_date[6:]}"
            try:
                logical_date = datetime.strptime(task_date, "%Y-%m-%d").date().isoformat()
            except ValueError as exc:
                raise RuntimeError(
                    f"shadow_blocked: invalid task business date {task_date!r}"
                ) from exc
    open_days = load_trade_calendar(need_date=execution_date or logical_date)
    if execution_date is None:
        # The production sell task runs at T 17:00
        # under the queue's datestr=T, but the sells target the NEXT
        # execution day — the latest SEALED package's execution_date
        # (the T-day seal's T+1).  open_days[-1] resolved to 2026-12-31
        # (snapshot calendar extends to year-end) and crashed.  A seal
        # whose execution day has already passed means there is NO fresh
        # T-day signal — refuse stale targets (fail-closed).
        latest = _latest_sealed_package(
            packages_zone,
            as_of_date=business_date,
        )
        if latest is None:
            raise RuntimeError(
                "shadow_blocked: no SEALED package — the sell engine "
                "requires the T-day target portfolio")
        execution_date = latest[1]["execution_date"]
        if business_date and latest[1].get("signal_date") != business_date:
            raise RuntimeError(
                f"shadow_blocked: no SEALED package for business date "
                f"{business_date} — refusing a stale historical sell target")
        if execution_date <= logical_date:
            raise RuntimeError(
                f"shadow_blocked: latest SEALED package "
                f"{latest[1].get('signal_date')} targets execution "
                f"{execution_date} (already passed {logical_date}) — no fresh "
                "T-day seal; refusing stale sell targets")
        open_days = load_trade_calendar(need_date=execution_date)
    if execution_date not in open_days:
        raise RuntimeError(
            f"shadow_blocked: {execution_date} is not an open trading day")
    pkg = _latest_package_for_execution(execution_date,
                                        packages_zone or PACKAGES_ZONE)
    if pkg is None:
        raise RuntimeError(
            f"shadow_blocked: no SEALED package for execution_date "
            f"{execution_date} — sell engine requires the T-day target "
            "portfolio")
    eligible, why = _package_execution_eligible(pkg)
    if not eligible:
        raise RuntimeError(
            f"shadow_blocked: package {pkg.name} for execution_date "
            f"{execution_date} is not execution-eligible ({why}) — the "
            "sell engine never consumes KNOWN_DEFECT/prestart packages")
    manifest = json.loads((pkg / "signal_package_manifest.json").read_text())
    signal_date = manifest["signal_date"]
    config = _candidate_execution_config()
    target = _target_map(pkg)
    machine = replay_all(zone, as_of_date=logical_date)
    held = _held_positions(machine)
    if not held:
        # v5.5.1 verifier contract: a no-position day still writes a
        # decision marker — the task scheduler's result verifier needs a
        # durable artifact to distinguish "legitimately nothing to sell"
        # from a crashed/failed run (fail-closed).
        decision_path = _orders_path(execution_date, zone).with_name("sell_decisions.json")
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.write_text(json.dumps({
            "signal_date": signal_date, "execution_date": execution_date,
            "reason": "no_open_positions",
            "decided_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"sells": 0, "execution_date": execution_date,
                "signal_date": signal_date, "reason": "no_open_positions"}

    close_map = _t_close_map(signal_date, prices_path)
    day_index = {d: i for i, d in enumerate(open_days)}
    exec_idx = day_index.get(execution_date, 0)
    log_path = event_log_path(zone, execution_date)
    seen_events = existing_identities(log_path)

    orders = _orders_for_date(execution_date, zone)
    # v5.5.3: (candidate, symbol) SET — one candidate holds several names.
    pending_sells = {(o.get("challenger_id"), o.get("symbol"))
                     for o in orders
                     if o.get("side") == "SELL"
                     and o.get("state") == "SELL_PRECOMMITTED"}
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    sells, skipped = [], 0
    for (cand, sym), (pos, buy) in sorted(held.items()):
        contract = config.get(cand)
        if contract is None:
            raise RuntimeError(
                f"shadow_blocked: candidate {cand} has no frozen execution "
                "contract — sell engine refuses to guess hold_days/costs")
        if (cand, sym) in pending_sells:
            skipped += 1  # already precommitted for this execution day
            continue
        hold_days = contract["hold_days"]
        sig_idx = day_index.get(buy.signal_date, 0)
        expired = (exec_idx - sig_idx) >= hold_days
        target_weight = target.get((cand, sym))
        # v5.5.3: sell quantity = what is still HELD (remaining_shares) —
        # never the previous sell order's shares (a partial risk_reduction
        # fill leaves the rest held and re-decidable).
        shares = pos.remaining_shares if pos.remaining_shares > 0 \
            else buy.lot_adjusted_shares or buy.target_shares
        precommit_price = float(close_map.get(sym, float("nan")))
        if not pd.notna(precommit_price) or precommit_price <= 0:
            # No T-close reference: cannot precommit a price -> defer to
            # the next run (never invent a price).
            skipped += 1
            continue
        if not expired and target_weight is None:
            # Hold-period rule: before hold expiry a position is left
            # untouched even if the latest package drops the symbol (no
            # churn on daily score noise — the exit decision is deferred
            # to the expiry rebalance).
            skipped += 1
            continue
        if not expired and target_weight is not None:
            band = contract["weight_drift_band"]
            if target_weight >= buy.target_weight * (1.0 - band) - 1e-12:
                skipped += 1  # hold: within band / still in target, pre-expiry
                continue
        # Exit decision reached: full exit on expiry-exclusion, partial
        # reduction when the target weight shrank beyond the band.
        sell_shares = shares
        reason = "rebalance_exit"
        if expired and target_weight is not None:
            skipped += 1  # still in target at expiry -> keep holding
            continue
        if not expired and target_weight is not None:
            target_shares = int(target_weight * contract["initial_cash_cny"]
                                / precommit_price // 100 * 100)
            sell_shares = min(shares, max(0, shares - target_shares))
            reason = "risk_reduction"
            if sell_shares <= 0:
                skipped += 1  # target >= current (rounding) — nothing to sell
                continue
        order_id = compute_order_id(
            _package_sha(pkg), cand, execution_date, sym, "SELL", 1)
        if any(o.get("order_id") == order_id for o in orders):
            # The same decision (package, candidate, execution day, symbol)
            # re-run is an idempotent no-op — orders.json stays 1:1 with
            # the event ledger.  A REJECTED sell is re-decided on a LATER
            # execution day (new order_id), never re-precommitted today.
            skipped += 1
            continue
        order = {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "challenger_id": cand,
            "symbol": sym,
            "side": "SELL",
            "target_weight": float(buy.target_weight),
            "target_shares": sell_shares,
            "lot_adjusted_shares": sell_shares,
            "precommit_price": precommit_price,
            "fill_price": None, "fill_status": None,
            "slippage_bps": None, "rejection_reason": None,
            "state": "SELL_PRECOMMITTED",
            "package_sha": _package_sha(pkg),
            "order_id": order_id,
            "precommit_run_id": run_id,
            "exit_reason": reason,
            "precommitted_at": datetime.now().isoformat(timespec="seconds"),
        }
        event = {
            "event_type": SELL_PRECOMMITTED,
            "signal_date": signal_date,
            "execution_date": execution_date,
            "challenger_id": cand,
            "symbol": sym,
            "side": "SELL",
            "target_weight": float(buy.target_weight),
            "target_shares": sell_shares,
            "lot_adjusted_shares": sell_shares,
            "precommit_price": precommit_price,
            "order_id": order_id,
            "source_package_sha": _package_sha(pkg),
            "exit_reason": reason,
        }
        append_event(log_path, event, seen=seen_events)
        orders.append(order)
        sells.append(order)
    orders_path = _orders_path(execution_date, zone)
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    orders_path.write_text(json.dumps(orders, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    if not sells:
        # v5.5.5: a held-position day with zero SELL decisions is still a
        # legitimate no-op — every position is inside its hold period (or
        # has no T-close reference).  The verifier needs the same durable
        # marker the no_open_positions branch writes, so a crash and a
        # legitimately-sell-nothing day stay distinguishable.  Re-runs
        # overwrite an identical payload (idempotent).
        decision_path = orders_path.with_name("sell_decisions.json")
        decision_path.write_text(json.dumps({
            "signal_date": signal_date, "execution_date": execution_date,
            "reason": "no_sells_all_skipped", "skipped": skipped,
            "decided_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"sells": len(sells), "skipped": skipped,
            "execution_date": execution_date, "signal_date": signal_date,
            "sells_detail": [{"challenger_id": o["challenger_id"],
                              "symbol": o["symbol"], "shares": o["target_shares"],
                              "reason": o.get("exit_reason")}
                             for o in sells]}


def _rebuild_accounts(config: dict[str, dict],
                      execution_zone: Path | None = None,
                      as_of_date: str | None = None):
    """Replay all fill events into per-candidate VirtualAccounts."""
    zone = execution_zone or EXECUTION_ZONE
    accounts: dict[str, VirtualAccount] = {}
    for ev in iter_all_events(zone, as_of_date=as_of_date):
        etype = ev["event_type"]
        if etype not in (BUY_FILLED, SELL_FILLED):
            continue
        cand = ev["challenger_id"]
        contract = config.get(cand)
        if contract is None:
            raise RuntimeError(
                f"shadow_blocked: fill event for candidate {cand} with no "
                "execution contract — account cannot be costed")
        acc = accounts.get(cand)
        if acc is None:
            acc = accounts[cand] = VirtualAccount(
                cand, initial_cash=contract["initial_cash_cny"],
                cost_rate=contract["cost_rate"],
                slippage_bps=contract["slippage_bps"])
        shares = int(ev["shares"])
        if etype == BUY_FILLED:
            acc.buy_fill(ev["symbol"], shares, float(ev["fill_price"]))
        else:
            acc.sell_fill(ev["symbol"], shares, float(ev["fill_price"]))
    return accounts


def nav(execution_date: str | None = None,
        execution_zone: Path | None = None,
        prices_path: Path | None = None) -> dict:
    """T+1 15:30 — daily per-candidate NAV snapshots (mark to close).

    Accounts are rebuilt from the fill-event ledger (never from
    orders.json); a held symbol with no close price for the day raises
    ACCOUNT_CONSERVATION_ERROR (a 0-price mark is forbidden).  The NAV
    snapshot event is appended per candidate — a rerun is an idempotent
    no-op.
    """
    zone = execution_zone or EXECUTION_ZONE
    today = datetime.now().date().isoformat()
    open_days = load_trade_calendar(need_date=execution_date or today)
    if execution_date is None:
        # v5.5.3: NAV is a mark-to-close of TODAY — never open_days[-1]
        # (the snapshot calendar extends to 2026-12-31, so a date-less
        # run would snapshot a nonexistent future day).
        execution_date = today
    if execution_date not in open_days:
        raise RuntimeError(
            f"shadow_blocked: {execution_date} is not an open trading day")
    config = _candidate_execution_config()
    accounts = _rebuild_accounts(config, zone, as_of_date=execution_date)
    if not accounts:
        summary_path = zone / "nav" / "nav_summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            days = summary.get("days") or {}
            if execution_date in days:
                days.pop(execution_date, None)
                summary["generated_at"] = datetime.now().isoformat(
                    timespec="seconds")
                summary_path.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8")
        return {"date": execution_date, "accounts": [], "reason": "no_fills"}
    prices = _load_execution_prices(execution_date, prices_path)
    close_map = (prices.set_index("symbol")["raw_close"].to_dict()
                 if not prices.empty else {})
    log_path = event_log_path(zone, execution_date)
    seen_events = existing_identities(log_path)
    snapshots = []
    for cand, acc in sorted(accounts.items()):
        snap = acc.daily_snapshot(execution_date, close_map)
        snapshots.append(snap)
        append_event(log_path, {
            "event_type": NAV_SNAPSHOT,
            "signal_date": execution_date,
            "execution_date": execution_date,
            "challenger_id": cand,
            "symbol": None,
            "side": None,
            "shares": None,
            "nav": snap["nav"],
            "cash": snap["cash"],
        }, seen=seen_events)
    nav_dir = zone / "nav"
    nav_dir.mkdir(parents=True, exist_ok=True)
    summary_path = nav_dir / "nav_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) \
        if summary_path.exists() else {"days": {}}
    summary["days"][execution_date] = snapshots
    summary["generated_at"] = datetime.now().isoformat(timespec="seconds")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    return {"date": execution_date, "accounts": snapshots}


def round_trips(execution_zone: Path | None = None) -> dict:
    """E4 counter: ONLY complete BUY_FILLED -> HOLDING -> SELL_FILLED
    chains (the state machine's completed_round_trips)."""
    machine = replay_all(execution_zone or EXECUTION_ZONE)
    open_positions = [f"{cand}/{sym}" for (cand, sym), p
                      in machine.positions.items()
                      if p.state != "ROUND_TRIP_COMPLETED"]
    return {"round_trips": machine.completed_round_trips(),
            "open_positions": open_positions,
            "orders": len(machine.orders)}


def _package_sha(pkg_dir: Path) -> str:
    """Canonical package identity from package_sha256.json."""
    sha_path = pkg_dir / "package_sha256.json"
    if not sha_path.exists():
        raise RuntimeError(
            f"shadow_blocked: package_sha256.json missing at {pkg_dir} — "
            "no package identity, idempotency contract cannot apply")
    return str(json.loads(sha_path.read_text(encoding="utf-8"))
               .get("package_sha256") or "")


def precommit(execution_date: str | None = None,
              packages_zone: Path | None = None,
              execution_zone: Path | None = None,
              prices_path: Path | None = None) -> dict:
    """T+1 09:25 — materialize BUY orders from the SEALED package.

    Orders start at SIGNAL_CREATED, pass TARGET_PORTFOLIO_SEALED and end
    at ORDER_PRECOMMITTED with precommit_price = T-day close (reference).
    Target shares are lot-adjusted (100-share A-share lots).  The orders
    file is append-only per execution date.

    v5.5.1 idempotency contract (each order carries package_sha, order_id,
    precommit_run_id, event_sequence):

      | same order_id rerun              | IDEMPOTENT_SUCCESS (no append) |
      | same execution day, other pkg SHA | BLOCKED                       |
      | duplicate BUY same cand+symbol   | BLOCKED                       |
      | precommit after reconcile        | BLOCKED                       |
    """
    today = datetime.now().date().isoformat()
    open_days = load_trade_calendar(need_date=execution_date or today)
    if execution_date is None:
        # Resolve from the latest SEALED package —
        # at T+1 09:25 that is the T-day seal's execution_date (T+1).
        # open_days[-1] resolved to 2026-12-31 (snapshot calendar
        # extends to year-end) and crashed precommit in production.
        latest = _latest_sealed_package(packages_zone)
        if latest is None:
            raise RuntimeError(
                "shadow_blocked: no SEALED package — seal the T-day "
                "package first")
        execution_date = latest[1]["execution_date"]
    if execution_date not in open_days:
        raise RuntimeError(
            f"shadow_blocked: {execution_date} is not an open trading day")
    pkg = _latest_package_for_execution(execution_date, packages_zone)
    if pkg is None:
        raise RuntimeError(
            f"shadow_blocked: no SEALED package for execution_date "
            f"{execution_date} — seal the T-day package first")
    eligible, why = _package_execution_eligible(pkg)
    if not eligible:
        raise RuntimeError(
            f"shadow_blocked: package {pkg.name} for execution_date "
            f"{execution_date} is not execution-eligible ({why}) — the "
            "execution chain never consumes KNOWN_DEFECT/prestart packages")
    manifest = json.loads((pkg / "signal_package_manifest.json").read_text())
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    close_map = _t_close_map(manifest["signal_date"], prices_path)
    package_sha = _package_sha(pkg)
    precommit_run_id = datetime.now().strftime("%Y%m%dT%H%M%S%f")

    orders_path = _orders_path(execution_date, execution_zone)
    existing = []
    if orders_path.exists():
        existing = json.loads(orders_path.read_text(encoding="utf-8"))
        # SELL_PRECOMMITTED orders share the same
        # execution-day file — the sell task writes them at T 17:00 and
        # precommit appends BUYs at T+1 09:25.  Only terminal states
        # (fills/rejects — i.e. the day was reconciled) block a rerun.
        if any(o.get("state") not in ("ORDER_PRECOMMITTED",
                                      "SELL_PRECOMMITTED") for o in existing):
            raise RuntimeError(
                f"shadow_blocked: orders for {execution_date} already "
                "reconciled — precommit is append-only before fills")
        if any("order_id" not in o for o in existing):
            raise RuntimeError(
                f"shadow_blocked: orders for {execution_date} predate the "
                "v5.5.1 idempotency contract (no order_id) — the "
                "execution-day ledger must be rebuilt from the package")

    orders = list(existing)
    seen_ids = {o["order_id"] for o in orders if "order_id" in o}
    if seen_ids:
        foreign = [o for o in orders if o.get("package_sha") != package_sha]
        if foreign:
            raise RuntimeError(
                f"shadow_blocked: orders for {execution_date} were "
                f"precommitted from a DIFFERENT package "
                f"({foreign[0].get('package_sha')} vs {package_sha}) — "
                "same-day orders may not mix package identities")

    # v5.5.2: event ledger + hold-period rule.  A symbol already HELD is
    # never re-bought (equal-weight target unchanged -> no add-on);
    # new entries into the target portfolio are bought exactly once.
    log_path = event_log_path(execution_zone or EXECUTION_ZONE,
                              execution_date)
    seen_events = existing_identities(log_path)
    # v5.5.3: held is a (candidate, symbol) SET — one candidate can hold
    # several names (TopN > 1) and a closed round trip is not held.
    replay_as_of = manifest["signal_date"]
    held = {(p.challenger_id, p.symbol) for p in
            replay_all(execution_zone or EXECUTION_ZONE,
                       as_of_date=replay_as_of).positions.values()
            if p.state != ROUND_TRIP_COMPLETED}
    # v5.5.3 cash-aware sizing: accounts rebuilt from the fill-event
    # ledger give each candidate's CURRENT cash — the cap for BUY
    # notional, never the hardcoded 500k.
    config = _candidate_execution_config()
    accounts = _rebuild_accounts(
        config, execution_zone or EXECUTION_ZONE, as_of_date=replay_as_of)

    run_orders = []
    idempotent_skipped = 0
    held_skipped = 0
    # per-candidate reserved cash so consecutive BUYs in one run share
    # the same pool (TopN > 1: each order reduces what the next can buy).
    reserved: dict[str, float] = {}
    for seq, (_, row) in enumerate(portfolios.iterrows(), start=1):
        symbol = str(row["symbol"])
        candidate_id = str(row.get("candidate_id", ""))
        order_id = compute_order_id(package_sha, candidate_id,
                                    execution_date, symbol, "BUY", 1)
        if order_id in seen_ids:
            idempotent_skipped += 1  # IDEMPOTENT_SUCCESS — already there
            continue
        key = (candidate_id, symbol)
        if any(o.get("challenger_id") == candidate_id
               and o.get("symbol") == symbol and o.get("side") == "BUY"
               for o in orders):
            raise RuntimeError(
                f"shadow_blocked: duplicate BUY for candidate {candidate_id} "
                f"symbol {symbol} on {execution_date} — one precommit per "
                "(candidate, symbol, execution day)")
        if (candidate_id, symbol) in held:
            held_skipped += 1  # already holding — no re-buy (v5.5.2 rule)
            continue
        precommit_price = float(close_map.get(symbol, float("nan")))
        # v5.5.3 cash-aware sizing: the contract's initial cash defines
        # the target notional; the account's CURRENT cash caps it (never
        # the hardcoded 500k).  v5.5.4 (2026-08-07): the cap is cost-aware
        # — notional may never exceed cash/(1+rate), so the first order
        # cannot eat the whole cash AND its costs (2026-08-06: C0's
        # first order reserved -15.34 and the NEXT symbol 601163
        # fail-closed the entire morning precommit).  A candidate whose
        # cash cannot afford one A-share lot still gets its order at
        # zero shares (the portfolio contract demands one order per row)
        # — the reconcile rejects it with NO_CASH, and the shortfall
        # never blocks other candidates with intact cash.
        contract = config.get(candidate_id)
        if contract is None:
            raise RuntimeError(
                f"shadow_blocked: candidate {candidate_id} has no frozen "
                "execution contract — precommit refuses to size on guesses")
        acc = accounts.get(candidate_id)
        available_cash = reserved.get(
            candidate_id,
            acc.available_cash if acc is not None
            else contract["initial_cash_cny"])
        rate_total = 1.0 + contract["cost_rate"] \
            + contract["slippage_bps"] / 1e4
        notional = min(float(row["target_weight"]) * contract["initial_cash_cny"],
                       available_cash / rate_total)
        shares = int(notional / precommit_price // 100 * 100) \
            if pd.notna(precommit_price) and precommit_price > 0 else 0
        # reserve this order's estimated cash outlay — consecutive BUYs
        # in the same run share one cash pool (never double-spend).
        # Zero-share (insufficient-cash) orders reserve nothing.
        if shares > 0:
            est_total = notional * rate_total
            reserved[candidate_id] = available_cash - est_total
        order = {
            "signal_date": manifest["signal_date"],
            "execution_date": execution_date,
            "challenger_id": candidate_id,
            "symbol": symbol,
            "side": "BUY",
            "target_weight": float(row["target_weight"]),
            "target_shares": shares,
            "lot_adjusted_shares": shares,
            "precommit_price": precommit_price,
            "fill_price": None, "fill_status": None,
            "slippage_bps": None, "rejection_reason": None,
            "state": "ORDER_PRECOMMITTED",
            "package_sha": package_sha,
            "order_id": order_id,
            "precommit_run_id": precommit_run_id,
            "event_sequence": seq,
            "precommitted_at": datetime.now().isoformat(timespec="seconds"),
        }
        append_event(log_path, {
            "event_type": ORDER_PRECOMMITTED,
            "signal_date": manifest["signal_date"],
            "execution_date": execution_date,
            "challenger_id": candidate_id,
            "symbol": symbol,
            "side": "BUY",
            "target_weight": float(row["target_weight"]),
            "target_shares": shares,
            "lot_adjusted_shares": shares,
            "precommit_price": precommit_price,
            "order_id": order_id,
            "source_package_sha": package_sha,
        }, seen=seen_events)
        run_orders.append(order)
        orders.append(order)
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    orders_path.write_text(json.dumps(orders, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return {"precommitted": len(orders), "execution_date": execution_date,
            "package": str(pkg.name), "signal_date": manifest["signal_date"],
            "package_sha": package_sha, "precommit_run_id": precommit_run_id,
            "idempotent_skipped": idempotent_skipped,
            "held_skipped": held_skipped}


def reconcile_from_package(execution_date: str | None = None,
                           execution_zone: Path | None = None,
                           prices_path: Path | None = None,
                           packages_zone: Path | None = None) -> dict:
    """T+1 09:35 — fill PRECOMMITTED orders at open with directional gates.

    BUY blocks limit-UP (can_buy_at_open); suspension / not-listed / no
    open price block.  A completed BUY_FILLED opens a HOLDING; round
    trips are counted only after a later SELL_FILLED (state machine).

    v5.5 live: prices come from the PIT snapshot for historical days and
    from ods_daily (raw) beyond the snapshot end; ST / listing /
    suspension flags come from the SEALED package universe (T-day status).

    Scheduled after the execution day's raw bars land in ods_daily; a
    pre-open run could otherwise see no bars and mark every order NO_OPEN.
    them (every order would NO_OPEN).  The fill uses the recorded OPEN —
    semantically the T+1 open execution the contract promises.
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
    pending = [o for o in orders
               if o.get("state") in ("ORDER_PRECOMMITTED", "SELL_PRECOMMITTED")]
    if not pending:
        return {"reconciled": 0, "failed": 0,
                "status": {"state": "already_reconciled"}}
    log_path = event_log_path(execution_zone or EXECUTION_ZONE,
                              execution_date)
    seen_events = existing_identities(log_path)

    prices = _load_execution_prices(execution_date, prices_path)
    day_prices = prices.set_index("symbol") if not prices.empty else prices

    # T-day status flags from the SEALED package universe (PIT contract);
    # absent universe -> flags stay None (gates treat unknown as blocked).
    st_map = listed_map = suspended_map = pd.Series(dtype=float)
    pkg = _latest_package_for_execution(
        execution_date, packages_zone or PACKAGES_ZONE)
    if pkg is not None:
        eligible, why = _package_execution_eligible(pkg)
        if not eligible:
            raise RuntimeError(
                f"shadow_blocked: package {pkg.name} for execution_date "
                f"{execution_date} is not execution-eligible ({why}) — "
                "reconcile never consumes KNOWN_DEFECT/prestart packages")
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
    buy_filled = buy_rejected = sell_filled = sell_rejected = 0
    # v5.5.3: accounts rebuilt from prior fills — the fill-time
    # buying-power check runs against CURRENT cash (intraday moves can
    # push a precommitted order beyond the precommit budget).
    config = _candidate_execution_config()
    accounts = _rebuild_accounts(config, zone, as_of_date=execution_date)
    for o in pending:
        symbol = str(o["symbol"])
        side = o.get("side", "BUY")
        info = day_prices.loc[symbol] if symbol in day_prices.index else None
        open_price = float(info["raw_open"] or info["open"] or float("nan")) \
            if info is not None else float("nan")
        prev_close = float(info.get("raw_pre_close") or float("nan")) \
            if info is not None else float("nan")
        def _reject(fill_status: str, reason: str) -> None:
            """Record a rejection in orders.json AND in the event ledger
            (the ledger is the durable truth — a rejection is an event)."""
            o["fill_status"] = fill_status
            o["rejection_reason"] = reason
            o["state"] = f"{side}_REJECTED"
            append_event(log_path, {
                "event_type": f"{side}_REJECTED",
                "signal_date": o.get("signal_date"),
                "execution_date": execution_date,
                "challenger_id": o.get("challenger_id"),
                "symbol": symbol,
                "side": side,
                "shares": int(o.get("lot_adjusted_shares")
                              or o.get("target_shares") or 0),
                "order_id": o.get("order_id"),
                "source_package_sha": o.get("package_sha"),
                "reason": reason,
            }, seen=seen_events)

        if not pd.notna(open_price) or open_price <= 0:
            _reject("NO_OPEN", "no_open_price")
            failed += 1
            continue
        if not pd.notna(prev_close) or prev_close <= 0:
            _reject("NO_PREV_CLOSE", "missing_prev_close_limit_unknown")
            failed += 1
            continue
        is_st = float(st_map.get(symbol)) if (
            symbol in st_map.index and pd.notna(st_map.get(symbol))) else 0.0
        if side == "BUY":
            allowed, reason = _MR.can_buy_at_open(
                open_price, prev_close, symbol, is_st,
                is_listed=_flag(listed_map, symbol),
                is_suspended=_flag(suspended_map, symbol))
        else:
            # SELL: limit-DOWN blocks, suspension defers (retried by the
            # next sell_precommit), never a forced cross.
            allowed, reason = _MR.can_sell_at_open(
                open_price, prev_close, symbol, is_st,
                is_listed=_flag(listed_map, symbol),
                is_suspended=_flag(suspended_map, symbol))
        if not allowed:
            _reject("BLOCKED", reason or "gate_blocked")
            failed += 1
            if side == "SELL":
                sell_rejected += 1
            else:
                buy_rejected += 1
            continue
        # v5.5.3 fill-time buying power + account conservation: the fill
        # must clear the account's CURRENT cash (precommit sizing is a
        # T-day estimate; the open price and concurrent fills move the
        # true balance).  A cash shortfall rejects THIS order; an account
        # invariant breach (negative cash / oversell) fails closed.
        cand = o.get("challenger_id")
        contract = config.get(cand)
        if contract is None:
            raise RuntimeError(
                f"shadow_blocked: fill for candidate {cand} without a "
                "frozen execution contract — the account cannot be costed")
        acc = accounts.get(cand)
        if acc is None:
            acc = accounts[cand] = VirtualAccount(
                cand, initial_cash=contract["initial_cash_cny"],
                cost_rate=contract["cost_rate"],
                slippage_bps=contract["slippage_bps"])
        shares_to_fill = int(o.get("lot_adjusted_shares")
                             or o.get("target_shares") or 0)
        # v5.5.4: zero-share orders (precommitted when a candidate's
        # cash could not afford one lot — see precommit sizing) never
        # reach the fill path, where a 0-share BUY would "fill" for
        # total=0 and mint a 0-share FILLED position.  Reject with
        # NO_CASH: the order ends terminal (contract satisfied), the
        # ledger records the rejection.
        if shares_to_fill <= 0:
            _reject("NO_CASH", "insufficient_cash_for_one_lot")
            failed += 1
            if side == "BUY":
                buy_rejected += 1
            else:
                sell_rejected += 1
            continue
        try:
            if side == "BUY":
                notional = shares_to_fill * open_price
                total = notional * (1.0 + acc.cost_rate
                                    + acc.slippage_bps / 1e4)
                if acc.cash + 1e-9 < total:
                    _reject("NO_CASH", "insufficient_cash_at_fill")
                    failed += 1
                    buy_rejected += 1
                    continue
                acc.buy_fill(symbol, shares_to_fill, open_price)
            else:
                acc.sell_fill(symbol, shares_to_fill, open_price)
            acc.verify_conservation()
        except AccountConservationError as exc:
            raise RuntimeError(
                f"shadow_blocked: ACCOUNT_CONSERVATION_ERROR on {side} "
                f"fill {cand} {symbol}: {exc}") from exc
        o["fill_price"] = float(open_price)
        o["slippage_bps"] = round(
            (open_price / float(o["precommit_price"]) - 1.0) * 1e4, 2) \
            if o.get("precommit_price") else None
        o["fill_status"] = "FILLED"
        o["state"] = f"{side}_FILLED"
        o["filled_at"] = datetime.now().isoformat(timespec="seconds")
        filled += 1
        # v5.5.2: the fill event is the durable truth; the state machine
        # derives BUY_FILLED->HOLDING and SELL_FILLED->ROUND_TRIP_COMPLETED
        # from replay.  shares = the order's lot-adjusted quantity.
        append_event(log_path, {
            "event_type": f"{side}_FILLED",
            "signal_date": o.get("signal_date"),
            "execution_date": execution_date,
            "challenger_id": o.get("challenger_id"),
            "symbol": symbol,
            "side": side,
            "shares": int(o.get("lot_adjusted_shares")
                          or o.get("target_shares") or 0),
            "fill_price": float(open_price),
            "slippage_bps": o["slippage_bps"],
            "order_id": o.get("order_id"),
            "source_package_sha": o.get("package_sha"),
        }, seen=seen_events)
        if side == "SELL":
            sell_filled += 1
        else:
            buy_filled += 1
    orders_path.write_text(json.dumps(orders, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return {"reconciled": filled, "failed": failed,
            "execution_date": execution_date,
            "status": {"buy_filled": buy_filled, "buy_rejected": buy_rejected,
                       "sell_filled": sell_filled,
                       "sell_rejected": sell_rejected}}


def legacy_reconcile_audit(execution_date: str | None = None) -> dict:
    """v5.5.1: legacy log-based reconcile as an ENGINEERING-ONLY audit.

    Reads the legacy daily log, runs the old fill gates, and writes the
    result to the smoke-test zone — NEVER to the E4 execution zone, NEVER
    back into the legacy log, NEVER to the formal shadow status file.
    The output is explicitly not evidence.
    """
    smoke = PROJECT_ROOT / "exports" / "forward_shadow_smoke_tests"
    smoke.mkdir(parents=True, exist_ok=True)
    result = reconcile(
        execution_date,
        log_path=LOG_PATH,          # read the real legacy log
        write_back=False,           # but do not modify it
        status_path=smoke / "legacy_reconcile_status.json",
    )
    result["evidence_eligible"] = False
    result["engine_use_only"] = True
    out_path = smoke / f"legacy_reconcile_{execution_date or 'latest'}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    result["wrote_to"] = str(out_path.relative_to(PROJECT_ROOT))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["record", "precommit", "reconcile",
                                           "sell-precommit", "nav",
                                           "round-trips",
                                           "legacy-reconcile-audit", "status"],
                        default="record")
    parser.add_argument("--date", default=None, help="signal/execution date YYYY-MM-DD")
    parser.add_argument(
        "--business-date", default=None,
        help="historical T-day for sell-precommit replay (YYYY-MM-DD)",
    )
    args = parser.parse_args()
    try:
        if args.mode == "record":
            print(json.dumps(record(args.date), ensure_ascii=False))
        elif args.mode == "precommit":
            print(json.dumps(precommit(args.date), ensure_ascii=False))
        elif args.mode == "reconcile":
            # v5.5.1: package-path ONLY, fail-closed.  A RuntimeError here
            # must surface — the legacy log path was auto-fallback and hid
            # package/execution breakage (forbidden by design).
            print(json.dumps(reconcile_from_package(args.date),
                             ensure_ascii=False))
        elif args.mode == "sell-precommit":
            # v5.5.2: T 17:00 sell decisions for T+1 execution.
            print(json.dumps(sell_precommit(
                args.date, business_date=args.business_date),
                ensure_ascii=False))
        elif args.mode == "nav":
            # v5.5.2: T+1 close per-candidate NAV snapshot.
            print(json.dumps(nav(args.date), ensure_ascii=False))
        elif args.mode == "round-trips":
            # v5.5.2: E4 counter — only complete round-trip chains.
            print(json.dumps(round_trips(), ensure_ascii=False))
        elif args.mode == "legacy-reconcile-audit":
            print(json.dumps(legacy_reconcile_audit(args.date),
                             ensure_ascii=False))
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
