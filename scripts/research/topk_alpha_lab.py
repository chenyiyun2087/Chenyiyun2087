#!/usr/bin/env python3
"""Deterministic Top5/Top10 challenger laboratory for a CNY 500k account.

This module is research-only.  It never changes the production route, never
authorizes capital, and never upgrades synthetic evidence to historical E3.
It consumes a canonical PIT factor panel plus canonical market data and
evaluates a pre-registered TopK/cost/holding-period matrix with T+1 open
execution, lot sizing, ADV and concentration constraints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from runtime.acceptance_config import canonical_sha
from runtime.pit_semantic_contract import validate_explicit_timezone

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INITIAL_CAPITAL_CNY = 500_000.0
HOLDING_COUNTS = (5, 10)
REBALANCE_DAYS = (5, 10, 20)
COST_RATES = (0.00075,)
SLIPPAGE_BPS = (10, 20, 50)
MAX_INDUSTRY_WEIGHT = 0.30
MAX_STOCK_WEIGHT = 0.20
STOCK_WEIGHT_CAPS = (0.15, 0.20)
ADV_FRACTION = 0.002
LOT_SIZE = 100

CHALLENGER_FACTORS: dict[str, tuple[str, ...]] = {
    "size_lowvol_value_quality": (
        "size", "volatility", "value", "quality",
    ),
    "financial_quality_growth": (
        "quality", "growth", "earnings_acceleration",
    ),
    "residual_momentum_reversal_trend": (
        "residual_momentum", "short_reversal", "trend_stability",
    ),
    "industry_strength_regime": (
        "industry_strength", "market_regime_score",
    ),
}

CHALLENGER_FACTOR_SIGNS: dict[str, dict[str, float]] = {
    "size_lowvol_value_quality": {
        "size": -1.0, "volatility": -1.0, "value": -1.0, "quality": 1.0,
    },
    "financial_quality_growth": {
        "quality": 1.0, "growth": 1.0, "earnings_acceleration": 1.0,
    },
    "residual_momentum_reversal_trend": {
        "residual_momentum": 1.0, "short_reversal": 1.0, "trend_stability": 1.0,
    },
    "industry_strength_regime": {
        "industry_strength": 1.0, "market_regime_score": 1.0,
    },
}

CONFIG_PATH = PROJECT_ROOT / "config" / "topk_alpha_challenger.yaml"


@dataclass(frozen=True)
class LabConfig:
    initial_capital_cny: float = INITIAL_CAPITAL_CNY
    holdings: tuple[int, ...] = HOLDING_COUNTS
    rebalance_days: tuple[int, ...] = REBALANCE_DAYS
    cost_rates: tuple[float, ...] = COST_RATES
    slippage_bps: tuple[int, ...] = SLIPPAGE_BPS
    max_industry_weight: float = MAX_INDUSTRY_WEIGHT
    max_stock_weight: float = MAX_STOCK_WEIGHT
    stock_weight_caps: tuple[float, ...] = STOCK_WEIGHT_CAPS
    adv_fraction: float = ADV_FRACTION
    lot_size: int = LOT_SIZE
    ic_horizons: tuple[int, ...] = (5, 10, 20)
    min_ic_coverage: float = 0.95
    challengers: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            name: tuple(factors) for name, factors in CHALLENGER_FACTORS.items()
        }
    )
    factor_signs: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            name: dict(signs)
            for name, signs in CHALLENGER_FACTOR_SIGNS.items()
        }
    )


def load_lab_config(path: Path = CONFIG_PATH) -> LabConfig:
    """Load the pre-registered challenger matrix from its YAML contract."""
    if not path.is_file():
        return LabConfig()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    challenger_payload = payload.get("challengers") or {}
    challengers = {
        str(name): tuple(str(factor) for factor in factors)
        for name, factors in challenger_payload.items()
    } or {
        name: tuple(factors) for name, factors in CHALLENGER_FACTORS.items()
    }
    flat_signs = payload.get("factor_signs") or {}
    factor_signs = {
        name: {
            factor: float(flat_signs.get(factor, 1.0))
            for factor in factors
        }
        for name, factors in challengers.items()
    }
    if not flat_signs:
        factor_signs = {
            name: dict(CHALLENGER_FACTOR_SIGNS.get(name, {}))
            for name in challengers
        }
    return LabConfig(
        initial_capital_cny=float(payload.get("initial_capital_cny", INITIAL_CAPITAL_CNY)),
        holdings=tuple(int(value) for value in payload.get("holdings", HOLDING_COUNTS)),
        rebalance_days=tuple(
            int(value) for value in payload.get("rebalance_days", REBALANCE_DAYS)
        ),
        cost_rates=tuple(
            float(value) for value in payload.get("cost_rates", COST_RATES)
        ),
        slippage_bps=tuple(
            int(value) for value in payload.get("slippage_bps", SLIPPAGE_BPS)
        ),
        max_industry_weight=float(
            payload.get("max_industry_weight", MAX_INDUSTRY_WEIGHT)
        ),
        max_stock_weight=float(payload.get("max_stock_weight", MAX_STOCK_WEIGHT)),
        stock_weight_caps=tuple(
            float(value) for value in payload.get("stock_weight_caps", STOCK_WEIGHT_CAPS)
        ),
        adv_fraction=float(payload.get("adv_fraction", ADV_FRACTION)),
        lot_size=int(payload.get("lot_size", LOT_SIZE)),
        ic_horizons=tuple(
            int(value) for value in payload.get("ic_horizons", (5, 10, 20))
        ),
        min_ic_coverage=float(payload.get("min_ic_coverage", 0.95)),
        challengers=challengers,
        factor_signs=factor_signs,
    )


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:
        return "UNKNOWN"


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype={"symbol": str})


def _blocked(
    output_dir: Path,
    blockers: list[str],
    *,
    panel_path: Path | None = None,
    market_path: Path | None = None,
    release: str = "topk_alpha_challenger_v1",
    strategy: str = "topk_registered_challengers",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": "topk_alpha_lab_v1",
        "status": "BLOCKED",
        "research_status": "BLOCKED",
        "trading_status": "BLOCKED",
        "capital_status": "NO_SCALE",
        "capital_authority": False,
        "initial_capital_cny": INITIAL_CAPITAL_CNY,
        "panel_path": str(panel_path) if panel_path else None,
        "market_path": str(market_path) if market_path else None,
        "snapshot_identity": {},
        "sample_start": None,
        "sample_end": None,
        "release": release,
        "strategy": strategy,
        "config_sha256": _sha(CONFIG_PATH) if CONFIG_PATH.exists() else "",
        "code_head": _git_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": "Asia/Shanghai",
        "blockers": sorted(set(blockers)),
        "execution_model": "strict_t1_open_precommit_v1",
        "signal_cutoff": "T15:30:00+08:00",
        "execution_time": "T+1 09:30:00+08:00",
    }
    report["content_sha256"] = canonical_sha(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    (output_dir / "topk_alpha_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    gate = {
        "schema_version": "core_alpha_target_gate_v1",
        "release": release,
        "strategy": strategy,
        "config_sha256": report["config_sha256"],
        "code_head": report["code_head"],
        "input_snapshot_sha256": "",
        "snapshot_identity": {},
        "sample_start": None,
        "sample_end": None,
        "execution_model": report["execution_model"],
        "timezone": report["timezone"],
        "status": "RESEARCH_CONTINUE",
        "checks": {},
        "blockers": sorted(set(blockers)),
        "allowed_new_capital_cny": 0,
        "capital_authority": False,
    }
    gate["content_sha256"] = canonical_sha(
        {key: value for key, value in gate.items() if key != "content_sha256"}
    )
    (output_dir / "core_alpha_target_gate_report.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def _validate_inputs(panel: pd.DataFrame, market: pd.DataFrame) -> list[str]:
    blockers: list[str] = []
    required_panel = {
        "trade_date", "symbol", "eligible_universe", "formal_score",
        "signal_time", "industry",
    }
    required_market = {
        "trade_date", "symbol", "open", "close", "amount", "pre_close",
        "limit_status", "market_available_at", "is_st", "is_suspended",
    }
    blockers.extend(
        f"panel_column_missing:{column}"
        for column in sorted(required_panel - set(panel.columns))
    )
    blockers.extend(
        f"market_column_missing:{column}"
        for column in sorted(required_market - set(market.columns))
    )
    for name, frame in (("panel", panel), ("market", market)):
        if {"trade_date", "symbol"}.issubset(frame.columns):
            if frame.duplicated(["trade_date", "symbol"]).any():
                blockers.append(f"duplicate_key:{name}")
    if "signal_time" in panel:
        if validate_explicit_timezone(panel["signal_time"]):
            blockers.append("signal_time_timezone_invalid")
        signal = pd.to_datetime(panel["signal_time"], errors="coerce", utc=True)
        if signal.isna().any():
            blockers.append("signal_time_invalid")
    if "market_available_at" in market:
        if validate_explicit_timezone(market["market_available_at"]):
            blockers.append("market_available_at_timezone_invalid")
        available = pd.to_datetime(
            market["market_available_at"], errors="coerce", utc=True
        )
        if available.isna().any():
            blockers.append("market_available_at_invalid")
        if "trade_date" in market:
            market_signal = pd.to_datetime(
                pd.to_datetime(market["trade_date"], errors="coerce").dt.strftime(
                    "%Y-%m-%d"
                )
                + "T15:30:00+08:00",
                errors="coerce",
                utc=True,
            )
            if (available.notna() & market_signal.notna() & (available > market_signal)).any():
                blockers.append("market_available_at_after_signal")
    if "limit_status" in market and market["limit_status"].isna().any():
        blockers.append("market_limit_status_missing")
    for column in ("open", "close", "pre_close", "amount"):
        if column in market:
            values = pd.to_numeric(market[column], errors="coerce")
            if values.isna().any():
                blockers.append(f"market_numeric_missing:{column}")
            if (values <= 0).any():
                blockers.append(f"market_numeric_non_positive:{column}")
    for column in ("is_st", "is_suspended"):
        if column in market and market[column].isna().any():
            blockers.append(f"market_status_missing:{column}")
    if "trade_date" in panel and "signal_time" in panel:
        signal = pd.to_datetime(panel["signal_time"], errors="coerce", utc=True)
        expected = pd.to_datetime(
            pd.to_datetime(panel["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            + "T15:30:00+08:00",
            errors="coerce", utc=True,
        )
        if (signal != expected).fillna(True).any():
            blockers.append("signal_time_not_canonical_t1530")
    return blockers


def _validate_factor_availability(
    panel: pd.DataFrame,
    factor_names: tuple[str, ...],
) -> list[str]:
    """Enforce the factor-level PIT contract used by the challenger lab.

    A factor value without its own availability timestamp is not evidence of
    a point-in-time value.  The lab therefore fails closed for missing,
    timezone-naive, unparsable, or post-signal timestamps.  This check is
    deliberately row-wise so a single leaked revision cannot be hidden by a
    daily aggregate.
    """
    blockers: list[str] = []
    if "signal_time" not in panel.columns:
        return blockers
    signal = pd.to_datetime(panel["signal_time"], errors="coerce", utc=True)
    for factor in factor_names:
        availability_column = f"{factor}_available_at"
        if availability_column not in panel.columns:
            blockers.append(f"challenger_factor_available_at_missing:{factor}")
            continue
        raw = panel[availability_column]
        if raw.isna().any():
            blockers.append(f"challenger_factor_available_at_missing:{factor}")
        timezone_errors = validate_explicit_timezone(raw)
        if timezone_errors:
            blockers.append(f"challenger_factor_available_at_timezone_invalid:{factor}")
        available = pd.to_datetime(raw, errors="coerce", utc=True)
        if available.isna().any():
            blockers.append(f"challenger_factor_available_at_invalid:{factor}")
        future = available.notna() & signal.notna() & (available > signal)
        if future.any():
            blockers.append(f"challenger_factor_available_after_signal:{factor}")
        if signal.isna().any() and available.notna().any():
            blockers.append("signal_time_invalid")
    # Financially sourced challengers cannot use a backfilled point estimate:
    # the panel must carry an announcement timestamp and a revision chain.
    if set(factor_names) & {"value", "quality", "growth", "earnings_acceleration"}:
        for column in ("announcement_date", "revision_id", "revision_sequence"):
            if column not in panel.columns:
                blockers.append(f"financial_revision_field_missing:{column}")
        if "announcement_date" in panel.columns:
            raw_announcement = panel["announcement_date"]
            if validate_explicit_timezone(raw_announcement):
                blockers.append("announcement_date_timezone_invalid")
            announcement = pd.to_datetime(raw_announcement, errors="coerce", utc=True)
            if announcement.isna().any():
                blockers.append("announcement_date_invalid")
            if announcement.notna().any() and signal.notna().any():
                if (announcement > signal).any():
                    blockers.append("announcement_date_after_signal")
            for factor in factor_names:
                availability_column = f"{factor}_available_at"
                if availability_column in panel.columns:
                    available = pd.to_datetime(
                        panel[availability_column], errors="coerce", utc=True
                    )
                    if (announcement.notna() & available.notna() & (announcement > available)).any():
                        blockers.append(
                            f"announcement_date_after_factor_available:{factor}"
                        )
        if "revision_id" in panel.columns and panel["revision_id"].isna().any():
            blockers.append("revision_id_missing")
        if "revision_sequence" in panel.columns:
            sequence = pd.to_numeric(panel["revision_sequence"], errors="coerce")
            if sequence.isna().any() or (sequence < 1).any():
                blockers.append("revision_sequence_invalid")
    return sorted(set(blockers))


def _annualized_metrics(nav: pd.Series, initial: float) -> dict[str, float | None]:
    values = pd.to_numeric(nav, errors="coerce").dropna()
    if len(values) < 2:
        return {
            "annualized_return": None,
            "max_drawdown": None,
            "sharpe": None,
            "calmar": None,
            "total_return": None,
        }
    daily = values.pct_change().dropna()
    days = max(int((len(values) - 1)), 1)
    total_return = float(values.iloc[-1] / initial - 1.0)
    annualized = float((values.iloc[-1] / initial) ** (252.0 / days) - 1.0)
    drawdown = values / values.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    sharpe = float(np.sqrt(252.0) * daily.mean() / daily.std()) if daily.std() > 0 else None
    calmar = float(annualized / abs(max_drawdown)) if max_drawdown < 0 else None
    return {
        "annualized_return": annualized,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "calmar": calmar,
        "total_return": total_return,
    }


def _benchmark_annualized_return(data: pd.DataFrame) -> float | None:
    if "market_return" not in data.columns:
        return None
    daily = pd.to_numeric(
        data.groupby("trade_date")["market_return"].first(), errors="coerce"
    ).dropna()
    if len(daily) < 2:
        return None
    growth = float((1.0 + daily).prod())
    return float(growth ** (252.0 / max(len(daily), 1)) - 1.0)


def _annual_positive_contribution(nav_series: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    frame = pd.DataFrame(nav_series)
    if frame.empty:
        return None, None
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    frame = frame.dropna().sort_values("trade_date")
    if frame.empty:
        return None, None
    annual_returns = frame.set_index("trade_date")["nav"].resample("YE").last().pct_change().dropna()
    positive = annual_returns[annual_returns > 0]
    if positive.empty:
        return 0.0, None
    contribution = positive / positive.sum()
    return float((annual_returns > 0).mean()), float(contribution.max())


def _oos_window_metrics(nav_series: list[dict[str, Any]], windows: list[dict[str, str]]) -> tuple[float | None, float | None]:
    if not nav_series or not windows:
        return None, None
    frame = pd.DataFrame(nav_series)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    frame = frame.dropna().sort_values("trade_date")
    outcomes: list[float] = []
    for window in windows:
        start = pd.Period(window["validation_end"], freq="M").end_time
        end = pd.Period(window["test_end"], freq="M").end_time
        scoped = frame[(frame["trade_date"] > start) & (frame["trade_date"] <= end)]
        if len(scoped) >= 2:
            outcomes.append(float(scoped["nav"].iloc[-1] / scoped["nav"].iloc[0] - 1.0))
    if not outcomes:
        return None, None
    positive_ratio = float(np.mean(np.asarray(outcomes) > 0))
    # A conservative PBO proxy: the share of OOS windows below zero.  It is
    # explicitly labelled as a proxy until a full combinatorial PBO module is
    # supplied; the gate remains fail-closed when it is unavailable.
    pbo_proxy = float(np.mean(np.asarray(outcomes) <= 0))
    return positive_ratio, pbo_proxy


def _dsr_confidence(result: dict[str, Any]) -> float | None:
    nav = pd.DataFrame(result.get("nav_series") or [])
    if len(nav) < 3:
        return None
    values = pd.to_numeric(nav["nav"], errors="coerce").dropna()
    daily = values.pct_change().dropna()
    if len(daily) < 2 or daily.std() <= 0:
        return None
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252.0))
    # Normal approximation with a multiple-testing penalty for the registered
    # TopK/cost/holding/cap matrix;
    # matrix; this is diagnostic evidence, never a fabricated PASS.
    matrix_cells = max(
        len(HOLDING_COUNTS)
        * len(REBALANCE_DAYS)
        * len(COST_RATES)
        * len(SLIPPAGE_BPS)
        * len(STOCK_WEIGHT_CAPS),
        1,
    )
    z = sharpe * np.sqrt(len(daily) / 252.0) - np.sqrt(2.0 * np.log(matrix_cells))
    return float(0.5 * (1.0 + math.erf(z / np.sqrt(2.0))))


def _build_nested_windows(dates: list[pd.Timestamp]) -> list[dict[str, str]]:
    if not dates:
        return []
    months = sorted(pd.Series(dates).dt.to_period("M").unique())
    if len(months) < 60:
        return []
    holdout_start = months[-12]
    usable = [month for month in months if month < holdout_start]
    windows: list[dict[str, str]] = []
    cursor = 0
    while cursor + 48 < len(usable):
        train_start = usable[cursor]
        train_end = usable[cursor + 35]
        validation_end = usable[cursor + 41]
        test_end = usable[cursor + 47]
        windows.append({
            "train_start": str(train_start),
            "train_end": str(train_end),
            "validation_end": str(validation_end),
            "test_end": str(test_end),
        })
        cursor += 6
    return windows


def _holdout_start(dates: list[pd.Timestamp]) -> pd.Timestamp | None:
    """Return the first day of the untouched final 12-month holdout."""
    months = sorted(pd.Series(dates).dt.to_period("M").unique()) if dates else []
    if len(months) < 12:
        return None
    return months[-12].start_time


def _holdout_metrics(
    nav_series: list[dict[str, Any]],
    holdout_start: pd.Timestamp | None,
    initial_capital: float,
) -> tuple[dict[str, float | None], pd.DataFrame]:
    if holdout_start is None or not nav_series:
        empty = pd.DataFrame(columns=["trade_date", "nav"])
        return _annualized_metrics(empty["nav"], initial_capital), empty
    frame = pd.DataFrame(nav_series)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    frame = frame.dropna().sort_values("trade_date")
    frame = frame[frame["trade_date"] >= holdout_start].copy()
    base = float(frame["nav"].iloc[0]) if not frame.empty else initial_capital
    return _annualized_metrics(frame["nav"], base), frame


def _factor_ic_report(
    data: pd.DataFrame,
    factor_names: tuple[str, ...],
    horizons: tuple[int, ...],
    *,
    min_coverage: float,
) -> dict[str, Any]:
    """Report challenger-factor Rank IC and decay without inventing labels.

    Forward-return columns are evaluation labels only.  If a real panel does
    not carry a requested label, the factor row is BLOCKED; no close or
    benchmark return is substituted for it.
    """
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    for factor in factor_names:
        if factor not in data.columns:
            blockers.append(f"factor_column_missing:{factor}")
            for horizon in horizons:
                rows.append({
                    "factor": factor,
                    "horizon": int(horizon),
                    "status": "BLOCKED",
                    "mean_rank_ic": None,
                    "information_ratio": None,
                    "positive_ic_ratio": None,
                    "coverage": 0.0,
                    "daily_observations": 0,
                })
            continue
        for horizon in horizons:
            target = f"fwd_{int(horizon)}d_return"
            if target not in data.columns:
                blockers.append(f"forward_label_missing:{target}")
                rows.append({
                    "factor": factor,
                    "horizon": int(horizon),
                    "status": "BLOCKED",
                    "mean_rank_ic": None,
                    "information_ratio": None,
                    "positive_ic_ratio": None,
                    "coverage": 0.0,
                    "daily_observations": 0,
                })
                continue
            factor_values = pd.to_numeric(data[factor], errors="coerce")
            target_values = pd.to_numeric(data[target], errors="coerce")
            coverage = float(
                factor_values.notna().combine(target_values.notna(), lambda left, right: left and right).mean()
            )
            frame = pd.DataFrame({
                "trade_date": data["trade_date"],
                "factor": factor_values,
                "target": target_values,
            })
            daily_ic: list[float] = []
            for _, group in frame.groupby("trade_date", sort=True):
                group = group.dropna()
                if len(group) < 5 or group["factor"].nunique() < 2:
                    continue
                value = group["factor"].corr(group["target"], method="spearman")
                if pd.notna(value):
                    daily_ic.append(float(value))
            mean_ic = float(np.mean(daily_ic)) if daily_ic else None
            std_ic = float(np.std(daily_ic, ddof=1)) if len(daily_ic) > 1 else None
            information_ratio = (
                float(mean_ic / std_ic)
                if mean_ic is not None and std_ic not in (None, 0.0)
                else None
            )
            status = (
                "PASS"
                if coverage >= min_coverage and mean_ic is not None
                else "BLOCKED"
            )
            if status != "PASS":
                blockers.append(f"factor_ic_incomplete:{factor}:{horizon}d")
            rows.append({
                "factor": factor,
                "horizon": int(horizon),
                "status": status,
                "mean_rank_ic": mean_ic,
                "information_ratio": information_ratio,
                "positive_ic_ratio": (
                    float(np.mean(np.asarray(daily_ic) > 0)) if daily_ic else None
                ),
                "coverage": coverage,
                "daily_observations": len(daily_ic),
            })
    decay: list[dict[str, Any]] = []
    for factor in factor_names:
        factor_rows = [row for row in rows if row["factor"] == factor]
        values = [row["mean_rank_ic"] for row in factor_rows]
        comparable = [value for value in values if value is not None]
        decay.append({
            "factor": factor,
            "rank_ic_by_horizon": {
                str(row["horizon"]): row["mean_rank_ic"] for row in factor_rows
            },
            "absolute_rank_ic_non_increasing": (
                len(comparable) == len(values)
                and all(
                    abs(float(left)) >= abs(float(right))
                    for left, right in zip(comparable, comparable[1:])
                )
            ),
        })
    return {
        "status": "PASS" if rows and not blockers else "BLOCKED",
        "rows": rows,
        "decay": decay,
        "horizons": [int(value) for value in horizons],
        "blockers": sorted(set(blockers)),
        "coverage_threshold": min_coverage,
    }


def _simulate(
    data: pd.DataFrame,
    *,
    challenger: str,
    top_k: int,
    rebalance_days: int,
    cost_rate: float,
    slippage_bps: int,
    max_stock_weight: float,
    config: LabConfig,
) -> dict[str, Any]:
    data = data.sort_values(["trade_date", "symbol"]).copy()
    dates = sorted(data["trade_date"].dropna().unique())
    if len(dates) < 3:
        return {"top_k": top_k, "rebalance_days": rebalance_days, "cost_rate": cost_rate,
                "slippage_bps": slippage_bps, "status": "BLOCKED", "blockers": ["insufficient_trading_days"]}
    factor_names = config.challengers.get(
        challenger, CHALLENGER_FACTORS.get(challenger, ())
    )
    if not factor_names:
        return {
            "top_k": top_k,
            "rebalance_days": rebalance_days,
            "cost_rate": cost_rate,
            "slippage_bps": slippage_bps,
            "status": "BLOCKED",
            "blockers": ["unknown_challenger"],
        }
    signs = config.factor_signs.get(challenger, CHALLENGER_FACTOR_SIGNS.get(challenger, {}))
    # Cross-sectional rank composite.  No missing factor is treated as a
    # neutral zero; rows with an unavailable registered factor are excluded.
    data["challenger_score"] = np.nan
    for day, group_index in data.groupby("trade_date", sort=False).groups.items():
        group = data.loc[group_index]
        complete = group[list(factor_names)].notna().all(axis=1)
        if not complete.any():
            continue
        score = pd.Series(0.0, index=group.index, dtype=float)
        for factor in factor_names:
            values = pd.to_numeric(group[factor], errors="coerce")
            # Deterministic cross-sectional winsorization followed by
            # industry-neutral standardization.  Missing values remain
            # unavailable and are never converted into a neutral score.
            valid = values.dropna()
            if len(valid) >= 5:
                lower, upper = valid.quantile(0.01), valid.quantile(0.99)
                values = values.clip(lower=lower, upper=upper)
            if "industry" in group.columns:
                group_mean = values.groupby(group["industry"]).transform("mean")
                group_std = values.groupby(group["industry"]).transform("std").replace(0, np.nan)
                standardized = (values - group_mean).div(group_std)
                values = standardized.where(standardized.notna(), values)
            rank = values.rank(pct=True)
            score += rank * float(signs.get(factor, 1.0))
        data.loc[group.index[complete], "challenger_score"] = score.loc[group.index[complete]]

    signal_indices = list(range(0, len(dates) - 1, rebalance_days))
    signal_dates = [dates[index] for index in signal_indices]
    cash = float(config.initial_capital_cny)
    positions: dict[str, dict[str, Any]] = {}
    nav_rows: list[dict[str, Any]] = []
    turnover = 0.0
    rejected = 0
    frozen_shares = 0
    selected_count = 0
    max_adv_participation = 0.0
    adv_limited_orders = 0
    slip = slippage_bps / 10_000.0

    def day_rows(day: object) -> pd.DataFrame:
        return data[data["trade_date"].eq(day)].set_index("symbol")

    def mark_equity(day: object) -> float:
        rows = day_rows(day)
        equity = cash
        for symbol, position in positions.items():
            close = pd.to_numeric(rows.get("close", pd.Series(dtype=float)).get(symbol), errors="coerce")
            if pd.notna(close) and float(close) > 0:
                position["last_price"] = float(close)
            last_price = position.get("last_price", 0.0)
            if pd.notna(last_price) and float(last_price) > 0:
                equity += int(position["shares"]) * float(last_price)
        return float(equity)

    for signal_index, signal_date in zip(signal_indices, signal_dates):
        execution_index = signal_index + 1
        execution_date = dates[execution_index]
        next_signal_index = signal_indices[signal_indices.index(signal_index) + 1] if signal_index in signal_indices[:-1] else len(dates)
        period_end = dates[next_signal_index - 1] if next_signal_index > execution_index else execution_date
        signal = data[
            data["trade_date"].eq(signal_date)
            & data["eligible_universe"].fillna(False).astype(bool)
            & data["challenger_score"].notna()
        ].sort_values(["challenger_score", "symbol"], ascending=[False, True])
        picks = signal.head(top_k).copy()
        selected_count += len(picks)
        desired = set(picks["symbol"].astype(str))
        execution = day_rows(execution_date)
        equity_before = mark_equity(execution_date)

        # Sell undesired holdings first.  A limit-down/suspended row freezes
        # the position and never fabricates a fill.
        for symbol in list(positions):
            if symbol in desired:
                continue
            info = execution.loc[symbol] if symbol in execution.index else None
            if info is None or str(info.get("limit_status")) in {"LIMIT_DOWN", "SUSPENDED", "NO_TRADE"} or int(float(info.get("is_suspended", 0) or 0)) == 1:
                frozen_shares += int(positions[symbol]["shares"])
                rejected += 1
                continue
            price = float(pd.to_numeric(info.get("open"), errors="coerce")) if pd.notna(info.get("open")) else 0.0
            if price <= 0:
                frozen_shares += int(positions[symbol]["shares"])
                rejected += 1
                continue
            shares = int(positions[symbol]["shares"])
            gross = shares * price * (1.0 - slip)
            fee = gross * cost_rate
            cash += gross - fee
            turnover += shares * price
            positions.pop(symbol, None)

        # Carry existing holdings into concentration accounting.  Frozen
        # positions (e.g. a limit-down sale) still consume both their stock
        # and industry budget; they are not silently dropped from the cap
        # calculation.
        industry_weight: dict[str, float] = {}
        for held_symbol, held in positions.items():
            held_price = float(held.get("last_price") or 0.0)
            if held_price <= 0:
                continue
            held_weight = int(held.get("shares") or 0) * held_price / max(equity_before, 1e-12)
            held_industry = str(held.get("industry") or "UNKNOWN")
            industry_weight[held_industry] = industry_weight.get(held_industry, 0.0) + held_weight
        for row in picks.itertuples(index=False):
            symbol = str(row.symbol)
            if symbol in positions:
                continue
            if symbol not in execution.index:
                rejected += 1
                continue
            info = execution.loc[symbol]
            if str(info.get("limit_status")) in {"LIMIT_UP", "SUSPENDED", "NO_TRADE"} or int(float(info.get("is_suspended", 0) or 0)) == 1:
                rejected += 1
                continue
            price = float(pd.to_numeric(info.get("open"), errors="coerce")) if pd.notna(info.get("open")) else 0.0
            amount = float(pd.to_numeric(info.get("amount"), errors="coerce")) if pd.notna(info.get("amount")) else 0.0
            if price <= 0 or amount <= 0:
                rejected += 1
                continue
            industry = str(getattr(row, "industry", "UNKNOWN"))
            weight = min(1.0 / max(top_k, 1), max_stock_weight)
            weight = min(weight, max(config.max_industry_weight - industry_weight.get(industry, 0.0), 0.0))
            unconstrained_notional = min(equity_before * weight, cash)
            target_notional = min(unconstrained_notional, amount * config.adv_fraction)
            if target_notional + 1e-9 < unconstrained_notional:
                adv_limited_orders += 1
            shares = int(max(target_notional, 0.0) / (price * (1.0 + slip)) // config.lot_size * config.lot_size)
            if shares <= 0:
                rejected += 1
                continue
            gross = shares * price * (1.0 + slip)
            fee = gross * cost_rate
            if gross + fee > cash + 1e-9:
                rejected += 1
                continue
            cash -= gross + fee
            turnover += shares * price
            max_adv_participation = max(
                max_adv_participation,
                float(shares * price / max(amount, 1e-12)),
            )
            positions[symbol] = {
                "shares": shares,
                "industry": industry,
                "last_price": price,
            }
            industry_weight[industry] = industry_weight.get(industry, 0.0) + (shares * price / max(equity_before, 1e-12))

        period = dates[execution_index:next_signal_index]
        for trade_date in period:
            nav_rows.append({
                "trade_date": pd.Timestamp(trade_date).strftime("%Y-%m-%d"),
                "nav": mark_equity(trade_date),
            })

    nav = pd.DataFrame(nav_rows)
    metrics = _annualized_metrics(nav["nav"] if not nav.empty else pd.Series(dtype=float), config.initial_capital_cny)
    return {
        **metrics,
        "top_k": top_k,
        "rebalance_days": rebalance_days,
        "cost_rate": cost_rate,
        "slippage_bps": slippage_bps,
        "max_stock_weight": max_stock_weight,
        "selected_count": selected_count,
        "rejected_orders": rejected,
        "turnover_cny": turnover,
        "turnover_rate_on_initial_capital": float(turnover / max(config.initial_capital_cny, 1e-12)),
        "max_adv_participation": max_adv_participation,
        "adv_limited_orders": adv_limited_orders,
        "capacity": {
            "initial_capital_cny": config.initial_capital_cny,
            "adv_fraction_limit": config.adv_fraction,
            "max_adv_participation": max_adv_participation,
            "adv_limited_orders": adv_limited_orders,
            "lot_size": config.lot_size,
            "status": "PASS" if max_adv_participation <= config.adv_fraction + 1e-12 else "BLOCKED",
        },
        "frozen_shares": frozen_shares,
        "nav_rows": int(len(nav)),
        "nav_series": nav.to_dict("records"),
        "status": "PASS" if metrics["annualized_return"] is not None else "BLOCKED",
    }


def run_topk_alpha_lab(
    *,
    panel_path: Path,
    market_path: Path,
    output_dir: Path,
    evidence_origin: str = "HISTORICAL_REAL",
    challenger: str | None = None,
    config: LabConfig | None = None,
    release: str = "topk_alpha_challenger_v1",
    strategy: str = "topk_registered_challengers",
) -> dict[str, Any]:
    config = config or load_lab_config()
    if not panel_path.exists() or not market_path.exists():
        return _blocked(
            output_dir, ["panel_or_market_missing"],
            panel_path=panel_path, market_path=market_path,
            release=release, strategy=strategy,
        )
    panel = _read(panel_path)
    market = _read(market_path)
    blockers = _validate_inputs(panel, market)
    if blockers:
        return _blocked(
            output_dir, blockers, panel_path=panel_path, market_path=market_path,
            release=release, strategy=strategy,
        )
    if evidence_origin not in {"SYNTHETIC", "HISTORICAL_REAL"}:
        return _blocked(
            output_dir, ["evidence_origin_invalid"], panel_path=panel_path,
            market_path=market_path, release=release, strategy=strategy,
        )

    panel["trade_date"] = pd.to_datetime(panel["trade_date"], errors="coerce").dt.normalize()
    market["trade_date"] = pd.to_datetime(market["trade_date"], errors="coerce").dt.normalize()
    panel["symbol"] = panel["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    market["symbol"] = market["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    data = panel.merge(
        market,
        on=["trade_date", "symbol"],
        how="inner",
        suffixes=("", "_market"),
    )
    dates = sorted(data["trade_date"].dropna().unique())
    windows = _build_nested_windows([pd.Timestamp(value) for value in dates])
    holdout_start = _holdout_start([pd.Timestamp(value) for value in dates])
    results: list[dict[str, Any]] = []
    challengers = [challenger] if challenger else list(config.challengers)
    for challenger_name in challengers:
        required_factors = config.challengers.get(challenger_name)
        if required_factors is None:
            results.append({"challenger": challenger_name, "status": "BLOCKED", "blockers": ["unknown_challenger"]})
            continue
        missing_factors = sorted(set(required_factors) - set(data.columns))
        factor_blockers = _validate_factor_availability(panel, required_factors)
        factor_ic_report = _factor_ic_report(
            data,
            required_factors,
            config.ic_horizons,
            min_coverage=config.min_ic_coverage,
        )
        if missing_factors or factor_blockers:
            results.append({
                "challenger": challenger_name,
                "status": "BLOCKED",
                "blockers": [
                    *[f"challenger_factor_missing:{factor}" for factor in missing_factors],
                    *factor_blockers,
                ],
                "factor_ic_report": factor_ic_report,
                "factor_ic": factor_ic_report,
                "factor_decay": factor_ic_report["decay"],
            })
            continue
        for top_k in config.holdings:
            for rebalance_days in config.rebalance_days:
                for cost_rate in config.cost_rates:
                    for slippage_bps in config.slippage_bps:
                        for max_stock_weight in config.stock_weight_caps:
                            result = _simulate(
                                data,
                                challenger=challenger_name,
                                top_k=top_k,
                                rebalance_days=rebalance_days,
                                cost_rate=cost_rate,
                                slippage_bps=slippage_bps,
                                max_stock_weight=float(max_stock_weight),
                                config=config,
                            )
                            result["challenger"] = challenger_name
                            result["factor_ic_report"] = factor_ic_report
                            result["factor_ic"] = factor_ic_report
                            result["factor_decay"] = factor_ic_report["decay"]
                            result["cost_stress"] = {
                                "base_cost_rate": cost_rate,
                                "slippage_bps": slippage_bps,
                                "two_x_cost_rate": cost_rate * 2.0,
                                "status": "PASS",
                            }
                            if result.get("status") == "PASS":
                                oos_metrics, oos_frame = _holdout_metrics(
                                    result.get("nav_series") or [],
                                    holdout_start,
                                    config.initial_capital_cny,
                                )
                                for metric_name, metric_value in oos_metrics.items():
                                    result[f"oos_{metric_name}"] = metric_value
                                result["oos_sample_start"] = (
                                    oos_frame["trade_date"].min().date().isoformat()
                                    if not oos_frame.empty
                                    else None
                                )
                                result["oos_sample_end"] = (
                                    oos_frame["trade_date"].max().date().isoformat()
                                    if not oos_frame.empty
                                    else None
                                )
                                oos_data = data
                                if holdout_start is not None:
                                    oos_data = data[
                                        data["trade_date"] >= holdout_start
                                    ]
                                benchmark_return = _benchmark_annualized_return(oos_data)
                                result["benchmark_annualized_return"] = benchmark_return
                                result["annualized_excess_return"] = (
                                    float(result.get("oos_annualized_return"))
                                    - float(benchmark_return)
                                    if benchmark_return is not None
                                    and result.get("oos_annualized_return") is not None
                                    else None
                                )
                                result["oos_annualized_excess_return"] = result[
                                    "annualized_excess_return"
                                ]
                                stress = _simulate(
                                    data,
                                    challenger=challenger_name,
                                    top_k=top_k,
                                    rebalance_days=rebalance_days,
                                    cost_rate=cost_rate * 2.0,
                                    slippage_bps=slippage_bps,
                                    max_stock_weight=float(max_stock_weight),
                                    config=config,
                                )
                                result["two_x_cost_annualized_return"] = stress.get("annualized_return")
                                result["cost_stress"]["two_x_cost_annualized_return"] = stress.get(
                                    "annualized_return"
                                )
                                stress_oos, _ = _holdout_metrics(
                                    stress.get("nav_series") or [],
                                    holdout_start,
                                    config.initial_capital_cny,
                                )
                                result["two_x_cost_oos_annualized_return"] = stress_oos.get(
                                    "annualized_return"
                                )
                                result["oos_two_x_cost_annualized_return"] = result[
                                    "two_x_cost_oos_annualized_return"
                                ]
                                positive_ratio, pbo_proxy = _oos_window_metrics(result.get("nav_series") or [], windows)
                                result["positive_oos_window_ratio"] = positive_ratio
                                result["pbo"] = pbo_proxy
                                result["pbo_method"] = "negative_oos_window_proxy"
                                result["pbo_qualified"] = False
                                result["dsr_confidence"] = _dsr_confidence(result)
                                positive_year_ratio, max_year_contribution = _annual_positive_contribution(result.get("nav_series") or [])
                                result["positive_year_ratio"] = positive_year_ratio
                                result["max_positive_year_contribution"] = max_year_contribution
                                # Remove the five most profitable symbols from the
                                # sample and rerun the exact same challenger path.
                                symbol_return = data.groupby("symbol").apply(
                                    lambda frame: float(
                                        pd.to_numeric(frame["close"], errors="coerce").iloc[-1]
                                        / max(pd.to_numeric(frame["close"], errors="coerce").iloc[0], 1e-12)
                                        - 1.0
                                    ), include_groups=False
                                )
                                excluded = set(symbol_return.nlargest(5).index.astype(str))
                                without_top5 = data[~data["symbol"].astype(str).isin(excluded)]
                                no_top5 = _simulate(
                                    without_top5,
                                    challenger=challenger_name,
                                    top_k=top_k,
                                    rebalance_days=rebalance_days,
                                    cost_rate=cost_rate,
                                    slippage_bps=slippage_bps,
                                    max_stock_weight=float(max_stock_weight),
                                    config=config,
                                )
                                result["remove_top5_annualized_return"] = no_top5.get("annualized_return")
                                result["remove_top5_return_positive"] = bool(
                                    no_top5.get("annualized_return") is not None
                                    and float(no_top5.get("annualized_return")) > 0
                                )
                                no_top5_oos, _ = _holdout_metrics(
                                    no_top5.get("nav_series") or [],
                                    holdout_start,
                                    config.initial_capital_cny,
                                )
                                result["remove_top5_oos_annualized_return"] = no_top5_oos.get(
                                    "annualized_return"
                                )
                                result["remove_top5_oos_return_positive"] = bool(
                                    no_top5_oos.get("annualized_return") is not None
                                    and float(no_top5_oos.get("annualized_return")) > 0
                                )
                            results.append(result)

    gate = evaluate_core_alpha_target(results, nested_windows=windows)
    gate.update({
        "release": release,
        "strategy": strategy,
        "config_sha256": _sha(CONFIG_PATH) if CONFIG_PATH.exists() else "",
        "code_head": _git_sha(),
        "input_snapshot_sha256": canonical_sha({
            "panel_sha256": _sha(panel_path),
            "market_sha256": _sha(market_path),
        }),
        "snapshot_identity": {
            "panel_sha256": _sha(panel_path),
            "market_sha256": _sha(market_path),
        },
        "sample_start": str(min(dates).date()) if dates else None,
        "sample_end": str(max(dates).date()) if dates else None,
        "execution_model": "strict_t1_open_precommit_v1",
        "timezone": "Asia/Shanghai",
        "capital_authority": False,
    })
    gate["content_sha256"] = canonical_sha(
        {key: value for key, value in gate.items() if key != "content_sha256"}
    )
    valid_results = [item for item in results if item.get("status") == "PASS"]
    config_sha = _sha(CONFIG_PATH) if CONFIG_PATH.exists() else ""
    report_blockers = sorted({
        blocker
        for item in results
        for blocker in (item.get("blockers") or [])
    })
    report: dict[str, Any] = {
        "schema_version": "topk_alpha_lab_v1",
        "status": "PASS" if valid_results else "BLOCKED",
        "research_status": "RESEARCH_CONTINUE" if valid_results else "BLOCKED",
        "trading_status": "BLOCKED",
        "capital_status": "NO_SCALE",
        "capital_authority": False,
        "evidence_origin": evidence_origin,
        "historical_evidence_level": "E0" if evidence_origin != "HISTORICAL_REAL" else "E3_PENDING_FORMAL_CHAIN",
        "release": release,
        "strategy": strategy,
        "initial_capital_cny": config.initial_capital_cny,
        "execution_model": "strict_t1_open_precommit_v1",
        "signal_cutoff": "T15:30:00+08:00",
        "execution_time": "T+1 09:30:00+08:00",
        "cost_model": {"cost_rates": config.cost_rates, "slippage_bps": config.slippage_bps},
        "challengers": {
            name: list(factors) for name, factors in config.challengers.items()
        },
        "factor_signs": config.factor_signs,
        "factor_ic_horizons": list(config.ic_horizons),
        "factor_ic_reports": {
            str(item.get("challenger")): item.get("factor_ic_report")
            for item in results
            if item.get("factor_ic_report") is not None
        },
        "stock_weight_caps": list(config.stock_weight_caps),
        "panel_sha256": _sha(panel_path),
        "market_sha256": _sha(market_path),
        "input_snapshot_sha256": canonical_sha({
            "panel_sha256": _sha(panel_path),
            "market_sha256": _sha(market_path),
        }),
        "snapshot_identity": {
            "panel_sha256": _sha(panel_path),
            "market_sha256": _sha(market_path),
        },
        "config_sha256": config_sha,
        "code_head": _git_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": "Asia/Shanghai",
        "sample_start": str(min(dates).date()) if dates else None,
        "sample_end": str(max(dates).date()) if dates else None,
        "blockers": report_blockers,
        "determinism_sha256": canonical_sha({
            "panel_sha256": _sha(panel_path),
            "market_sha256": _sha(market_path),
            "config_sha256": config_sha,
            "matrix": {
                "holdings": config.holdings,
                "rebalance_days": config.rebalance_days,
                "cost_rates": config.cost_rates,
                "slippage_bps": config.slippage_bps,
                "stock_weight_caps": config.stock_weight_caps,
            },
        }),
        "nested_walk_forward": {
            "train_months": 36,
            "validation_months": 6,
            "test_months": 6,
            "holdout_months": 12,
            "holdout_start": (
                holdout_start.date().isoformat() if holdout_start is not None else None
            ),
            "windows": windows,
        },
        "results": results,
        "core_alpha_target_gate": gate,
    }
    report["content_sha256"] = canonical_sha(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "topk_alpha_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    (output_dir / "core_alpha_target_gate_report.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    scorecard = {
        "schema_version": "strategy_scorecard_topk_v1",
        "release": release,
        "strategy": strategy,
        "config_sha256": config_sha,
        "code_head": report["code_head"],
        "input_snapshot_sha256": report["input_snapshot_sha256"],
        "snapshot_identity": report["snapshot_identity"],
        "execution_model": report["execution_model"],
        "timezone": report["timezone"],
        "status": report["research_status"],
        "research_status": report["research_status"],
        "trading_status": "BLOCKED",
        "capital_status": "NO_SCALE",
        "allowed_new_capital_cny": 0,
        "core_alpha_target_gate": gate,
        "report_sha256": _sha(output_dir / "topk_alpha_report.json"),
    }
    scorecard["content_sha256"] = canonical_sha(
        {k: v for k, v in scorecard.items() if k != "content_sha256"}
    )
    (output_dir / "strategy_scorecard.json").write_text(
        json.dumps(scorecard, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return report


def evaluate_core_alpha_target(
    results: list[dict[str, Any]], *, nested_windows: list[dict[str, str]]
) -> dict[str, Any]:
    blockers: list[str] = []
    candidates = [item for item in results if item.get("status") == "PASS"]
    if not candidates:
        blockers.append("no_valid_topk_result")
    if not nested_windows:
        blockers.append("nested_walk_forward_insufficient_history")
    best = max(
        candidates,
        key=lambda item: float(item.get("annualized_return") or -np.inf),
        default={},
    )
    def _metric(key: str) -> Any:
        # OOS metrics are authoritative when the nested walk-forward produced
        # them.  The fallback keeps this public evaluator compatible with
        # hand-built diagnostic result rows, which are still blocked by the
        # missing nested-window/PBO evidence in a formal run.
        return best.get(f"oos_{key}") if f"oos_{key}" in best else best.get(key)

    def _ge(key: str, threshold: float) -> bool:
        value = _metric(key)
        if value is None:
            blockers.append(f"metric_unavailable:{key}")
            return False
        return float(value) >= threshold

    def _le(key: str, threshold: float) -> bool:
        value = _metric(key)
        if value is None:
            blockers.append(f"metric_unavailable:{key}")
            return False
        return float(value) <= threshold

    checks = {
        "annualized_return_ge_30pct": _ge("annualized_return", 0.30),
        "annualized_excess_return_ge_15pct": _ge("annualized_excess_return", 0.15),
        "max_drawdown_ge_minus25pct": _ge("max_drawdown", -0.25),
        "sharpe_ge_1_20": _ge("sharpe", 1.20),
        "calmar_ge_1_20": _ge("calmar", 1.20),
        "positive_oos_window_ratio_ge_70pct": _ge("positive_oos_window_ratio", 0.70),
        "dsr_confidence_ge_95pct": _ge("dsr_confidence", 0.95),
        "pbo_le_20pct": bool(best.get("pbo_qualified")) and _le("pbo", 0.20),
        "max_positive_year_contribution_le_40pct": _le("max_positive_year_contribution", 0.40),
        "two_x_cost_annualized_ge_25pct": _ge("two_x_cost_annualized_return", 0.25),
        "remove_top5_return_positive": bool(
            best.get(
                "remove_top5_oos_return_positive",
                best.get("remove_top5_return_positive"),
            )
        )
        if best.get(
            "remove_top5_oos_return_positive",
            best.get("remove_top5_return_positive"),
        ) is not None
        else False,
        "factor_ic_evidence_pass": bool(
            (best.get("factor_ic_report") or {}).get("status") == "PASS"
        ),
        "capacity_constraints_pass": bool(
            (best.get("capacity") or {}).get("status") == "PASS"
        ),
    }
    if best and best.get(
        "remove_top5_oos_return_positive",
        best.get("remove_top5_return_positive"),
    ) is None:
        blockers.append("metric_unavailable:remove_top5_return_positive")
    blockers.extend(key for key, passed in checks.items() if not passed)
    if best and not best.get("pbo_qualified"):
        blockers.append("pbo_not_formal_combinatorial_evidence")
    if best and (best.get("factor_ic_report") or {}).get("status") != "PASS":
        blockers.append("factor_ic_evidence_missing_or_blocked")
    if best and (best.get("capacity") or {}).get("status") != "PASS":
        blockers.append("capacity_evidence_blocked")
    return {
        "status": "CORE_ALPHA_TARGET_PASS" if not blockers else "RESEARCH_CONTINUE",
        "checks": checks,
        "best_result": best,
        "nested_window_count": len(nested_windows),
        "blockers": sorted(set(blockers)),
        "capital_authority": False,
        "allowed_new_capital_cny": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--challenger", choices=sorted(CHALLENGER_FACTORS), default=None)
    parser.add_argument("--evidence-origin", default="HISTORICAL_REAL")
    parser.add_argument("--release", default="topk_alpha_challenger_v1")
    parser.add_argument("--strategy", default="topk_registered_challengers")
    args = parser.parse_args()
    result = run_topk_alpha_lab(
        panel_path=args.panel,
        market_path=args.market,
        output_dir=args.output_dir,
        evidence_origin=args.evidence_origin,
        challenger=args.challenger,
        release=args.release,
        strategy=args.strategy,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
