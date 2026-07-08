"""Build and optionally push the daily trusted strategy performance review."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.ops.production_config import load_production_config
from scripts.ops.production_risk_governor import build_risk_governor_decision, summarize_recent_shadow
from scripts.ops.feishu_notifier import send_feishu_text_audited, strategy_identity_block
from scripts.strategy_display import strategy_display_name


PRODUCTION_CONFIG = load_production_config()
DEFAULT_RISK_PROFILE = str(PRODUCTION_CONFIG["risk_profile"])
DEFAULT_STRATEGY = str(PRODUCTION_CONFIG["primary_strategy"])
DEFAULT_TOP_N = int(PRODUCTION_CONFIG["top_n"])
DEFAULT_MAX_TOTAL_POSITIONS = int(PRODUCTION_CONFIG["max_total_positions"])
DEFAULT_POSITION_RATIO = float(PRODUCTION_CONFIG["position_ratio"])
DEFAULT_HOLD_DAYS = int(PRODUCTION_CONFIG["hold_days"])
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "exports" / "production_strategy_reviews"
DEFAULT_SIGNAL_RESEARCH_DIR = PROJECT_ROOT / "exports" / "signal_research"
DEFAULT_REVIEW_WINDOW_DAYS = 63

# Fallback chain: when the production-governed strategy requires corporate-action
# snapshots that aren't available, try these alternatives that don't need them.
_VOL_STRATEGY_FALLBACKS = [
    "production_governed_vol_position_v1_2b_dynamic_score",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
    "baseline_full_liquidity_detail_vol_position",
]


def _discover_latest_backtest_dir(
    base_dir: Path = DEFAULT_SIGNAL_RESEARCH_DIR,
    prefix: str = "trusted_account_backtest",
) -> Path:
    """Auto-discover the latest backtest output directory under base_dir.

    Looks for directories whose name contains *prefix* and returns the one with
    the most recent timestamp (directories are sorted by name, which works when
    names follow YYYYMMDD_HHMMSS_* convention).
    """
    candidates: list[Path] = []
    for candidate in base_dir.iterdir():
        if not candidate.is_dir():
            continue
        if prefix in candidate.name:
            candidates.append(candidate)

    if not candidates:
        raise FileNotFoundError(
            f"No backtest directories matching '{prefix}*' found under {base_dir}. "
            f"Run research_trusted_strategy_account_backtest.py first, or pass "
            f"--vol-backtest-dir / --adaptive-v22-backtest-dir / --dual-3m-backtest-dir explicitly."
        )

    # Sort by name (timestamps sort lexicographically when YYYYMMDD_HHMMSS_...)
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates[0]


# Auto-discover latest backtest directory. Falls back to the old hardcoded
# defaults only when no directory is found (keeps --vol-backtest-dir override working).
def _resolve_backtest_default(cache: list[Path]) -> Path:
    if not cache:
        try:
            cache.append(_discover_latest_backtest_dir(DEFAULT_SIGNAL_RESEARCH_DIR))
        except FileNotFoundError:
            # Return a sentinel that will cause a clear error downstream
            return DEFAULT_SIGNAL_RESEARCH_DIR / "NO_BACKTEST_DIR_FOUND"
    return cache[0]


_BACKTEST_DIR_CACHE: list[Path] = []
DEFAULT_VOL_BACKTEST_DIR: Path = DEFAULT_SIGNAL_RESEARCH_DIR  # will be resolved lazily
DEFAULT_ADAPTIVE_V22_BACKTEST_DIR: Path = DEFAULT_SIGNAL_RESEARCH_DIR
DEFAULT_DUAL_3M_BACKTEST_DIR: Path = DEFAULT_SIGNAL_RESEARCH_DIR


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _date_compact(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def _safe_json_value(value):
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    clean = frame.where(pd.notna(frame), None)
    return [{k: _safe_json_value(v) for k, v in row.items()} for row in clean.to_dict("records")]


def _pct(value, digits: int = 2) -> str:
    try:
        v = float(value)
    except Exception:
        return "-"
    if not math.isfinite(v):
        return "-"
    return f"{v * 100:.{digits}f}%"


def _money(value, digits: int = 0) -> str:
    try:
        v = float(value)
    except Exception:
        return "-"
    if not math.isfinite(v):
        return "-"
    return f"{v:,.{digits}f}"


def _num(value, digits: int = 2) -> str:
    try:
        v = float(value)
    except Exception:
        return "-"
    if not math.isfinite(v):
        return "-"
    return f"{v:.{digits}f}"


def _table_exists(engine, full_table_name: str) -> bool:
    parts = str(full_table_name).split(".")
    if len(parts) == 2:
        schema, table = parts
    else:
        schema, table = "chenyiyun", parts[0]
    sql = text(
        """
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = :schema AND table_name = :table
        """
    )
    with engine.connect() as conn:
        return int(conn.execute(sql, {"schema": schema, "table": table}).scalar() or 0) > 0


def _latest_trade_date(engine) -> str:
    if _table_exists(engine, "chenyiyun.ads_trusted_strategy_candidates"):
        value = None
        with engine.connect() as conn:
            value = conn.execute(
                text(
                    """
                    SELECT MAX(trade_date)
                    FROM chenyiyun.ads_trusted_strategy_candidates
                    WHERE strategy = :strategy
                    """
                ),
                {"strategy": DEFAULT_STRATEGY},
            ).scalar()
        if value is not None:
            return pd.Timestamp(value).strftime("%Y-%m-%d")

    candidates = [
        ("chenyiyun.ads_trusted_strategy_candidates", "trade_date"),
        ("chenyiyun.ads_local_strategy_orders", "trade_date"),
        ("chenyiyun.ads_trusted_strategy_shadow_daily", "execution_date"),
        ("chenyiyun.live_daily_snapshots", "snapshot_date"),
    ]
    dates = []
    with engine.connect() as conn:
        for table, col in candidates:
            if not _table_exists(engine, table):
                continue
            value = conn.execute(text(f"SELECT MAX({col}) FROM {table}")).scalar()
            if value is not None:
                dates.append(pd.Timestamp(value).strftime("%Y-%m-%d"))
    if not dates:
        raise RuntimeError("Cannot infer review date: no candidate/order/shadow/live dates found.")
    return max(dates)


def _load_summary_row(backtest_dir: Path, strategy: str) -> dict:
    path = backtest_dir / "trusted_account_backtest_summary.csv"
    if not path.exists():
        raise RuntimeError(f"Missing backtest summary: {path}")
    frame = pd.read_csv(path)
    matched = frame[frame["strategy"].astype(str).eq(strategy)]
    if matched.empty:
        raise RuntimeError(f"Strategy {strategy} not found in {path}")
    return {k: _safe_json_value(v) for k, v in matched.iloc[0].to_dict().items()}


def _load_window_rows(backtest_dir: Path, strategy: str) -> list[dict]:
    path = backtest_dir / "trusted_account_backtest_window_summary.csv"
    if not path.exists():
        raise RuntimeError(f"Missing backtest window summary: {path}")
    frame = pd.read_csv(path)
    matched = frame[frame["strategy"].astype(str).eq(strategy)].copy()
    if matched.empty:
        raise RuntimeError(f"Strategy {strategy} windows not found in {path}")
    order = {"3m": 0, "6m": 1, "1y": 2, "3y": 3}
    matched["_order"] = matched["window"].map(order).fillna(99)
    matched = matched.sort_values(["_order", "window"]).drop(columns=["_order"])
    return _records(matched)


def _resolve_strategy(backtest_dir: Path, strategy: str, fallbacks: list[str] | None = None) -> str:
    """Resolve the best available strategy name from the backtest summary.

    If *strategy* is found, return it. Otherwise try each fallback in order.
    Raises RuntimeError if no match is found.
    """
    path = backtest_dir / "trusted_account_backtest_summary.csv"
    if not path.exists():
        raise RuntimeError(f"Missing backtest summary: {path}")
    frame = pd.read_csv(path)
    available = set(frame["strategy"].astype(str).unique())
    for candidate in [strategy] + (fallbacks or []):
        if candidate in available:
            if candidate != strategy:
                print(f"[review] strategy '{strategy}' not found, using '{candidate}' (fallback)")
            return candidate
    raise RuntimeError(
        f"Strategy '{strategy}' (and fallbacks {fallbacks}) not found in {path}. "
        f"Available: {sorted(available)}"
    )


def _load_strategy_block(backtest_dir: Path, strategy: str, required: bool = True, fallbacks: list[str] | None = None) -> dict:
    resolved = _resolve_strategy(backtest_dir, strategy, fallbacks=fallbacks)
    return {
        "summary": _load_summary_row(backtest_dir, resolved),
        "windows": _load_window_rows(backtest_dir, resolved),
        "resolved_strategy": resolved,
    }


def _load_compare_rows(backtest_dir: Path) -> list[dict]:
    path = backtest_dir / "trusted_account_backtest_summary.csv"
    if not path.exists():
        raise RuntimeError(f"Missing comparison backtest summary: {path}")
    frame = pd.read_csv(path)
    cols = [
        "strategy",
        "first_date",
        "last_date",
        "final_equity",
        "total_return",
        "annualized_return",
        "max_drawdown",
        "avg_gross_exposure",
        "trade_count",
    ]
    existing = [c for c in cols if c in frame.columns]
    return _records(frame[existing].copy())


def _current_drawdown(nav_values: list[float]) -> float:
    if not nav_values:
        return 0.0
    peak = max(nav_values)
    last = nav_values[-1]
    return (last / peak - 1.0) if peak > 0 else 0.0


def compute_rolling_window_metrics(
    nav_frame: pd.DataFrame,
    strategy: str,
    review_date: str,
    window_days: int = DEFAULT_REVIEW_WINDOW_DAYS,
) -> dict:
    """Compute recent account-level performance from trusted NAV rows."""
    required = {"strategy", "trade_date", "nav"}
    missing = sorted(required - set(nav_frame.columns))
    if missing:
        raise RuntimeError(f"trusted_account_backtest_nav.csv missing columns: {', '.join(missing)}")
    if nav_frame.empty:
        raise RuntimeError("trusted_account_backtest_nav.csv is empty.")

    frame = nav_frame[nav_frame["strategy"].astype(str).eq(strategy)].copy()
    if frame.empty:
        raise RuntimeError(f"Strategy {strategy} not found in trusted_account_backtest_nav.csv")

    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    if "gross_exposure" in frame.columns:
        frame["gross_exposure"] = pd.to_numeric(frame["gross_exposure"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "nav"]).sort_values("trade_date")
    if frame.empty:
        raise RuntimeError(f"Strategy {strategy} has no valid NAV rows.")

    review_ts = pd.Timestamp(review_date)
    available = frame[frame["trade_date"] <= review_ts].copy()
    if available.empty:
        raise RuntimeError(f"No NAV rows on or before review date {review_date}.")

    last_date = available["trade_date"].max()
    window = available.tail(max(1, int(window_days))).copy()
    nav = window["nav"].astype(float).tolist()
    daily_returns = window["nav"].pct_change().dropna().astype(float).tolist()

    status = "PASS"
    warnings: list[str] = []
    if len(window) < int(window_days):
        status = "INSUFFICIENT"
        warnings.append(f"only_{len(window)}_trade_days_available_for_{window_days}_day_window")
    if last_date < review_ts:
        status = "STALE"
        warnings.append(f"nav_last_date_{last_date.strftime('%Y-%m-%d')}_before_review_date_{review_ts.strftime('%Y-%m-%d')}")

    total_return = (nav[-1] / nav[0] - 1.0) if len(nav) >= 2 and nav[0] > 0 else 0.0
    annualized_return = ((1.0 + total_return) ** (252.0 / max(len(window) - 1, 1)) - 1.0) if len(nav) >= 2 else 0.0
    max_drawdown = 0.0
    peak = nav[0] if nav else 1.0
    for value in nav:
        peak = max(peak, value)
        drawdown = (value / peak - 1.0) if peak > 0 else 0.0
        max_drawdown = min(max_drawdown, drawdown)

    volatility = 0.0
    sharpe = None
    if len(daily_returns) >= 2:
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        volatility = variance ** 0.5 * (252 ** 0.5)
        sharpe = (mean_ret / (variance ** 0.5) * (252 ** 0.5)) if variance > 0 else None
    calmar = (annualized_return / abs(max_drawdown)) if max_drawdown < 0 else None
    avg_exposure = None
    if "gross_exposure" in window.columns:
        exposure = window["gross_exposure"].dropna()
        avg_exposure = float(exposure.mean()) if not exposure.empty else None

    return {
        "status": status,
        "freshness_ok": status == "PASS",
        "strategy": strategy,
        "requested_trade_days": int(window_days),
        "actual_trade_days": int(len(window)),
        "window_start": window["trade_date"].min().strftime("%Y-%m-%d"),
        "window_end": last_date.strftime("%Y-%m-%d"),
        "review_date": review_ts.strftime("%Y-%m-%d"),
        "total_return": float(total_return),
        "annualized_return": float(annualized_return),
        "max_drawdown": float(max_drawdown),
        "current_drawdown": float(_current_drawdown(nav)),
        "win_rate": float(sum(1 for r in daily_returns if r > 0) / len(daily_returns)) if daily_returns else None,
        "worst_day": float(min(daily_returns)) if daily_returns else None,
        "volatility": float(volatility),
        "sharpe": float(sharpe) if sharpe is not None and math.isfinite(sharpe) else None,
        "calmar": float(calmar) if calmar is not None and math.isfinite(calmar) else None,
        "avg_gross_exposure": avg_exposure,
        "warnings": warnings,
    }


def _load_rolling_window_metrics(backtest_dir: Path, strategy: str, review_date: str, window_days: int) -> dict:
    path = backtest_dir / "trusted_account_backtest_nav.csv"
    if not path.exists():
        raise RuntimeError(f"Missing backtest NAV: {path}")
    frame = pd.read_csv(path)
    return compute_rolling_window_metrics(frame, strategy, review_date, window_days)


def _load_backtests(args: argparse.Namespace) -> dict:
    primary_dir = Path(args.vol_backtest_dir)
    adaptive_dir = Path(args.adaptive_v22_backtest_dir)
    dual_dir = Path(args.dual_3m_backtest_dir)
    review_date = _normalize_date(getattr(args, "date", None)) or getattr(args, "review_date", None)
    review_window_days = int(getattr(args, "review_window_days", DEFAULT_REVIEW_WINDOW_DAYS))
    vol_strategy = getattr(args, "vol_backtest_strategy", None) or DEFAULT_STRATEGY
    primary = _load_strategy_block(primary_dir, vol_strategy, required=True, fallbacks=_VOL_STRATEGY_FALLBACKS)
    resolved = primary.get("resolved_strategy", vol_strategy)
    if review_date:
        primary["rolling_window_3m"] = _load_rolling_window_metrics(
            primary_dir,
            resolved,
            review_date,
            review_window_days,
        )
    return {
        "source_dirs": {
            "primary_governed": str(primary_dir),
            "adaptive_market_style_v22": str(adaptive_dir),
            "dual_system_3m": str(dual_dir),
        },
        "primary": primary,
        "primary_shadow_adaptive": _load_strategy_block(primary_dir, "adaptive_market_style", required=False),
        "adaptive_market_style_v22": _load_strategy_block(adaptive_dir, "adaptive_market_style", required=False),
        "dual_system_3m_compare": _load_compare_rows(dual_dir),
    }


def _read_sql(engine, sql: str, params: dict | None = None) -> pd.DataFrame:
    return pd.read_sql(text(sql), engine, params=params or {})


def _existing_columns(engine, full_table_name: str, desired: list[str]) -> list[str]:
    schema, table = full_table_name.split(".", 1) if "." in full_table_name else ("chenyiyun", full_table_name)
    try:
        frame = _read_sql(
            engine,
            """
            SELECT column_name AS column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            """,
            {"schema": schema, "table": table},
        )
    except Exception:
        return desired
    available = set(frame["column_name"].astype(str).tolist()) if not frame.empty else set()
    return [col for col in desired if col in available]


def _select_columns(engine, full_table_name: str, desired: list[str]) -> str:
    columns = _existing_columns(engine, full_table_name, desired)
    if not columns:
        raise RuntimeError(f"No expected columns found in {full_table_name}.")
    return ", ".join(f"`{col}`" for col in columns)


def _load_candidates(engine, review_date: str) -> tuple[pd.DataFrame, dict]:
    if not _table_exists(engine, "chenyiyun.ads_trusted_strategy_candidates"):
        raise RuntimeError("Missing table chenyiyun.ads_trusted_strategy_candidates.")
    cols = _select_columns(
        engine,
        "chenyiyun.ads_trusted_strategy_candidates",
        [
            "trade_date", "strategy", "rank_no", "rank", "symbol", "ts_code", "stock_name", "name",
            "industry", "effective_weight", "target_weight", "weight", "rank_score", "latest_close",
            "dynamic_factor_score", "liquidity_detail_score", "s_liquidity", "market_liquidity_bucket",
            "index_bucket", "output_json_path",
        ],
    )
    meta = _read_sql(
        engine,
        """
        SELECT MAX(trade_date) AS latest_date, COUNT(*) AS total_rows
        FROM chenyiyun.ads_trusted_strategy_candidates
        """,
    ).iloc[0].to_dict()
    frame = _read_sql(
        engine,
        """
        SELECT {cols}
        FROM chenyiyun.ads_trusted_strategy_candidates
        WHERE trade_date = :review_date AND strategy = :strategy
        ORDER BY rank_no, symbol
        """.format(cols=cols),
        {"review_date": review_date, "strategy": DEFAULT_STRATEGY},
    )
    if frame.empty:
        raise RuntimeError(f"No production candidates for {review_date} / {DEFAULT_STRATEGY}.")
    return frame, {k: _safe_json_value(v) for k, v in meta.items()}


def _load_orders(engine, review_date: str) -> tuple[pd.DataFrame, dict]:
    if not _table_exists(engine, "chenyiyun.ads_local_strategy_orders"):
        return pd.DataFrame(), {"warning": "missing table ads_local_strategy_orders"}
    cols = _select_columns(
        engine,
        "chenyiyun.ads_local_strategy_orders",
        [
            "trade_date", "strategy", "ts_code", "symbol", "stock_name", "name", "side", "price",
            "delta_shares", "allocated_shares", "target_weight", "delta_weight", "order_status",
            "reason", "create_time", "updated_at",
        ],
    )
    meta = _read_sql(
        engine,
        """
        SELECT MAX(trade_date) AS latest_date, COUNT(*) AS total_rows
        FROM chenyiyun.ads_local_strategy_orders
        """,
    ).iloc[0].to_dict()
    frame = _read_sql(
        engine,
        """
        SELECT {cols}
        FROM chenyiyun.ads_local_strategy_orders
        WHERE trade_date = :review_date
        ORDER BY side DESC, ts_code
        """.format(cols=cols),
        {"review_date": review_date},
    )
    return frame, {k: _safe_json_value(v) for k, v in meta.items()}


def _load_recent_shadow_history(engine, review_date: str, lookback_days: int = 5) -> dict:
    if not _table_exists(engine, "chenyiyun.ads_trusted_strategy_shadow_daily"):
        return summarize_recent_shadow([])
    frame = _read_sql(
        engine,
        """
        SELECT execution_date, validation_status, validation_actions, shadow_vs_theory_gap
        FROM chenyiyun.ads_trusted_strategy_shadow_daily
        WHERE execution_date <= :review_date
        ORDER BY execution_date DESC
        LIMIT :lookback_days
        """,
        {"review_date": review_date, "lookback_days": int(max(1, lookback_days))},
    )
    return summarize_recent_shadow(_records(frame))


def _load_candidate_output_params(candidates: pd.DataFrame) -> dict:
    if candidates.empty or "output_json_path" not in candidates.columns:
        return {}
    path_raw = str(candidates.iloc[0].get("output_json_path") or "").strip()
    if not path_raw:
        return {}
    path = Path(path_raw)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload.get("params") or {})


def _load_production_risk_decision(engine, review_date: str) -> dict:
    if not _table_exists(engine, "chenyiyun.ads_production_risk_decisions"):
        return {}
    cols = _select_columns(
        engine,
        "chenyiyun.ads_production_risk_decisions",
        [
            "trade_date", "risk_decision", "target_position_ratio", "fallback_strategy",
            "allow_new_buys", "reasons_json", "risk_governor_version", "created_at",
        ],
    )
    frame = _read_sql(
        engine,
        """
        SELECT {cols}
        FROM chenyiyun.ads_production_risk_decisions
        WHERE trade_date = :review_date
        LIMIT 1
        """.format(cols=cols),
        {"review_date": review_date},
    )
    if frame.empty:
        return {}
    row = _records(frame)[0]
    reasons = row.get("reasons_json")
    if isinstance(reasons, str):
        try:
            row["reasons"] = json.loads(reasons)
        except Exception:
            row["reasons"] = [reasons]
    return row


def _load_shadow(engine, review_date: str) -> tuple[pd.DataFrame, dict, dict]:
    if not _table_exists(engine, "chenyiyun.ads_trusted_strategy_shadow_daily"):
        return pd.DataFrame(), {}, {"warning": "missing table ads_trusted_strategy_shadow_daily"}
    summary_cols = _select_columns(
        engine,
        "chenyiyun.ads_trusted_strategy_shadow_daily",
        [
            "signal_date", "execution_date", "validation_status", "validation_actions",
            "total_orders", "executable_orders", "blocked_orders", "warning_orders",
            "avg_slippage_bps", "max_adverse_slippage_bps", "shadow_vs_theory_gap",
        ],
    )
    summary = _read_sql(
        engine,
        """
        SELECT {cols}
        FROM chenyiyun.ads_trusted_strategy_shadow_daily
        WHERE execution_date = :review_date
        ORDER BY execution_date DESC
        LIMIT 1
        """.format(cols=summary_cols),
        {"review_date": review_date},
    )
    meta = _read_sql(
        engine,
        """
        SELECT MAX(execution_date) AS latest_date, COUNT(*) AS total_rows
        FROM chenyiyun.ads_trusted_strategy_shadow_daily
        """,
    ).iloc[0].to_dict()
    fills = pd.DataFrame()
    if not summary.empty and _table_exists(engine, "chenyiyun.ads_trusted_strategy_shadow_fills"):
        fill_cols = _select_columns(
            engine,
            "chenyiyun.ads_trusted_strategy_shadow_fills",
            [
                "signal_date", "execution_date", "ts_code", "symbol", "stock_name", "name", "side",
                "planned_price", "execution_price", "shares", "tradable_flag", "block_reason",
                "slippage_bps", "warning_reason",
            ],
        )
        signal_date = pd.Timestamp(summary.iloc[0]["signal_date"]).strftime("%Y-%m-%d")
        fills = _read_sql(
            engine,
            """
            SELECT {cols}
            FROM chenyiyun.ads_trusted_strategy_shadow_fills
            WHERE signal_date = :signal_date AND execution_date = :review_date
            ORDER BY tradable_flag ASC, side DESC, ts_code
            """.format(cols=fill_cols),
            {"signal_date": signal_date, "review_date": review_date},
        )
    return fills, (_records(summary)[0] if not summary.empty else {}), {k: _safe_json_value(v) for k, v in meta.items()}


def _load_live(engine, review_date: str) -> dict:
    result = {
        "snapshot": {},
        "positions": [],
        "recent_trades": [],
        "meta": {},
        "warnings": [],
    }
    if _table_exists(engine, "chenyiyun.live_daily_snapshots"):
        snap_cols = _select_columns(
            engine,
            "chenyiyun.live_daily_snapshots",
            [
                "snapshot_date", "trade_date", "total_equity", "cash", "positions_value",
                "daily_return_pct", "daily_return", "hs300_return_pct", "excess_return_pct",
                "created_at",
            ],
        )
        meta = _read_sql(
            engine,
            """
            SELECT MAX(snapshot_date) AS latest_date, COUNT(*) AS total_rows
            FROM chenyiyun.live_daily_snapshots
            """,
        ).iloc[0].to_dict()
        result["meta"]["live_daily_snapshots"] = {k: _safe_json_value(v) for k, v in meta.items()}
        snap = _read_sql(
            engine,
            """
            SELECT {cols}
            FROM chenyiyun.live_daily_snapshots
            WHERE snapshot_date <= :review_date
            ORDER BY snapshot_date DESC
            LIMIT 1
            """.format(cols=snap_cols),
            {"review_date": review_date},
        )
        if not snap.empty:
            result["snapshot"] = _records(snap)[0]
        else:
            result["warnings"].append("live_daily_snapshots has no snapshot on or before review date")
    else:
        result["warnings"].append("missing table live_daily_snapshots")

    if _table_exists(engine, "chenyiyun.live_positions"):
        pos_cols = _existing_columns(
            engine,
            "chenyiyun.live_positions",
            ["symbol", "ts_code", "stock_name", "name", "shares", "current_price", "cost_price", "market_value", "unrealized_pnl", "updated_at"],
        )
        computed_market_value = ""
        if "market_value" not in pos_cols and {"shares", "current_price"}.issubset(set(pos_cols)):
            computed_market_value = ", COALESCE(shares, 0) * COALESCE(current_price, 0) AS market_value"
        if not pos_cols:
            raise RuntimeError("No expected columns found in chenyiyun.live_positions.")
        select_cols = ", ".join(f"`{col}`" for col in pos_cols) + computed_market_value
        order_col = "market_value" if ("market_value" in pos_cols or computed_market_value) else pos_cols[0]
        positions = _read_sql(
            engine,
            f"""
            SELECT {select_cols}
            FROM chenyiyun.live_positions
            ORDER BY `{order_col}` DESC
            """,
        )
        result["positions"] = _records(positions)
        result["meta"]["live_positions"] = {"rows": int(len(positions))}
        if positions.empty:
            result["warnings"].append("live_positions is empty; live realized strategy trend cannot be judged")
    else:
        result["warnings"].append("missing table live_positions")

    if _table_exists(engine, "chenyiyun.live_trades"):
        trade_cols = _select_columns(
            engine,
            "chenyiyun.live_trades",
            ["trade_date", "symbol", "ts_code", "stock_name", "name", "side", "price", "shares", "amount", "reason", "created_at"],
        )
        trades = _read_sql(
            engine,
            """
            SELECT {cols}
            FROM chenyiyun.live_trades
            ORDER BY trade_date DESC, created_at DESC
            LIMIT 10
            """.format(cols=trade_cols),
        )
        result["recent_trades"] = _records(trades)
        result["meta"]["live_trades"] = {"rows_loaded": int(len(trades))}
    else:
        result["warnings"].append("missing table live_trades")
    return result


def _summarize_candidates(candidates: pd.DataFrame) -> dict:
    d = candidates.copy()
    for col in ("effective_weight", "target_weight", "weight"):
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    weight_col = "effective_weight" if "effective_weight" in d.columns else ("target_weight" if "target_weight" in d.columns else "weight")
    industry_col = "industry" if "industry" in d.columns else None
    industry_counts = {}
    industry_weight = {}
    if industry_col:
        industry_counts = d[industry_col].fillna("未知").astype(str).value_counts().to_dict()
        if weight_col in d.columns:
            industry_weight = d.groupby(d[industry_col].fillna("未知").astype(str))[weight_col].sum().sort_values(ascending=False).to_dict()
    return {
        "rows": int(len(d)),
        "weight_col": weight_col if weight_col in d.columns else None,
        "weight_sum": float(d[weight_col].fillna(0).sum()) if weight_col in d.columns else None,
        "industry_counts": industry_counts,
        "industry_weight": {k: float(v) for k, v in industry_weight.items()},
    }


def _summarize_orders(orders: pd.DataFrame) -> dict:
    if orders.empty:
        return {
            "rows": 0,
            "buy_orders": 0,
            "sell_orders": 0,
            "planned_amount": 0.0,
            "target_weight_sum": None,
            "status_counts": {},
        }
    d = orders.copy()
    d["side"] = d["side"].astype(str).str.upper() if "side" in d.columns else ""
    for col in ("delta_shares", "price", "target_weight", "delta_weight"):
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    amount = 0.0
    if {"delta_shares", "price"}.issubset(d.columns):
        amount = float((d["delta_shares"].abs() * d["price"]).fillna(0).sum())
    return {
        "rows": int(len(d)),
        "buy_orders": int(d["side"].eq("BUY").sum()) if "side" in d.columns else 0,
        "sell_orders": int(d["side"].eq("SELL").sum()) if "side" in d.columns else 0,
        "planned_amount": amount,
        "target_weight_sum": float(d["target_weight"].fillna(0).sum()) if "target_weight" in d.columns else None,
        "status_counts": d["order_status"].fillna("planned").astype(str).value_counts().to_dict() if "order_status" in d.columns else {},
    }


def _backtest_window_label(summary: dict) -> str:
    start = summary.get("first_date") or "-"
    end = summary.get("last_date") or "-"
    days = summary.get("trading_days")
    try:
        days_text = f"{int(days)}个交易日"
    except Exception:
        days_text = "交易日数未知"
    return f"{start}~{end}（{days_text}）"


def _primary_backtest_strategy(backtests: dict) -> str:
    return str(backtests.get("primary", {}).get("resolved_strategy") or DEFAULT_STRATEGY)


def _build_decision(backtests: dict, shadow_summary: dict, shadow_history: dict, live: dict, candidate_params: dict, risk_decision_row: dict | None = None) -> dict:
    primary = backtests["primary"]["summary"]
    adaptive = backtests["adaptive_market_style_v22"]["summary"]
    primary_mdd = float(primary.get("max_drawdown") or 0)
    adaptive_mdd = float(adaptive.get("max_drawdown") or 0)
    blocked = int(shadow_summary.get("blocked_orders") or 0) if shadow_summary else 0
    live_positions = live.get("positions") or []
    adaptive_state = {
        "active_role": candidate_params.get("market_style_state"),
        "market_liquidity_bucket": candidate_params.get("market_liquidity_bucket"),
        "industry_state": candidate_params.get("industry_state"),
        "champion_score": candidate_params.get("champion_score"),
        "avg_vol_20": candidate_params.get("avg_vol_20"),
        "market_state": candidate_params.get("market_state"),
        "index_bucket": candidate_params.get("index_bucket"),
    }
    risk_decision_row = dict(risk_decision_row or {})
    if risk_decision_row:
        governor = {
            "risk_decision": risk_decision_row.get("risk_decision"),
            "target_position_ratio": risk_decision_row.get("target_position_ratio"),
            "fallback_strategy": risk_decision_row.get("fallback_strategy"),
            "allow_new_buys": bool(risk_decision_row.get("allow_new_buys", True)),
            "reasons": risk_decision_row.get("reasons") or [],
            "risk_governor_version": risk_decision_row.get("risk_governor_version") or "v1",
        }
    else:
        governor = build_risk_governor_decision(PRODUCTION_CONFIG, adaptive_state, shadow_history)
    decision = "继续运行飞书订单草案但不升仓；保留 adaptive_market_style v2.2 作为风控锚"
    reasons = [
        f"主策略当前回测窗口（{_backtest_window_label(primary)}）累计{_pct(primary.get('total_return'))}，最大回撤{_pct(primary_mdd)}。",
        f"adaptive_market_style v2.2同窗回撤{_pct(adaptive_mdd)}，适合做降风险参照。",
    ]
    if blocked:
        decision = "继续运行但需人工复核不可成交订单；若连续出现成交受阻，降到 defensive 或降低仓位"
        reasons.append(f"今日影子盘不可成交订单 {blocked} 个。")
    if governor.get("risk_decision") and governor.get("risk_decision") != "normal":
        decision = f"风险总闸触发：{governor.get('risk_decision')}，目标仓位 {_pct(governor.get('target_position_ratio'))}"
        reasons.append(" / ".join(str(item) for item in governor.get("reasons") or []))
    if not live_positions:
        reasons.append("实盘持仓为空或同步不足，不能用现有 live 表评价真实收益趋势。")
    return {"decision": decision, "reasons": reasons, "risk_governor": governor}


def _format_candidate_line(row: dict) -> str:
    rank = row.get("rank_no") or row.get("rank") or ""
    symbol = row.get("symbol") or row.get("ts_code") or ""
    name = row.get("stock_name") or row.get("name") or ""
    industry = row.get("industry") or "-"
    weight = row.get("effective_weight", row.get("target_weight", row.get("weight")))
    return f"{rank}. {symbol} {name} {industry} 权重{_pct(weight)}"


def _format_feishu(payload: dict) -> str:
    bt = payload["backtests"]
    primary = bt["primary"]["summary"]
    rolling = bt["primary"].get("rolling_window_3m") or {}
    adaptive = bt["adaptive_market_style_v22"]["summary"]
    primary_strategy = _primary_backtest_strategy(bt)
    primary_window = _backtest_window_label(primary)
    adaptive_window = _backtest_window_label(adaptive)
    candidate_summary = payload["current"]["candidate_summary"]
    order_summary = payload["current"]["order_summary"]
    shadow = payload["current"].get("shadow_summary") or {}
    shadow_history = payload["current"].get("shadow_history") or {}
    live = payload["current"].get("live") or {}
    snapshot = live.get("snapshot") or {}
    decision = payload["judgement"]
    lines = [
        "【核心精选策略收益评估】",
        f"日期：{payload['params']['review_date']}",
        strategy_identity_block(),
        f"实际回测策略：{strategy_display_name(primary_strategy, include_id=True)}",
        f"风险总闸版本：{decision.get('risk_governor', {}).get('risk_governor_version') or 'v1'} / 目标仓位 {_pct(decision.get('risk_governor', {}).get('target_position_ratio'))}",
        f"结论：{decision['decision']}",
        "",
        "最近3个月收益评估：",
        f"- 状态：{rolling.get('status') or '-'}；区间 {rolling.get('window_start') or '-'}~{rolling.get('window_end') or '-'}，交易日 {rolling.get('actual_trade_days') or 0}/{rolling.get('requested_trade_days') or 0}",
        f"- 收益 {_pct(rolling.get('total_return'))}，年化 {_pct(rolling.get('annualized_return'))}，最大回撤 {_pct(rolling.get('max_drawdown'))}，当前回撤 {_pct(rolling.get('current_drawdown'))}",
        f"- 胜率 {_pct(rolling.get('win_rate'))}，最差单日 {_pct(rolling.get('worst_day'))}，波动率 {_pct(rolling.get('volatility'))}，Sharpe {_num(rolling.get('sharpe'))}，Calmar {_num(rolling.get('calmar'))}，平均暴露 {_pct(rolling.get('avg_gross_exposure'))}",
        "",
        "当前回测窗口收益/回撤：",
        f"- 主策略窗口 {primary_window}：累计{_pct(primary.get('total_return'))}，年化{_pct(primary.get('annualized_return'))}，最大回撤{_pct(primary.get('max_drawdown'))}，期末权益{_money(primary.get('final_equity'))}",
        f"- 风控影子v2.2窗口 {adaptive_window}：累计{_pct(adaptive.get('total_return'))}，年化{_pct(adaptive.get('annualized_return'))}，最大回撤{_pct(adaptive.get('max_drawdown'))}",
        "",
        "当前运行：",
        f"- 候选 {candidate_summary['rows']} 只，目标仓位合计{_pct(candidate_summary.get('weight_sum'))}；行业："
        + "、".join(f"{k}{v}只" for k, v in list(candidate_summary.get("industry_counts", {}).items())[:5]),
        f"- 订单 {order_summary['rows']} 笔（BUY {order_summary['buy_orders']} / SELL {order_summary['sell_orders']}），计划金额 {_money(order_summary['planned_amount'])}",
    ]
    if shadow:
        lines.append(
            f"- 影子盘：可成交 {shadow.get('executable_orders', 0)} / 不可成交 {shadow.get('blocked_orders', 0)}，"
            f"均值滑点 {_num(shadow.get('avg_slippage_bps'), 1)} bps；"
            f"验收 {shadow.get('validation_status') or '-'} / {shadow.get('validation_actions') or '-'}"
        )
    else:
        lines.append("- 影子盘：今日无汇总记录，按数据缺口标注")
    if shadow_history:
        lines.append(
            f"- 最近影子盘：fail_streak {shadow_history.get('fail_streak', 0)}，"
            f"worst_action {shadow_history.get('worst_action') or '-'}"
        )
    if snapshot:
        lines.append(
            f"- 实盘快照：总权益 {_money(snapshot.get('total_equity'))}，现金 {_money(snapshot.get('cash'))}，"
            f"持仓市值 {_money(snapshot.get('positions_value'))}，日收益 {_pct(snapshot.get('daily_return_pct'))}"
        )
    for warning in live.get("warnings", [])[:2]:
        lines.append(f"- 数据提醒：{warning}")
    for warning in rolling.get("warnings", [])[:2]:
        lines.append(f"- 3个月评估提醒：{warning}")
    lines.append("")
    lines.append("Top5：")
    for row in payload["current"]["candidates"][:5]:
        lines.append("- " + _format_candidate_line(row))
    lines.append("")
    lines.append(f"报告：{payload['outputs']['markdown_path']}")
    return "\n".join(lines)


def _markdown_table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    lines = ["|" + "|".join(label for label, _ in columns) + "|", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        values = []
        for _, key in columns:
            value = row.get(key)
            if key in {"total_return", "annualized_return", "max_drawdown", "current_drawdown", "win_rate", "worst_day", "volatility", "avg_gross_exposure", "effective_weight", "target_weight", "weight"}:
                values.append(_pct(value))
            elif key in {"final_equity", "planned_amount", "execution_amount"}:
                values.append(_money(value))
            else:
                values.append(str(_safe_json_value(value) if value is not None else "-"))
        lines.append("|" + "|".join(values) + "|")
    return lines


def _format_markdown(payload: dict) -> str:
    bt = payload["backtests"]
    primary = bt["primary"]["summary"]
    rolling = bt["primary"].get("rolling_window_3m") or {}
    adaptive = bt["adaptive_market_style_v22"]["summary"]
    primary_strategy = _primary_backtest_strategy(bt)
    primary_window = _backtest_window_label(primary)
    adaptive_window = _backtest_window_label(adaptive)
    current = payload["current"]
    shadow_history = current.get("shadow_history") or {}
    lines = [
        f"# 核心精选策略收益评估 - {payload['params']['review_date']}",
        "",
        "## 结论",
        "",
        f"- {payload['judgement']['decision']}",
    ]
    lines.extend(f"- {item}" for item in payload["judgement"]["reasons"])
    lines.extend(
        [
            "",
            "## 生产口径",
            "",
            f"- 风险档：`{DEFAULT_RISK_PROFILE}`",
            f"- 主策略：`{DEFAULT_STRATEGY}`",
            f"- 实际回测策略：`{primary_strategy}`",
            f"- TopN：{DEFAULT_TOP_N}；总持仓上限：{DEFAULT_MAX_TOTAL_POSITIONS}；持有期：{DEFAULT_HOLD_DAYS} 个交易日；默认仓位：{_pct(DEFAULT_POSITION_RATIO)}",
            f"- 配置文件：`{PRODUCTION_CONFIG.get('config_path')}`",
            "- 回测口径：T 日信号、T+1 执行、账户级、初始资金 50 万，成本/滑点沿用既有回测导出。",
            "",
            "## 最近3个月收益评估",
            "",
            f"- 状态：`{rolling.get('status') or '-'}`；freshness_ok={rolling.get('freshness_ok')}",
            f"- 区间：{rolling.get('window_start') or '-'} ~ {rolling.get('window_end') or '-'}；交易日：{rolling.get('actual_trade_days') or 0}/{rolling.get('requested_trade_days') or 0}",
            f"- 累计收益：{_pct(rolling.get('total_return'))}；年化收益：{_pct(rolling.get('annualized_return'))}；最大回撤：{_pct(rolling.get('max_drawdown'))}；当前回撤：{_pct(rolling.get('current_drawdown'))}",
            f"- 胜率：{_pct(rolling.get('win_rate'))}；最差单日：{_pct(rolling.get('worst_day'))}；波动率：{_pct(rolling.get('volatility'))}；Sharpe：{_num(rolling.get('sharpe'))}；Calmar：{_num(rolling.get('calmar'))}；平均暴露：{_pct(rolling.get('avg_gross_exposure'))}",
            *[f"- 数据提醒：{warning}" for warning in rolling.get("warnings", [])],
            "",
            "## 当前回测窗口",
            "",
            "|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|",
            "|---|---|---:|---:|---:|---:|---:|---:|",
            f"|主策略窗口（{primary_strategy}）|{primary_window}|{_money(primary.get('final_equity'))}|{_pct(primary.get('total_return'))}|{_pct(primary.get('annualized_return'))}|{_pct(primary.get('max_drawdown'))}|{_pct(primary.get('avg_gross_exposure'))}|{primary.get('trade_count')}|",
            f"|adaptive_market_style v2.2窗口|{adaptive_window}|{_money(adaptive.get('final_equity'))}|{_pct(adaptive.get('total_return'))}|{_pct(adaptive.get('annualized_return'))}|{_pct(adaptive.get('max_drawdown'))}|{_pct(adaptive.get('avg_gross_exposure'))}|{adaptive.get('trade_count')}|",
            "",
            "### 主策略近期窗口",
            "",
        ]
    )
    lines.extend(_markdown_table(bt["primary"]["windows"], [("窗口", "window"), ("起始", "window_start"), ("结束", "window_end"), ("收益", "total_return"), ("最大回撤", "max_drawdown"), ("平均暴露", "avg_gross_exposure")]))
    lines.extend(["", "### 策略同窗对照", ""])
    lines.extend(_markdown_table(bt["dual_system_3m_compare"], [("策略", "strategy"), ("收益", "total_return"), ("年化", "annualized_return"), ("最大回撤", "max_drawdown"), ("平均暴露", "avg_gross_exposure"), ("交易数", "trade_count")]))
    lines.extend(["", "## 当前候选与订单", ""])
    lines.extend(_markdown_table(current["candidates"][:10], [("排名", "rank_no"), ("代码", "symbol"), ("名称", "stock_name"), ("行业", "industry"), ("权重", "effective_weight")]))
    lines.extend(
        [
            "",
            f"- 候选权重合计：{_pct(current['candidate_summary'].get('weight_sum'))}",
            "- 行业集中：" + "、".join(f"{k}{v}只" for k, v in current["candidate_summary"].get("industry_counts", {}).items()),
            f"- 订单：{current['order_summary']['rows']} 笔，BUY {current['order_summary']['buy_orders']} / SELL {current['order_summary']['sell_orders']}，计划金额 {_money(current['order_summary']['planned_amount'])}",
            "",
            "## 影子盘与实盘",
            "",
        ]
    )
    shadow = current.get("shadow_summary") or {}
    if shadow:
        lines.extend(
            [
                f"- 影子盘信号日：{shadow.get('signal_date')}；执行日：{shadow.get('execution_date')}",
                f"- 可成交：{shadow.get('executable_orders')}；不可成交：{shadow.get('blocked_orders')}；警告：{shadow.get('warning_orders')}；平均滑点：{_num(shadow.get('avg_slippage_bps'), 1)} bps",
                f"- 验收：{shadow.get('validation_status') or '-'} / {shadow.get('validation_actions') or '-'}；shadow/theory gap：{_pct(shadow.get('shadow_vs_theory_gap'))}",
            ]
        )
    else:
        lines.append("- 影子盘：当前日期无汇总记录。")
    if shadow_history:
        lines.append(
            f"- 最近影子盘状态：fail_streak={shadow_history.get('fail_streak', 0)}；worst_action={shadow_history.get('worst_action') or '-'}"
        )
    live = current.get("live") or {}
    snapshot = live.get("snapshot") or {}
    if snapshot:
        lines.append(f"- 实盘快照：总权益 {_money(snapshot.get('total_equity'))}，现金 {_money(snapshot.get('cash'))}，持仓市值 {_money(snapshot.get('positions_value'))}，日收益 {_pct(snapshot.get('daily_return_pct'))}")
    for warning in live.get("warnings", []):
        lines.append(f"- 数据提醒：{warning}")
    lines.extend(["", "## 数据来源", ""])
    for name, path in bt["source_dirs"].items():
        lines.append(f"- {name}: `{path}`")
    return "\n".join(lines) + "\n"


def run_review(args: argparse.Namespace) -> dict:
    engine = create_engine(build_sqlalchemy_url())
    review_date = _normalize_date(args.date) or _latest_trade_date(engine)
    setattr(args, "review_date", review_date)
    backtests = _load_backtests(args)
    candidates, candidate_meta = _load_candidates(engine, review_date)
    candidate_params = _load_candidate_output_params(candidates)
    risk_decision_row = _load_production_risk_decision(engine, review_date)
    orders, order_meta = _load_orders(engine, review_date)
    shadow_fills, shadow_summary, shadow_meta = _load_shadow(engine, review_date)
    shadow_history = _load_recent_shadow_history(engine, review_date)
    live = _load_live(engine, review_date)
    current = {
        "candidate_meta": candidate_meta,
        "candidate_params": candidate_params,
        "risk_decision": risk_decision_row,
        "order_meta": order_meta,
        "shadow_meta": shadow_meta,
        "candidates": _records(candidates),
        "orders": _records(orders),
        "shadow_summary": shadow_summary,
        "shadow_history": shadow_history,
        "shadow_fills": _records(shadow_fills),
        "live": live,
        "candidate_summary": _summarize_candidates(candidates),
        "order_summary": _summarize_orders(orders),
    }
    payload = {
        "params": {
            "review_date": review_date,
            "risk_profile": DEFAULT_RISK_PROFILE,
            "strategy": DEFAULT_STRATEGY,
            "top_n": DEFAULT_TOP_N,
            "max_total_positions": DEFAULT_MAX_TOTAL_POSITIONS,
            "position_ratio": DEFAULT_POSITION_RATIO,
            "hold_days": DEFAULT_HOLD_DAYS,
            "execution_mode": str(PRODUCTION_CONFIG["execution_mode"]),
            "shadow_risk_strategy": str(PRODUCTION_CONFIG["shadow_risk_strategy"]),
            "shadow_version": str(PRODUCTION_CONFIG["shadow_version"]),
            "config_path": str(PRODUCTION_CONFIG["config_path"]),
            "review_window_days": int(getattr(args, "review_window_days", DEFAULT_REVIEW_WINDOW_DAYS)),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "backtests": backtests,
        "current": current,
        "judgement": {},
        "outputs": {},
        "notify_result": None,
    }
    payload["judgement"] = _build_decision(backtests, shadow_summary, shadow_history, live, candidate_params, risk_decision_row)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_root) / f"{timestamp}_{_date_compact(review_date)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "strategy_performance_review.json"
    md_path = out_dir / "strategy_performance_review.md"
    feishu_path = out_dir / "strategy_performance_review_feishu.txt"
    payload["outputs"] = {
        "output_dir": str(out_dir),
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "feishu_text_path": str(feishu_path),
    }

    feishu_text = _format_feishu(payload)
    if args.historical_reissue:
        feishu_text = "【历史补发】\n" + feishu_text
    markdown = _format_markdown(payload)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    feishu_path.write_text(feishu_text, encoding="utf-8")

    if args.notify_feishu:
        ok, reason = send_feishu_text_audited(
            engine,
            feishu_text,
            business_date=_date_compact(review_date),
            notification_type="trusted_strategy_performance_review",
            task_name="trusted_strategy_performance_review",
            dedupe_key=f"trusted_strategy_performance_review:{_date_compact(review_date)}",
        )
        payload["notify_result"] = reason
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if not ok:
            print(f"[WARN] Feishu notification queued for retry: {reason}")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build trusted strategy performance review and optionally push Feishu.")
    parser.add_argument("--date", default=None, help="Review date YYYY-MM-DD or YYYYMMDD. Defaults to latest production date.")
    parser.add_argument("--notify-feishu", action="store_true", help="Send a standalone Feishu strategy performance review.")
    parser.add_argument("--historical-reissue", action="store_true", help="Prefix the notification as a historical reissue.")
    parser.add_argument("--review-window-days", type=int, default=DEFAULT_REVIEW_WINDOW_DAYS, help="Recent performance review window in trading days. Defaults to 63.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    _resolved = str(_resolve_backtest_default(_BACKTEST_DIR_CACHE))
    parser.add_argument("--vol-backtest-dir", default=_resolved)
    parser.add_argument(
        "--vol-backtest-strategy",
        default=None,
        help="Override the strategy name to load from the vol backtest summary. "
             "Defaults to the production primary_strategy; falls back through "
             f"{_VOL_STRATEGY_FALLBACKS}.",
    )
    parser.add_argument("--adaptive-v22-backtest-dir", default=_resolved)
    parser.add_argument("--dual-3m-backtest-dir", default=_resolved)
    args = parser.parse_args()
    payload = run_review(args)
    print(json.dumps({"status": "SUCCESS", "params": payload["params"], "outputs": payload["outputs"], "notify_result": payload["notify_result"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
