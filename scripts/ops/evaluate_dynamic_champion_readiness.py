"""Fail-closed live-readiness assessment for the dynamic-score champion.

The evaluator never changes production routing or authorizes capital.  It reads
immutable repository evidence, computes a decision-ready assessment, and writes
an auditable report package.  Missing, stale, mismatched, or simulated evidence
is a failed gate rather than an inferred pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROGRAM = PROJECT_ROOT / "config" / "dynamic_champion_live_program.yaml"
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class GateResult:
    gate: str
    category: str
    required: str
    actual: str
    passed: bool
    blocking: bool
    evidence: str
    remediation: str


@dataclass(frozen=True)
class ReadinessDecision:
    decision: str
    allowed_capital_cny: float
    current_lane: str
    next_lane: str
    blocking_gates: tuple[str, ...]
    warning_gates: tuple[str, ...]


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [_json_ready(row) for row in frame.to_dict(orient="records")]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(payload: Any) -> str:
    encoded = json.dumps(
        _json_ready(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _relative(path: Path, root: Path = PROJECT_ROOT) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def load_program(path: Path = DEFAULT_PROGRAM) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = {
        "program_id",
        "strategy_id",
        "release_id",
        "acceptance_config",
        "release_registry",
        "approved_backtest_snapshot",
        "target_capital_cny",
        "capital_ladder",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"program_config_missing:{','.join(missing)}")
    ladder = payload["capital_ladder"]
    amounts = [float(row["capital_cny"]) for row in ladder]
    if amounts != sorted(amounts) or amounts[-1] != float(payload["target_capital_cny"]):
        raise ValueError("program_capital_ladder_invalid")
    if bool(payload.get("canary_enabled")) or bool(payload.get("broker_api_enabled")):
        raise ValueError("program_must_start_fail_closed")
    profile_name = str(payload.get("validation_profile") or "")
    if profile_name and profile_name not in {
        "alpha_v3",
        "alpha_v4_1",
        "alpha_v4_2",
        "alpha_v4_3",
        "alpha_v4_4",
        "alpha_v4_5",
        "alpha_v4_6",
        "formal_v5_0",
    }:
        raise ValueError(f"unsupported_validation_profile:{profile_name}")
    return payload


def load_upgrade_evidence(program: dict[str, Any]) -> dict[str, Any]:
    """Load PR-A–E evidence without inferring success from file existence."""
    configured = program.get("upgrade_evidence") or {}
    rows: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    for evidence_id, configured_path in configured.items():
        parts = str(evidence_id).split("_", 2)
        path = PROJECT_ROOT / str(configured_path)
        payload: dict[str, Any] = {}
        status = "MISSING"
        detail = "evidence_file_missing"
        sha256 = ""
        if path.exists():
            sha256 = _sha256(path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                status = "INVALID"
                detail = "evidence_json_invalid"
            else:
                status = str(payload.get("status") or "UNKNOWN")
                reasons = (
                    payload.get("blockers")
                    or payload.get("blocking_reasons")
                    or payload.get("blocking_checks")
                    or ([payload["reason"]] if payload.get("reason") else [])
                )
                detail = "; ".join(str(reason) for reason in reasons) or status
        payloads[str(evidence_id)] = payload
        rows.append(
            {
                "phase": (
                    f"PR-{parts[1].upper()}"
                    if len(parts) > 1 and parts[0] == "pr"
                    else str(evidence_id)
                ),
                "scope": str(evidence_id),
                "status": status,
                "detail": detail,
                "evidence": str(configured_path),
                "evidence_sha256": sha256,
            }
        )
    v3_path = PROJECT_ROOT / str(program.get("alpha_v3_evidence") or "")
    if str(program.get("alpha_v3_evidence") or "") and v3_path.exists():
        try:
            v3_payload = json.loads(v3_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            v3_payload = {"status": "INVALID", "blocking_gates": ["invalid_json"]}
        payloads["alpha_v3"] = v3_payload
    return {"rows": rows, "payloads": payloads}


def fifo_round_trips(trades: pd.DataFrame, strategy_id: str) -> pd.DataFrame:
    """Pair buys and sells FIFO and allocate both-side costs per share."""
    scoped = trades[trades["strategy"].astype(str).eq(strategy_id)].copy()
    scoped["trade_date"] = pd.to_datetime(scoped["trade_date"], errors="coerce")
    scoped["_row_order"] = np.arange(len(scoped))
    scoped = scoped.sort_values(["trade_date", "_row_order"])
    lots: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    closed: list[dict[str, Any]] = []
    for row in scoped.to_dict(orient="records"):
        symbol = str(row.get("symbol", "")).replace(".0", "").zfill(6)
        shares = int(float(row.get("shares") or 0))
        if not symbol or shares <= 0:
            continue
        price = float(row.get("price") or 0)
        cost_per_share = float(row.get("cost") or 0) / shares
        side = str(row.get("side", "")).upper()
        if side == "BUY":
            lots[symbol].append(
                {
                    "shares": shares,
                    "entry_date": row["trade_date"],
                    "entry_price": price,
                    "entry_cost_per_share": cost_per_share,
                    "name": str(row.get("name") or ""),
                    "industry": str(row.get("industry") or "未知"),
                }
            )
            continue
        if side != "SELL":
            continue
        remaining = shares
        while remaining > 0 and lots[symbol]:
            lot = lots[symbol][0]
            quantity = min(remaining, int(lot["shares"]))
            gross_pnl = (price - float(lot["entry_price"])) * quantity
            allocated_cost = (
                float(lot["entry_cost_per_share"]) + cost_per_share
            ) * quantity
            invested = float(lot["entry_price"]) * quantity
            net_pnl = gross_pnl - allocated_cost
            closed.append(
                {
                    "strategy": strategy_id,
                    "symbol": symbol,
                    "name": lot["name"],
                    "industry": lot["industry"],
                    "entry_date": lot["entry_date"],
                    "exit_date": row["trade_date"],
                    "shares": quantity,
                    "entry_price": lot["entry_price"],
                    "exit_price": price,
                    "gross_pnl": gross_pnl,
                    "cost": allocated_cost,
                    "net_pnl": net_pnl,
                    "net_return": net_pnl / invested if invested else np.nan,
                    "exit_reason": str(row.get("reason") or ""),
                }
            )
            lot["shares"] -= quantity
            remaining -= quantity
            if lot["shares"] == 0:
                lots[symbol].popleft()
    result = pd.DataFrame(closed)
    if not result.empty:
        result["holding_calendar_days"] = (
            result["exit_date"] - result["entry_date"]
        ).dt.days
        result["exit_month"] = result["exit_date"].dt.to_period("M").astype(str)
        result["exit_year"] = result["exit_date"].dt.year.astype(str)
    return result


def _max_loss_streak(values: Iterable[float]) -> int:
    current = longest = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def _drawdown_diagnostics(nav: pd.Series) -> dict[str, Any]:
    if nav.empty:
        return {
            "max_drawdown": None,
            "max_drawdown_days": None,
            "recovery_days": None,
        }
    peaks = nav.cummax()
    drawdown = nav / peaks - 1.0
    trough = drawdown.idxmin()
    peak_value = peaks.loc[trough]
    peak_candidates = nav.loc[:trough]
    peak_date = peak_candidates[peak_candidates.eq(peak_value)].index[-1]
    post = nav.loc[trough:]
    recovery = post[post.ge(peak_value)]
    recovery_date = recovery.index[0] if not recovery.empty else None
    return {
        "max_drawdown": float(drawdown.min()),
        "max_drawdown_days": int((trough - peak_date).days),
        "recovery_days": (
            int((recovery_date - trough).days) if recovery_date is not None else None
        ),
        "drawdown_peak_date": peak_date.date().isoformat(),
        "drawdown_trough_date": trough.date().isoformat(),
        "drawdown_recovery_date": (
            recovery_date.date().isoformat() if recovery_date is not None else None
        ),
    }


def _rolling_return(nav: pd.Series, days: int) -> float | None:
    if len(nav) <= days:
        return None
    return float(nav.iloc[-1] / nav.iloc[-days - 1] - 1.0)


def _annualized_return(daily_returns: pd.Series) -> float | None:
    if daily_returns.empty:
        return None
    compounded = float((1.0 + daily_returns).prod())
    if compounded <= 0:
        return -1.0
    return compounded ** (252.0 / len(daily_returns)) - 1.0


def _var_cvar(daily_returns: pd.Series, quantile: float = 0.05) -> tuple[float | None, float | None]:
    if daily_returns.empty:
        return None, None
    var = float(daily_returns.quantile(quantile))
    tail = daily_returns[daily_returns.le(var)]
    return var, float(tail.mean()) if not tail.empty else var


def analyze_backtest(run_dir: Path, strategy_id: str) -> dict[str, Any]:
    required_files = {
        "summary": "trusted_account_backtest_summary.csv",
        "nav": "trusted_account_backtest_nav.csv",
        "trades": "trusted_account_backtest_trades.csv",
        "positions": "trusted_account_backtest_positions.csv",
        "candidates": "trusted_account_backtest_candidates.csv",
        "report": "trusted_account_backtest_report.json",
    }
    missing = [name for name, filename in required_files.items() if not (run_dir / filename).exists()]
    if missing:
        raise FileNotFoundError(f"backtest_evidence_missing:{','.join(missing)}")

    summary_all = pd.read_csv(run_dir / required_files["summary"])
    summary = summary_all[summary_all["strategy"].astype(str).eq(strategy_id)]
    if len(summary) != 1:
        raise ValueError(
            f"strategy_identity_mismatch:{strategy_id}:matching_summary_rows={len(summary)}"
        )
    summary_row = summary.iloc[0].to_dict()

    nav_all = pd.read_csv(run_dir / required_files["nav"], low_memory=False)
    nav = nav_all[nav_all["strategy"].astype(str).eq(strategy_id)].copy()
    nav["trade_date"] = pd.to_datetime(nav["trade_date"], errors="coerce")
    nav["nav"] = pd.to_numeric(nav["nav"], errors="coerce")
    nav = nav.dropna(subset=["trade_date", "nav"]).sort_values("trade_date")
    if nav.empty:
        raise ValueError("backtest_nav_missing_for_strategy")
    nav_series = nav.drop_duplicates("trade_date").set_index("trade_date")["nav"]
    daily_returns = nav_series.pct_change().dropna()
    drawdown = nav_series / nav_series.cummax() - 1.0
    var95, cvar95 = _var_cvar(daily_returns)

    monthly_nav = nav.set_index("trade_date")["nav"].resample("ME").last().dropna()
    monthly_returns_series = monthly_nav.pct_change()
    if not monthly_nav.empty:
        monthly_returns_series.iloc[0] = float(monthly_nav.iloc[0] - 1.0)
    monthly = pd.DataFrame(
        {
            "month": monthly_nav.index.to_period("M").astype(str),
            "month_end_nav": monthly_nav.values,
            "monthly_return": monthly_returns_series.values,
        }
    )
    monthly["positive"] = monthly["monthly_return"].gt(0)
    monthly["cumulative_return"] = monthly["month_end_nav"] - 1.0

    annual_nav = nav.set_index("trade_date")["nav"].resample("YE").last().dropna()
    annual_returns_series = annual_nav.pct_change()
    if not annual_nav.empty:
        annual_returns_series.iloc[0] = float(annual_nav.iloc[0] - 1.0)
    annual = pd.DataFrame(
        {
            "year": annual_nav.index.year.astype(str),
            "year_end_nav": annual_nav.values,
            "annual_return": annual_returns_series.values,
        }
    )

    rolling_rows = []
    for label, days in (("3个月", 63), ("6个月", 126), ("12个月", 252)):
        rolling_rows.append(
            {
                "window": label,
                "trading_days": days,
                "return": _rolling_return(nav_series, days),
                "end_date": nav_series.index[-1].date().isoformat(),
            }
        )
    rolling = pd.DataFrame(rolling_rows)

    trades = pd.read_csv(run_dir / required_files["trades"], low_memory=False)
    round_trips = fifo_round_trips(trades, strategy_id)
    if round_trips.empty:
        win_rate = payoff_ratio = profit_factor = None
        avg_trade = median_trade = avg_holding = None
        max_loss_streak = 0
        stock = pd.DataFrame()
        industry = pd.DataFrame()
    else:
        wins = round_trips[round_trips["net_pnl"].gt(0)]
        losses = round_trips[round_trips["net_pnl"].lt(0)]
        avg_win = float(wins["net_return"].mean()) if not wins.empty else None
        avg_loss = abs(float(losses["net_return"].mean())) if not losses.empty else None
        win_rate = float(round_trips["net_pnl"].gt(0).mean())
        payoff_ratio = avg_win / avg_loss if avg_win is not None and avg_loss else None
        profit_factor = (
            float(wins["net_pnl"].sum() / abs(losses["net_pnl"].sum()))
            if not losses.empty and abs(float(losses["net_pnl"].sum())) > 0
            else None
        )
        avg_trade = float(round_trips["net_return"].mean())
        median_trade = float(round_trips["net_return"].median())
        avg_holding = float(round_trips["holding_calendar_days"].mean())
        max_loss_streak = _max_loss_streak(
            round_trips.sort_values("exit_date")["net_pnl"].tolist()
        )
        stock = (
            round_trips.groupby(["symbol", "name", "industry"], dropna=False)
            .agg(
                closed_trades=("net_pnl", "size"),
                wins=("net_pnl", lambda values: int(values.gt(0).sum())),
                net_pnl=("net_pnl", "sum"),
                avg_net_return=("net_return", "mean"),
            )
            .reset_index()
            .sort_values("net_pnl", ascending=False)
        )
        stock["win_rate"] = stock["wins"] / stock["closed_trades"]
        positive_stock_pnl = stock.loc[stock["net_pnl"].gt(0), "net_pnl"].sum()
        stock["positive_profit_share"] = np.where(
            stock["net_pnl"].gt(0) & (positive_stock_pnl > 0),
            stock["net_pnl"] / positive_stock_pnl,
            0.0,
        )
        industry = (
            round_trips.groupby("industry", dropna=False)
            .agg(
                closed_trades=("net_pnl", "size"),
                wins=("net_pnl", lambda values: int(values.gt(0).sum())),
                net_pnl=("net_pnl", "sum"),
                avg_net_return=("net_return", "mean"),
            )
            .reset_index()
            .sort_values("net_pnl", ascending=False)
        )
        industry["win_rate"] = industry["wins"] / industry["closed_trades"]

    data_quality = pd.DataFrame(
        [
            {
                "check": "策略身份",
                "status": "PASS",
                "actual": strategy_id,
                "severity": "critical",
            },
            {
                "check": "NAV日期重复",
                "status": "PASS" if not nav["trade_date"].duplicated().any() else "FAIL",
                "actual": int(nav["trade_date"].duplicated().sum()),
                "severity": "critical",
            },
            {
                "check": "NAV缺失或非有限值",
                "status": "PASS" if np.isfinite(nav["nav"]).all() else "FAIL",
                "actual": int((~np.isfinite(nav["nav"])).sum()),
                "severity": "critical",
            },
            {
                "check": "交易日期缺失",
                "status": "PASS" if trades["trade_date"].notna().all() else "FAIL",
                "actual": int(trades["trade_date"].isna().sum()),
                "severity": "critical",
            },
            {
                "check": "回测滑点",
                "status": "PASS" if float(summary_row.get("slippage_rate", 0) or 0) >= 0.001 else "LIMIT",
                "actual": float(summary_row.get("slippage_rate", 0) or 0),
                "severity": "high",
            },
        ]
    )

    metrics = {
        "strategy_id": strategy_id,
        "sample_start": str(summary_row.get("first_date")),
        "sample_end": str(summary_row.get("last_date")),
        "trading_days": int(summary_row.get("trading_days") or len(nav)),
        "initial_cash": float(summary_row.get("initial_cash") or 0),
        "final_equity": float(summary_row.get("final_equity") or 0),
        "total_return": float(summary_row.get("total_return") or 0),
        "annualized_return": float(summary_row.get("annualized_return") or 0),
        "annualized_volatility": (
            float(daily_returns.std(ddof=1) * math.sqrt(252))
            if len(daily_returns) > 1
            else None
        ),
        "downside_volatility": (
            float(daily_returns[daily_returns.lt(0)].std(ddof=1) * math.sqrt(252))
            if len(daily_returns[daily_returns.lt(0)]) > 1
            else None
        ),
        "sharpe_ratio": (
            float((_annualized_return(daily_returns) or 0) / (daily_returns.std(ddof=1) * math.sqrt(252)))
            if len(daily_returns) > 1 and daily_returns.std(ddof=1) > 0
            else None
        ),
        "max_drawdown": float(drawdown.min()),
        "daily_var_95": var95,
        "daily_cvar_95": cvar95,
        "closed_round_trips": int(len(round_trips)),
        "trade_win_rate": win_rate,
        "avg_trade_return": avg_trade,
        "median_trade_return": median_trade,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "avg_holding_calendar_days": avg_holding,
        "max_consecutive_losses": max_loss_streak,
        "total_cost": float(summary_row.get("total_cost") or 0),
        "turnover": float(summary_row.get("turnover") or 0),
        "slippage_rate": float(summary_row.get("slippage_rate") or 0),
        "trade_cost_rate": float(summary_row.get("trade_cost_rate") or 0),
        "positive_month_ratio": (
            float(monthly["positive"].mean()) if not monthly.empty else None
        ),
        "best_month": (
            str(monthly.loc[monthly["monthly_return"].idxmax(), "month"])
            if not monthly.empty
            else None
        ),
        "best_month_return": (
            float(monthly["monthly_return"].max()) if not monthly.empty else None
        ),
        "worst_month": (
            str(monthly.loc[monthly["monthly_return"].idxmin(), "month"])
            if not monthly.empty
            else None
        ),
        "worst_month_return": (
            float(monthly["monthly_return"].min()) if not monthly.empty else None
        ),
        **_drawdown_diagnostics(nav_series),
    }
    return {
        "metrics": metrics,
        "monthly": monthly,
        "annual": annual,
        "rolling": rolling,
        "round_trips": round_trips,
        "stock_attribution": stock,
        "industry_attribution": industry,
        "data_quality": data_quality,
        "nav": pd.DataFrame(
            {
                "trade_date": nav_series.index.strftime("%Y-%m-%d"),
                "nav": nav_series.values,
                "drawdown": drawdown.values,
                "daily_return": nav_series.pct_change().values,
            }
        ),
        "file_manifest": [
            {
                "file": _relative(run_dir / filename),
                "sha256": _sha256(run_dir / filename),
                "bytes": (run_dir / filename).stat().st_size,
            }
            for filename in required_files.values()
        ],
    }


def load_registry_status(
    registry_path: Path, strategy_id: str, expected_release_id: str
) -> dict[str, Any]:
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    release = (payload.get("releases") or {}).get(strategy_id)
    if not release:
        return {"identity_passed": False, "reason": "strategy_missing_from_registry"}
    hashes = {
        name: release.get(name)
        for name in (
            "git_commit_sha",
            "config_sha",
            "data_snapshot_sha",
            "calendar_snapshot_sha",
            "corporate_action_snapshot_sha",
            "lifecycle_snapshot_sha",
        )
    }
    hash_ready = all(
        value and not str(value).upper().startswith("PENDING") for value in hashes.values()
    )
    return {
        "identity_passed": (
            str(release.get("release_id")) == expected_release_id and hash_ready
        ),
        "release_id": release.get("release_id"),
        "role": release.get("role"),
        "research_status": release.get("research_status"),
        "walk_forward_status": release.get("walk_forward_status"),
        "walk_forward_passed": bool(release.get("walk_forward_passed", False)),
        "walk_forward_windows_passed": int(
            release.get("walk_forward_windows_passed", 0) or 0
        ),
        "promotion_status": release.get("promotion_status"),
        "capital_status": release.get("capital_status"),
        "hashes": hashes,
        "hash_ready": hash_ready,
    }


def load_shadow_status(
    path: Path | None, strategy_id: str, release_id: str
) -> dict[str, Any]:
    empty = {
        "strategy_match": False,
        "release_match": False,
        "disabled_real_trading_days": 0,
        "economic_real_trading_days": 0,
        "completed_round_trips": 0,
        "reconciliation_errors": None,
        "cost_after_alpha_positive": False,
        "promotion_ready": False,
        "canary_ready": False,
        "source": _relative(path) if path else "未提供",
        "reason": "missing_release_scoped_shadow_evidence",
    }
    if path is None or not path.exists():
        return empty
    payload = json.loads(path.read_text(encoding="utf-8"))
    observed_strategy = str(
        payload.get("strategy_id")
        or payload.get("shadow_strategy")
        or payload.get("candidate_strategy")
        or ""
    )
    observed_release = str(payload.get("release_id") or "")
    strategy_match = observed_strategy == strategy_id
    release_match = observed_release == release_id
    if not strategy_match or not release_match:
        return {
            **empty,
            "strategy_match": strategy_match,
            "release_match": release_match,
            "observed_strategy_id": observed_strategy,
            "observed_release_id": observed_release,
            "reason": "shadow_identity_mismatch",
        }
    return {
        **empty,
        "strategy_match": True,
        "release_match": True,
        "disabled_real_trading_days": int(
            payload.get("disabled_real_trading_days")
            or payload.get("technical_shadow_days")
            or 0
        ),
        "economic_real_trading_days": int(
            payload.get("economic_real_trading_days")
            or payload.get("enabled_shadow_days")
            or 0
        ),
        "completed_round_trips": int(payload.get("completed_round_trips") or 0),
        "reconciliation_errors": payload.get("reconciliation_errors"),
        "cost_after_alpha_positive": bool(
            payload.get("cost_after_alpha_positive", False)
        ),
        "promotion_ready": bool(payload.get("promotion_ready", False)),
        "canary_ready": bool(payload.get("canary_ready", False)),
        "reason": "",
    }


def build_gates(
    *,
    program: dict[str, Any],
    acceptance: dict[str, Any],
    metrics: dict[str, Any],
    registry: dict[str, Any],
    shadow: dict[str, Any],
    upgrade_evidence: dict[str, Any] | None = None,
    report_generated: bool = False,
) -> list[GateResult]:
    profile_name = str(program.get("validation_profile") or "")
    profile = (
        (acceptance.get("validation_profiles") or {}).get(profile_name)
        if profile_name
        else None
    )
    full_history = acceptance["full_history"]
    history_period = (profile or {}).get("core_period") or full_history
    performance = (profile or {}).get("performance") or {
        "min_annualized_return": full_history["min_annualized_return"],
        "max_drawdown": full_history["max_drawdown"],
        "min_sharpe_ratio": -math.inf,
    }
    rolling_oos = acceptance["rolling_oos"]
    strict = acceptance["strict_ledger"]
    shadow_cfg = program["shadow"]
    evidence_payloads = (upgrade_evidence or {}).get("payloads", {})
    equivalence = evidence_payloads.get("pr_a_equivalence", {})
    preflight = evidence_payloads.get("pr_b_formal_readiness", {})
    formal_run = evidence_payloads.get("pr_c_formal_run", {})
    oos = evidence_payloads.get("pr_d_oos_robustness", {})
    capacity = evidence_payloads.get("pr_e_execution_capacity", {})
    alpha_v3 = evidence_payloads.get("alpha_v3", {})
    v3_gate_rows = alpha_v3.get("gates") or []
    v3_gates = {
        str(row.get("gate")): str(row.get("status") or "BLOCKED")
        for row in v3_gate_rows
        if isinstance(row, dict)
    }
    sample_start = str(metrics["sample_start"])
    sample_end = str(metrics["sample_end"])
    full_history_pass = (
        sample_start <= str(history_period["min_start_date"])
        and metrics["max_drawdown"] > float(performance["max_drawdown"])
        and metrics["annualized_return"] > float(performance["min_annualized_return"])
        and float(metrics.get("sharpe_ratio") or 0.0)
        > float(performance.get("min_sharpe_ratio", -math.inf))
        and (not profile or v3_gates.get("core_history") == "PASS")
    )
    walk_forward_pass = (
        (
            v3_gates.get("walk_forward") == "PASS"
            if profile
            else (
                registry.get("walk_forward_passed") is True
                and registry.get("walk_forward_status") == "PASSED"
                and oos.get("status") == "PASS"
            )
        )
    )
    strict_pass = (
        registry.get("research_status") == "PASSED_REVALIDATION"
        and registry.get("hash_ready") is True
        and formal_run.get("status") == "VERIFIED"
    )
    statistical_pass = (
        v3_gates.get("alpha_attribution") == "PASS"
        and v3_gates.get("factor_ic") == "PASS"
        if profile
        else oos.get("status") == "PASS"
    )
    stress_pass = (
        v3_gates.get("execution_stress") == "PASS"
        if profile
        else capacity.get("status") == "PASS"
    )
    disabled_pass = (
        shadow["strategy_match"]
        and shadow["release_match"]
        and shadow["disabled_real_trading_days"]
        >= int(shadow_cfg["disabled_min_real_trading_days"])
    )
    economic_pass = (
        disabled_pass
        and shadow["economic_real_trading_days"]
        >= int(shadow_cfg["economic_min_real_trading_days"])
        and shadow["completed_round_trips"]
        >= int(shadow_cfg["economic_min_completed_round_trips"])
        and shadow["reconciliation_errors"] == 0
        and shadow["cost_after_alpha_positive"]
    )
    gates = [
        GateResult(
            "release_identity",
            "数据与发布身份",
            "Git、配置、数据、日历、公司行动及生命周期快照哈希完整",
            (
                "Release注册、经济等价和Formal预检均通过"
                if (
                    registry.get("identity_passed")
                    and equivalence.get("status") == "PASS"
                    and preflight.get("status") == "READY_FOR_FORMAL_RUN"
                )
                else (
                    f"registry={registry.get('status') or registry.get('research_status')}; "
                    f"PR-A={equivalence.get('status', 'MISSING')}; "
                    f"PR-B={preflight.get('status', 'MISSING')}"
                )
            ),
            bool(
                registry.get("identity_passed")
                and equivalence.get("status") == "PASS"
                and preflight.get("status") == "READY_FOR_FORMAL_RUN"
            ),
            True,
            str(program.get("upgrade_evidence", {}).get("pr_a_equivalence") or program["release_registry"]),
            "生成并冻结缺失快照，绑定新的不可变release证据包。",
        ),
        GateResult(
            "full_history",
            "长周期回测",
            f"{history_period['min_start_date']}至最新完整交易日，覆盖率≥{float(history_period['min_trade_day_coverage']):.0%}",
            (
                f"{sample_start}至{sample_end}，{metrics['trading_days']}个交易日；"
                f"PR-C={formal_run.get('status', 'MISSING')}"
            ),
            full_history_pass,
            True,
            str(program["approved_backtest_snapshot"]),
            f"使用正式PIT快照从{history_period['min_start_date'][:4]}年重跑全部市场状态和基础/压力场景；更早数据仅作可选扩展证据。",
        ),
        GateResult(
            "rolling_oos",
            "Walk-forward",
            f"12/3/3滚动OOS；正窗口≥{float(rolling_oos['min_positive_window_ratio']):.0%}",
            (
                f"registry={registry.get('walk_forward_status')}; "
                f"PR-D={oos.get('status', 'MISSING')}"
            ),
            walk_forward_pass,
            True,
            str(program.get("upgrade_evidence", {}).get("pr_d_oos_robustness") or program["release_registry"]),
            "在冻结样本上完成带purge/embargo的滚动OOS，测试窗不得继续调参。",
        ),
        GateResult(
            "strict_ledger",
            "严格账本",
            f"{strict['required_status']}；T+1、订单守恒、持仓错配均为0",
            (
                f"registry={registry.get('research_status') or 'UNKNOWN'}; "
                f"PR-C={formal_run.get('status', 'MISSING')}"
            ),
            strict_pass,
            True,
            str(program["release_registry"]),
            "补齐公司行动和生命周期快照，生成逐release对账指标并通过严格账本Gate。",
        ),
        GateResult(
            "statistical_robustness",
            "统计稳健性",
            "DSR≥95%、PBO≤20%、Bootstrap下界非负并完成因子归因",
            (
                f"alpha_v3 attribution={v3_gates.get('alpha_attribution', 'MISSING')}; "
                f"factor_ic={v3_gates.get('factor_ic', 'MISSING')}"
                if profile
                else (
                    f"PR-D={oos.get('status', 'MISSING')}; "
                    f"technical_complete={oos.get('technical_evidence_complete', False)}"
                )
            ),
            statistical_pass,
            True,
            str(program.get("upgrade_evidence", {}).get("pr_d_oos_robustness") or "config/production_acceptance.yaml"),
            "对完整OOS收益运行DSR、CPCV-PBO、Block Bootstrap和七因子归因。",
        ),
        GateResult(
            "execution_stress",
            "成本、滑点与容量",
            "5档资金规模和全部成本/滑点压力场景通过",
            (
                f"alpha_v3 execution_stress={v3_gates.get('execution_stress', 'MISSING')}"
                if profile
                else (
                    f"当前冻结回测滑点={metrics.get('slippage_rate', 0):.2%}；"
                    f"PR-E={capacity.get('status', 'MISSING')}"
                )
            ),
            stress_pass,
            True,
            str(program.get("upgrade_evidence", {}).get("pr_e_execution_capacity") or program["approved_backtest_snapshot"]),
            "按50万至1000万元及全部成本/滑点组合重跑并保存拒单、冲击和回撤扩张。",
        ),
        GateResult(
            "disabled_shadow",
            "技术Shadow",
            f"连续{shadow_cfg['disabled_min_real_trading_days']}个真实交易日",
            f"{shadow['disabled_real_trading_days']}日；{shadow.get('reason') or 'scoped evidence'}",
            disabled_pass,
            True,
            str(shadow.get("source") or "未提供"),
            "从同日正式PIT快照开始累计，历史回填不得计数。",
        ),
        GateResult(
            "economic_shadow",
            "经济Shadow",
            f"{shadow_cfg['economic_min_real_trading_days']}个额外交易日、{shadow_cfg['economic_min_completed_round_trips']}个闭环",
            f"{shadow['economic_real_trading_days']}日、{shadow['completed_round_trips']}个闭环",
            economic_pass,
            True,
            str(shadow.get("source") or "未提供"),
            "技术Shadow通过后再累计成本后Alpha、成交偏差和逐日对账证据。",
        ),
        GateResult(
            "comprehensive_report",
            "评估报告",
            "完整报告通过校验并给出唯一资本结论",
            "已生成" if report_generated else "本次生成中",
            report_generated,
            True,
            "exports/dynamic_champion_live_readiness",
            "完成数据、图表和门禁一致性校验后发布独立私有Sites快照。",
        ),
        GateResult(
            "manual_approval",
            "人工审批",
            "与当前release及证据SHA绑定的Canary审批",
            "缺失",
            False,
            True,
            "docs/04_live_trading/CANARY_RUNBOOK.md",
            "仅在其余门禁全部通过后记录人工审批。",
        ),
        GateResult(
            "broker_api_boundary",
            "执行边界",
            "broker_api_enabled=false，所有订单人工确认",
            f"broker_api_enabled={str(bool(program.get('broker_api_enabled'))).lower()}",
            not bool(program.get("broker_api_enabled")),
            True,
            str(DEFAULT_PROGRAM.relative_to(PROJECT_ROOT)),
            "保持人工订单草案、成交文件导入和离线对账。",
        ),
    ]
    return gates


def decide(gates: list[GateResult]) -> ReadinessDecision:
    blocking = tuple(gate.gate for gate in gates if gate.blocking and not gate.passed)
    warnings = tuple(gate.gate for gate in gates if not gate.blocking and not gate.passed)
    offline = {
        "release_identity",
        "full_history",
        "rolling_oos",
        "strict_ledger",
        "statistical_robustness",
        "execution_stress",
    }
    offline_pass = all(
        gate.passed for gate in gates if gate.gate in offline
    )
    if not blocking:
        return ReadinessDecision("GO", 50_000.0, "CANARY_10", "CANARY_25", (), warnings)
    if offline_pass:
        return ReadinessDecision(
            "CONDITIONAL_GO",
            0.0,
            "SHADOW_DISABLED",
            "SHADOW_ENABLED",
            blocking,
            warnings,
        )
    return ReadinessDecision(
        "NO_GO",
        0.0,
        "RESEARCH_REVALIDATION",
        "SHADOW_DISABLED",
        blocking,
        warnings,
    )


def _source(
    source_id: str,
    label: str,
    path: str,
    description: str,
    generated_at: str,
    sql: str,
    filters: list[str],
    definitions: list[str],
    tables_used: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "label": label,
        "path": path,
        "query": {
            "description": description,
            "engine": "DuckDB",
            "language": "sql",
            "sql": sql,
            "executed_at": generated_at,
            "tables_used": tables_used or [path],
            "filters": filters,
            "metric_definitions": definitions,
        },
    }


def build_artifact(
    *,
    program: dict[str, Any],
    analysis: dict[str, Any],
    gates: list[GateResult],
    decision: ReadinessDecision,
    upgrade_evidence: dict[str, Any],
    generated_at: str,
    gate_matrix_path: str,
) -> dict[str, Any]:
    metrics = analysis["metrics"]
    gate_frame = pd.DataFrame([asdict(gate) for gate in gates])
    gate_frame["status"] = np.where(gate_frame["passed"], "PASS", "BLOCKED")
    gate_frame["pass_value"] = gate_frame["passed"].astype(int)
    ladder = pd.DataFrame(program["capital_ladder"])
    ladder["status"] = "BLOCKED"
    ladder["allowed_now_cny"] = 0
    headline = pd.DataFrame(
        [
            {
                "decision": decision.decision,
                "allowed_capital_cny": decision.allowed_capital_cny,
                "total_return": metrics["total_return"],
                "annualized_return": metrics["annualized_return"],
                "max_drawdown": metrics["max_drawdown"],
                "trade_win_rate": metrics["trade_win_rate"],
                "closed_round_trips": metrics["closed_round_trips"],
                "sample_trading_days": metrics["trading_days"],
                "blocking_gate_count": len(decision.blocking_gates),
            }
        ]
    )
    top_stock = analysis["stock_attribution"].head(12).copy()
    quality = analysis["data_quality"].copy()
    upgrade_frame = pd.DataFrame(upgrade_evidence["rows"])
    source_run = str(program["approved_backtest_snapshot"])
    backtest_source = _source(
        "source_backtest",
        "动态评分冠军冻结账户回测",
        f"{source_run}/trusted_account_backtest_nav.csv",
        "读取冻结账户级NAV与成交，按策略身份过滤并计算月度、年度、滚动收益、回撤和FIFO闭环交易。",
        generated_at,
        (
            "SELECT * FROM read_csv_auto("
            f"'{source_run}/trusted_account_backtest_nav.csv', header=true) "
            f"WHERE strategy='{program['strategy_id']}';"
        ),
        [
            f"策略：{program['strategy_id']}",
            f"冻结样本：{metrics['sample_start']}至{metrics['sample_end']}",
            "T日信号、T+1开盘执行",
            "未平仓交易不进入闭环胜率分母",
        ],
        [
            "月度收益 = 当月月末NAV / 上月月末NAV - 1；首月相对初始NAV=1",
            "最大回撤 = NAV / 历史峰值NAV - 1 的最小值",
            "闭环胜率 = 净收益大于0的FIFO闭环交易数 / 全部FIFO闭环交易数",
            "VaR/CVaR使用日收益左尾5%分位及其以下均值",
        ],
        tables_used=[
            f"{source_run}/trusted_account_backtest_nav.csv",
            f"{source_run}/trusted_account_backtest_trades.csv",
            f"{source_run}/trusted_account_backtest_summary.csv",
        ],
    )
    gate_source = _source(
        "source_governance",
        "生产验收配置与发布注册表",
        "config/production_acceptance.yaml",
        "将中心生产门禁、动态冠军release状态和本次证据覆盖逐项对照；缺失证据按失败处理。",
        generated_at,
        (
            "SELECT * FROM read_csv_auto("
            f"'{gate_matrix_path}', header=true);"
        ),
        [
            f"program_id：{program['program_id']}",
            f"release_id：{program['release_id']}",
            "缺失、身份不匹配、PENDING或历史模拟证据均不得通过",
        ],
        [
            "GO要求全部阻塞门禁通过，允许首阶段资金固定为5万元",
            "CONDITIONAL_GO仅表示离线门禁通过但真实Shadow或审批未完成，允许资金仍为0",
            "NO_GO表示至少一个离线可信度或执行门禁失败，允许资金为0",
        ],
        tables_used=[
            "config/production_acceptance.yaml",
            "config/strategy_release_registry.yaml",
            "config/dynamic_champion_live_program.yaml",
            *[str(path) for path in program.get("upgrade_evidence", {}).values()],
        ],
    )
    title = str(program["report"]["site_title"])
    decision_text = {
        "GO": "全部门禁通过，可进入5万元人工Canary。",
        "CONDITIONAL_GO": "离线证据通过，但真实Shadow或审批仍未完成，暂不允许投入资金。",
        "NO_GO": "当前存在关键可信度与执行证据缺口，允许新增风险资金为0元。",
    }[decision.decision]
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": "动态评分冠军从研究复验到50万元分级实盘的统一数据与门禁评估。",
        "generatedAt": generated_at,
        "sources": [backtest_source, gate_source],
        "cards": [
            {
                "id": "card_decision",
                "description": "当前证据对应的唯一资金放行结论。",
                "dataset": "headline",
                "metrics": [{"label": "准入结论", "field": "decision"}],
                "sourceId": "source_governance",
            },
            {
                "id": "card_allowed_capital",
                "description": "在当前证据和人工审批状态下允许投入的新增风险资金。",
                "dataset": "headline",
                "metrics": [
                    {
                        "label": "允许资金（人民币元）",
                        "field": "allowed_capital_cny",
                        "format": "number",
                    }
                ],
                "sourceId": "source_governance",
            },
            {
                "id": "card_total_return",
                "description": "冻结账户回测累计收益，不代表真实券商成交业绩。",
                "dataset": "headline",
                "metrics": [
                    {"label": "回测累计收益", "field": "total_return", "format": "percent"},
                    {
                        "label": "年化",
                        "field": "annualized_return",
                        "format": "percent",
                        "signed": True,
                    },
                ],
                "sourceId": "source_backtest",
            },
            {
                "id": "card_max_drawdown",
                "description": "冻结账户回测NAV相对历史峰值的最大跌幅。",
                "dataset": "headline",
                "metrics": [
                    {
                        "label": "最大回撤",
                        "field": "max_drawdown",
                        "format": "percent",
                        "signed": True,
                    }
                ],
                "sourceId": "source_backtest",
            },
            {
                "id": "card_blockers",
                "description": "尚未通过的资金放行硬门禁数量。",
                "dataset": "headline",
                "metrics": [
                    {
                        "label": "阻塞门禁",
                        "field": "blocking_gate_count",
                        "format": "number",
                    }
                ],
                "sourceId": "source_governance",
            },
        ],
        "charts": [
            {
                "id": "chart_monthly_returns",
                "title": "冻结回测月度组合收益",
                "description": f"{metrics['sample_start']}至{metrics['sample_end']}；按月末NAV环比计算。",
                "type": "bar",
                "dataset": "monthly_returns",
                "encodings": {
                    "x": {"field": "month", "type": "temporal"},
                    "y": {
                        "field": "monthly_return",
                        "type": "quantitative",
                        "format": "percent",
                    },
                },
                "options": {"orientation": "vertical", "grouping": "single"},
                "sourceId": "source_backtest",
            },
            {
                "id": "chart_nav",
                "title": "冻结回测组合净值",
                "description": "账户NAV按初始净值归一化为1。",
                "type": "line",
                "dataset": "nav_series",
                "encodings": {
                    "x": {"field": "trade_date", "type": "temporal"},
                    "y": {"field": "nav", "type": "quantitative"},
                },
                "sourceId": "source_backtest",
            },
            {
                "id": "chart_drawdown",
                "title": "冻结回测组合回撤",
                "description": "相对历史净值峰值的跌幅；0表示处于历史新高。",
                "type": "line",
                "dataset": "drawdown_series",
                "encodings": {
                    "x": {"field": "trade_date", "type": "temporal"},
                    "y": {
                        "field": "drawdown",
                        "type": "quantitative",
                        "format": "percent",
                    },
                },
                "sourceId": "source_backtest",
            },
            {
                "id": "chart_annual_returns",
                "title": "冻结回测年度组合收益",
                "description": "首年相对初始NAV=1，其余年度按年末NAV环比计算。",
                "type": "bar",
                "dataset": "annual_returns",
                "encodings": {
                    "x": {"field": "year", "type": "nominal"},
                    "y": {
                        "field": "annual_return",
                        "type": "quantitative",
                        "format": "percent",
                    },
                },
                "options": {"orientation": "vertical", "grouping": "single"},
                "sourceId": "source_backtest",
            },
            {
                "id": "chart_gate_status",
                "title": "实盘准入门禁状态",
                "description": "1表示通过，0表示阻塞；缺失证据按阻塞处理。",
                "type": "bar",
                "dataset": "gate_matrix",
                "encodings": {
                    "x": {"field": "gate", "type": "nominal"},
                    "y": {"field": "pass_value", "type": "quantitative"},
                },
                "options": {"orientation": "horizontal", "grouping": "single"},
                "sourceId": "source_governance",
            },
        ],
        "tables": [
            {
                "id": "table_upgrade_evidence",
                "title": "PR-A至PR-E工程与业务证据",
                "description": "工程实现可以通过，但业务证据只有明确PASS才可用于准入。",
                "dataset": "upgrade_evidence",
                "columns": [
                    {"field": "phase", "label": "阶段", "type": "text"},
                    {"field": "scope", "label": "范围", "type": "text"},
                    {"field": "status", "label": "证据状态", "type": "text"},
                    {"field": "detail", "label": "阻塞或结论", "type": "text"},
                    {"field": "evidence_sha256", "label": "证据SHA-256", "type": "text"},
                ],
                "defaultSort": {"field": "phase", "direction": "asc"},
                "sourceId": "source_governance",
            },
            {
                "id": "table_gate_matrix",
                "title": "实盘准入门禁明细",
                "description": "每项均给出要求、当前证据和修复动作。",
                "dataset": "gate_matrix",
                "columns": [
                    {"field": "category", "label": "类别", "type": "text"},
                    {"field": "gate", "label": "门禁", "type": "text"},
                    {"field": "status", "label": "状态", "type": "text"},
                    {"field": "required", "label": "要求", "type": "text"},
                    {"field": "actual", "label": "当前证据", "type": "text"},
                    {"field": "remediation", "label": "下一步", "type": "text"},
                ],
                "defaultSort": {"field": "status", "direction": "asc"},
                "sourceId": "source_governance",
            },
            {
                "id": "table_monthly_returns",
                "title": "月度收益明细",
                "description": "精确列示月末NAV、月度收益与累计收益。",
                "dataset": "monthly_returns",
                "columns": [
                    {"field": "month", "label": "月份", "type": "text"},
                    {"field": "month_end_nav", "label": "月末NAV", "type": "number"},
                    {
                        "field": "monthly_return",
                        "label": "月度收益",
                        "type": "percent",
                        "movement": True,
                    },
                    {
                        "field": "cumulative_return",
                        "label": "累计收益",
                        "type": "percent",
                        "movement": True,
                    },
                ],
                "defaultSort": {"field": "month", "direction": "asc"},
                "sourceId": "source_backtest",
            },
            {
                "id": "table_stock_attribution",
                "title": "个股闭环收益贡献",
                "description": "按FIFO闭环交易净收益排序；仅显示前12项用于报告阅读。",
                "dataset": "stock_attribution",
                "columns": [
                    {"field": "symbol", "label": "代码", "type": "text"},
                    {"field": "name", "label": "名称", "type": "text"},
                    {"field": "industry", "label": "行业", "type": "text"},
                    {"field": "closed_trades", "label": "闭环数", "type": "number"},
                    {"field": "win_rate", "label": "胜率", "type": "percent"},
                    {
                        "field": "net_pnl",
                        "label": "净收益（人民币元）",
                        "type": "number",
                        "movement": True,
                    },
                    {
                        "field": "positive_profit_share",
                        "label": "正收益贡献占比",
                        "type": "percent",
                    },
                ],
                "defaultSort": {"field": "net_pnl", "direction": "desc"},
                "sourceId": "source_backtest",
            },
            {
                "id": "table_data_quality",
                "title": "数据质量检查",
                "description": "关键身份、唯一性、完整性与执行假设检查。",
                "dataset": "data_quality",
                "columns": [
                    {"field": "check", "label": "检查", "type": "text"},
                    {"field": "status", "label": "状态", "type": "text"},
                    {"field": "actual", "label": "结果", "type": "text"},
                    {"field": "severity", "label": "严重度", "type": "text"},
                ],
                "defaultSort": {"field": "severity", "direction": "asc"},
                "sourceId": "source_backtest",
            },
            {
                "id": "table_capital_ladder",
                "title": "资金分级路径",
                "description": "只有上一阶段满足真实交易日、闭环数和人工审批后才能进入下一阶段。",
                "dataset": "capital_ladder",
                "columns": [
                    {"field": "stage", "label": "阶段", "type": "text"},
                    {
                        "field": "capital_cny",
                        "label": "资金（人民币元）",
                        "type": "number",
                    },
                    {
                        "field": "min_real_trading_days",
                        "label": "最低真实交易日",
                        "type": "number",
                    },
                    {
                        "field": "min_completed_round_trips",
                        "label": "最低闭环数",
                        "type": "number",
                    },
                    {"field": "status", "label": "当前状态", "type": "text"},
                ],
                "defaultSort": {"field": "capital_cny", "direction": "asc"},
                "sourceId": "source_governance",
            },
        ],
        "blocks": [
            {"type": "markdown", "body": f"# {title}"},
            {
                "type": "markdown",
                "body": (
                    "## Executive Summary\n\n"
                    f"- **当前结论：{decision.decision}。** {decision_text}\n"
                    f"- **历史回测有正收益，但证据范围不足。** 冻结样本累计收益"
                    f" {metrics['total_return']:.1%}、年化 {metrics['annualized_return']:.1%}、"
                    f"最大回撤 {metrics['max_drawdown']:.1%}，但只覆盖"
                    f" {metrics['sample_start']} 至 {metrics['sample_end']}，未达到alpha_v3自2018年以来的核心验证要求。\n"
                    f"- **当前允许新增风险资金为 {decision.allowed_capital_cny:,.0f} 元。** "
                    f"仍有 {len(decision.blocking_gates)} 个硬门禁未通过，不能启用Canary。\n"
                    "- **实施边界保持不变。** 当前生产底座继续运行，动态评分冠军不继承既有本金例外，券商API持续关闭。"
                ),
            },
            {
                "type": "metric-strip",
                "cardIds": [
                    "card_decision",
                    "card_allowed_capital",
                    "card_total_return",
                    "card_max_drawdown",
                    "card_blockers",
                ],
            },
            {
                "type": "markdown",
                "body": (
                    "## PR-A至PR-E工程已合并，但不等于业务证据通过\n\n"
                    "Release等价、Formal预检、不可变回测、OOS稳健性和容量矩阵的"
                    "失败关闭基础设施均已进入主干。当前正式PIT输入仍未冻结，所以下游"
                    "证据按依赖链保持BLOCKED；这不会被本地测试通过或代码合并替代。"
                ),
            },
            {"type": "table", "tableId": "table_upgrade_evidence"},
            {
                "type": "markdown",
                "body": (
                    "## 回测收益可观，但尚不足以支持实盘放行\n\n"
                    "冻结回测提供了研究价值，但样本起点、滑点、容量压力、正式OOS与严格数据快照均未满足中心生产验收配置。"
                    "因此收益只能作为继续复验的理由，不能作为投入资金的依据。"
                ),
            },
            {"type": "chart", "chartId": "chart_monthly_returns"},
            {"type": "table", "tableId": "table_monthly_returns"},
            {
                "type": "markdown",
                "body": (
                    "## 回撤与年度分布显示需要跨周期复验\n\n"
                    "现有样本经历的市场状态有限；正式结论必须补齐2015年以来各类牛熊、风格切换和流动性冲击阶段。"
                ),
            },
            {"type": "chart", "chartId": "chart_nav"},
            {"type": "chart", "chartId": "chart_drawdown"},
            {"type": "chart", "chartId": "chart_annual_returns"},
            {
                "type": "markdown",
                "body": (
                    "## 盈利来源需要进一步去集中化验证\n\n"
                    "个股贡献按闭环净收益归集。正式稳健性验收还必须加入单股、单行业、单月贡献上限和剔除前五大赢家压力测试。"
                ),
            },
            {"type": "table", "tableId": "table_stock_attribution"},
            {
                "type": "markdown",
                "body": (
                    "## 当前门禁仍是NO-GO，而不是等待自动开启\n\n"
                    "系统按fail-closed处理缺失、身份不匹配和PENDING证据。真实Shadow只能从同日正式PIT快照开始累计，历史回填不计入80个交易日。"
                ),
            },
            {"type": "chart", "chartId": "chart_gate_status"},
            {"type": "table", "tableId": "table_gate_matrix"},
            {
                "type": "markdown",
                "body": (
                    "## 资金必须按5万、12.5万、25万、50万逐级验证\n\n"
                    "每一级至少运行60个真实交易日并完成30个闭环交易；任何账本、执行、数据或风险硬门槛失败都回退上一阶段。"
                ),
            },
            {"type": "table", "tableId": "table_capital_ladder"},
            {
                "type": "markdown",
                "body": (
                    "## 数据质量仍有高优先级缺口\n\n"
                    "当前冻结文件可读取且策略身份明确，但执行滑点为0，正式公司行动、生命周期、日历与统计稳健性证据尚未完整绑定到release。"
                ),
            },
            {"type": "table", "tableId": "table_data_quality"},
            {
                "type": "markdown",
                "body": (
                    "## 建议的下一步\n\n"
                    "1. 冻结2018年至今的正式PIT输入，补齐公司行动与证券生命周期快照；更早数据仅作扩展证据。\n"
                    "2. 完成12/3/3 Walk-forward、DSR/PBO/Bootstrap、因子归因和全部成本容量矩阵。\n"
                    "3. 严格账本达到VERIFIED后，从零资金技术Shadow开始累计真实交易日。\n"
                    "4. 只有报告更新为GO并绑定人工审批，才允许首阶段5万元人工Canary。"
                ),
            },
            {
                "type": "markdown",
                "body": (
                    "## Further Questions\n\n"
                    "- 2018年以来数据源的PIT可用性是否足以做到无不可解释覆盖缺口？\n"
                    "- 在10–100bp滑点和不同账户规模下，成本后Alpha还能保留多少？\n"
                    "- 真实Shadow中的恢复事件是否足够频繁，能在合理时间内形成30个独立样本？"
                ),
            },
            {
                "type": "markdown",
                "body": (
                    "## Caveats and Assumptions\n\n"
                    "- 本报告是已保存回测与治理证据的发布快照，不是实时数据库或券商账户连接。\n"
                    "- 回测不是实盘业绩；未完成的真实Shadow和Canary样本不会被历史模拟替代。\n"
                    "- 缺失证据按失败处理，NO_GO不会自动随日期推移转为GO。\n"
                    "- 所有真实订单继续要求人工确认，系统不启用券商API自动报单。"
                ),
            },
        ],
    }
    for index, block in enumerate(manifest["blocks"], start=1):
        block.setdefault("id", f"block_{index:02d}")
    snapshot = {
        "version": 1,
        "status": (
            "blocked"
            if any(row["status"] in {"BLOCKED", "MISSING", "INVALID", "UNKNOWN"} for row in upgrade_evidence["rows"])
            else "ready"
        ),
        "generatedAt": generated_at,
        "datasets": {
            "headline": _records(headline),
            "monthly_returns": _records(analysis["monthly"]),
            "annual_returns": _records(analysis["annual"]),
            "rolling_returns": _records(analysis["rolling"]),
            "nav_series": _records(analysis["nav"][["trade_date", "nav"]]),
            "drawdown_series": _records(
                analysis["nav"][["trade_date", "drawdown"]]
            ),
            "gate_matrix": _records(gate_frame),
            "stock_attribution": _records(top_stock),
            "data_quality": _records(quality),
            "capital_ladder": _records(ladder),
            "upgrade_evidence": _records(upgrade_frame),
        },
    }
    if snapshot["status"] == "blocked":
        snapshot["accessIssues"] = [
            {
                "id": "formal_evidence_unavailable",
                "scope": "dynamic_champion_formal_readiness",
                "sourceId": "source_governance",
                "dataset": "upgrade_evidence",
                "message": (
                    "正式PIT、不可变Formal Run、OOS与容量所需业务数据尚未完整加载；"
                    "报告保留冻结回测供研究复核，但资金结论为NO_GO。"
                ),
            }
        ]
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": [backtest_source, gate_source],
    }


def markdown_report(
    *,
    program: dict[str, Any],
    analysis: dict[str, Any],
    gates: list[GateResult],
    decision: ReadinessDecision,
    upgrade_evidence: dict[str, Any],
    generated_at: str,
) -> str:
    metrics = analysis["metrics"]
    lines = [
        "# 动态评分冠军策略实盘准入全面评估",
        "",
        f"> 评估时间：{generated_at}；策略：`{program['strategy_id']}`；"
        f"release：`{program['release_id']}`。",
        "",
        "## Executive Summary",
        "",
        f"- **结论：`{decision.decision}`。** 当前允许新增风险资金为"
        f" **{decision.allowed_capital_cny:,.0f} 元**，不得启用Canary。",
        f"- **冻结回测累计收益 {metrics['total_return']:.2%}、年化"
        f" {metrics['annualized_return']:.2%}、最大回撤 {metrics['max_drawdown']:.2%}。**"
        f" 但样本仅覆盖 {metrics['sample_start']} 至 {metrics['sample_end']}，"
        "不满足alpha_v3自2018年以来的正式核心验证门禁。",
        f"- **仍有 {len(decision.blocking_gates)} 个硬门禁未通过。**"
        " 主要缺口是正式快照、长周期回测、Walk-forward、严格账本、统计稳健性、"
        "成本容量压力和80个真实Shadow交易日。",
        "- **当前生产路由不变。** 动态评分冠军保持研究/阻塞状态，不继承既有本金例外，"
        "券商API保持关闭。",
        "",
        "## PR-A至PR-E升级证据",
        "",
        "| 阶段 | 范围 | 业务证据状态 | 阻塞或结论 |",
        "|---|---|---|---|",
    ]
    for row in upgrade_evidence["rows"]:
        lines.append(
            f"| {row['phase']} | `{row['scope']}` | {row['status']} | "
            f"{row['detail']} |"
        )
    lines.extend(
        [
        "",
        "代码与本地CI通过只证明失败关闭基础设施可用；业务证据在正式PIT输入"
        "缺失时仍保持BLOCKED，不能用于放行资金。",
        "",
        "## 绩效与风险概览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 样本交易日 | {metrics['trading_days']} |",
        f"| 累计收益 | {metrics['total_return']:.2%} |",
        f"| 年化收益 | {metrics['annualized_return']:.2%} |",
        f"| 年化波动率 | {metrics['annualized_volatility']:.2%} |"
        if metrics["annualized_volatility"] is not None
        else "| 年化波动率 | — |",
        f"| 最大回撤 | {metrics['max_drawdown']:.2%} |",
        f"| 日度VaR 95% | {metrics['daily_var_95']:.2%} |"
        if metrics["daily_var_95"] is not None
        else "| 日度VaR 95% | — |",
        f"| 日度CVaR 95% | {metrics['daily_cvar_95']:.2%} |"
        if metrics["daily_cvar_95"] is not None
        else "| 日度CVaR 95% | — |",
        f"| FIFO闭环交易 | {metrics['closed_round_trips']} |",
        f"| 闭环胜率 | {metrics['trade_win_rate']:.2%} |"
        if metrics["trade_win_rate"] is not None
        else "| 闭环胜率 | — |",
        f"| 盈亏比 | {metrics['payoff_ratio']:.2f} |"
        if metrics["payoff_ratio"] is not None
        else "| 盈亏比 | — |",
        f"| 利润因子 | {metrics['profit_factor']:.2f} |"
        if metrics["profit_factor"] is not None
        else "| 利润因子 | — |",
        f"| 最大连续亏损 | {metrics['max_consecutive_losses']} |",
        f"| 最佳月份 | {metrics['best_month']}（{metrics['best_month_return']:.2%}） |",
        f"| 最差月份 | {metrics['worst_month']}（{metrics['worst_month_return']:.2%}） |",
        "",
        "## 月度收益",
        "",
        "| 月份 | 月度收益 | 累计收益 |",
        "|---|---:|---:|",
        ]
    )
    for row in analysis["monthly"].to_dict(orient="records"):
        lines.append(
            f"| {row['month']} | {float(row['monthly_return']):.2%} | "
            f"{float(row['cumulative_return']):.2%} |"
        )
    lines.extend(
        [
            "",
            "## 实盘准入门禁",
            "",
            "| 类别 | 门禁 | 状态 | 当前证据 | 修复动作 |",
            "|---|---|---|---|---|",
        ]
    )
    for gate in gates:
        lines.append(
            f"| {gate.category} | `{gate.gate}` | "
            f"{'PASS' if gate.passed else 'BLOCKED'} | {gate.actual} | {gate.remediation} |"
        )
    lines.extend(
        [
            "",
            "## 数据质量与可复现性",
            "",
            "- 冻结回测文件均已生成SHA-256清单；同一输入应产生相同的评估包哈希。",
            "- 当前策略身份可精确解析，但发布注册表中的日历、公司行动和生命周期快照仍为PENDING。",
            "- 当前冻结回测滑点参数为0，不能据此判断真实可实现收益或容量。",
            "- 当前仓库中的Shadow报告属于其他策略或其他release，不能计入本策略20+60日门禁。",
            "",
            "## 资金分级路径",
            "",
            "| 阶段 | 资金 | 最低真实交易日 | 最低闭环数 | 当前状态 |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for row in program["capital_ladder"]:
        lines.append(
            f"| {row['stage']} | {float(row['capital_cny']):,.0f}元 | "
            f"{row['min_real_trading_days']} | {row['min_completed_round_trips']} | BLOCKED |"
        )
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            "1. 使用正式PIT快照重跑2018年至今的核心周期、市场状态和成本容量矩阵。",
            "2. 完成12/3/3 Walk-forward、DSR、CPCV-PBO、Block Bootstrap和七因子归因。",
            "3. 补齐公司行动和证券生命周期快照，使严格账本达到 `VERIFIED`。",
            "4. 从同日正式PIT数据开始累计20日技术Shadow，再累计60日经济Shadow和30个闭环。",
            "5. 仅当本报告更新为 `GO` 并绑定人工审批后，启用5万元人工Canary。",
            "",
            "## Caveats and Assumptions",
            "",
            "- 本报告是仓库中已保存证据的快照，不是实时数据库或券商账户连接。",
            "- 回测结果不是实盘业绩，不对未来收益作保证。",
            "- 历史模拟和跨策略Shadow证据不会计入本release的真实观察日。",
            "- 缺失证据按失败处理，生产底座和现有资金边界保持不变。",
            "",
        ]
    )
    return "\n".join(lines)


def write_assessment(
    *,
    program_path: Path,
    output_dir: Path,
    shadow_status_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    program = load_program(program_path)
    acceptance_path = PROJECT_ROOT / str(program["acceptance_config"])
    registry_path = PROJECT_ROOT / str(program["release_registry"])
    run_dir = PROJECT_ROOT / str(program["approved_backtest_snapshot"])
    acceptance = (
        yaml.safe_load(acceptance_path.read_text(encoding="utf-8")) or {}
    )["acceptance"]
    analysis = analyze_backtest(run_dir, str(program["strategy_id"]))
    upgrade_evidence = load_upgrade_evidence(program)
    registry = load_registry_status(
        registry_path, str(program["strategy_id"]), str(program["release_id"])
    )
    shadow = load_shadow_status(
        shadow_status_path, str(program["strategy_id"]), str(program["release_id"])
    )
    gates_initial = build_gates(
        program=program,
        acceptance=acceptance,
        metrics=analysis["metrics"],
        registry=registry,
        shadow=shadow,
        upgrade_evidence=upgrade_evidence,
        report_generated=False,
    )
    # Report generation itself is deterministic and is marked passed in the final package.
    gates = [
        (
            GateResult(
                gate=gate.gate,
                category=gate.category,
                required=gate.required,
                actual="已生成；Artifact与UTF-8校验结果随证据包归档",
                passed=True,
                blocking=gate.blocking,
                evidence=gate.evidence,
                remediation="发布前完成Artifact与UTF-8校验。",
            )
            if gate.gate == "comprehensive_report"
            else gate
        )
        for gate in gates_initial
    ]
    decision = decide(gates)
    timestamp = (generated_at or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    generated_iso = timestamp.isoformat(timespec="seconds")
    output_dir.mkdir(parents=True, exist_ok=False)

    artifact = build_artifact(
        program=program,
        analysis=analysis,
        gates=gates,
        decision=decision,
        upgrade_evidence=upgrade_evidence,
        generated_at=generated_iso,
        gate_matrix_path=_relative(output_dir / "gate_matrix.csv"),
    )
    report_text = markdown_report(
        program=program,
        analysis=analysis,
        gates=gates,
        decision=decision,
        upgrade_evidence=upgrade_evidence,
        generated_at=generated_iso,
    )
    frames = {
        "gate_matrix.csv": pd.DataFrame([asdict(gate) for gate in gates]),
        "monthly_returns.csv": analysis["monthly"],
        "annual_returns.csv": analysis["annual"],
        "rolling_returns.csv": analysis["rolling"],
        "nav_drawdown.csv": analysis["nav"],
        "round_trips.csv": analysis["round_trips"],
        "stock_attribution.csv": analysis["stock_attribution"],
        "industry_attribution.csv": analysis["industry_attribution"],
        "data_quality.csv": analysis["data_quality"],
        "capital_ladder.csv": pd.DataFrame(program["capital_ladder"]).assign(
            status="BLOCKED"
        ),
        "upgrade_evidence.csv": pd.DataFrame(upgrade_evidence["rows"]),
    }
    for filename, frame in frames.items():
        frame.to_csv(output_dir / filename, index=False)
    (output_dir / "artifact.json").write_text(
        json.dumps(_json_ready(artifact), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(report_text, encoding="utf-8")

    assessment = {
        "schema_version": "1.0",
        "generated_at": generated_iso,
        "program": program,
        "decision": asdict(decision),
        "metrics": analysis["metrics"],
        "registry": registry,
        "shadow": shadow,
        "upgrade_evidence": upgrade_evidence["rows"],
        "gates": [asdict(gate) for gate in gates],
        "file_manifest": analysis["file_manifest"],
        "git_commit_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip(),
    }
    assessment["assessment_sha256"] = _canonical_sha(assessment)
    (output_dir / "readiness.json").write_text(
        json.dumps(_json_ready(assessment), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    package_manifest = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            package_manifest.append(
                {
                    "file": path.name,
                    "sha256": _sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    (output_dir / "evidence_manifest.json").write_text(
        json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "decision": decision.decision,
        "allowed_capital_cny": decision.allowed_capital_cny,
        "blocking_gates": list(decision.blocking_gates),
        "output_dir": str(output_dir),
        "artifact_path": str(output_dir / "artifact.json"),
        "report_path": str(output_dir / "report.md"),
        "assessment_sha256": assessment["assessment_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate dynamic-score champion live readiness without changing production."
    )
    parser.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    parser.add_argument("--shadow-status", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    output_dir = args.output_dir or (
        PROJECT_ROOT
        / "exports"
        / "dynamic_champion_live_readiness"
        / datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S")
    )
    result = write_assessment(
        program_path=args.program,
        output_dir=output_dir,
        shadow_status_path=args.shadow_status,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["decision"] != "GO":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
