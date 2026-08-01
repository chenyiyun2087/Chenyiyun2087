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
from runtime.artifact_seal import verify_seal
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
    min_rank_ic: float = 0.02
    min_information_ratio: float = 0.30
    min_positive_ic_ratio: float = 0.55
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
        min_rank_ic=float(payload.get("min_rank_ic", 0.02)),
        min_information_ratio=float(payload.get("min_information_ratio", 0.30)),
        min_positive_ic_ratio=float(payload.get("min_positive_ic_ratio", 0.55)),
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
        "execution_data_contract": {
            "limit_status_field": "open_limit_status",
            "limit_status_fallback": "synthetic_only",
            "adv_field": "adv_cny",
            "adv_definition": "prior_trading_day_rolling20_amount",
            "same_day_amount_used_for_decision": False,
            "same_day_close_used_for_decision": False,
        },
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


def _validate_formal_package_binding(
    *,
    package_dir: Path | None,
    formal_pit_run_id: str | None,
    package_id: str | None,
    seal_sha256: str | None,
) -> tuple[list[str], dict[str, Any]]:
    """Verify the immutable Formal Package before historical Challenger use."""
    blockers: list[str] = []
    identity: dict[str, Any] = {}
    if package_dir is None or not package_dir.is_dir():
        return ["formal_package_binding_missing"], identity
    package_manifest_path = package_dir / "package_manifest.json"
    source_manifest_path = package_dir / "source_manifest.json"
    if not package_manifest_path.is_file():
        blockers.append("formal_package_manifest_missing")
    else:
        try:
            package_manifest = json.loads(package_manifest_path.read_text(encoding="utf-8"))
            identity.update({
                "formal_pit_run_id": package_manifest.get("formal_pit_run_id"),
                "package_id": package_manifest.get("package_id"),
            })
            if formal_pit_run_id and package_manifest.get("formal_pit_run_id") != formal_pit_run_id:
                blockers.append("formal_pit_run_id_mismatch")
            if package_id and package_manifest.get("package_id") != package_id:
                blockers.append("package_id_mismatch")
        except Exception as exc:
            blockers.append(f"formal_package_manifest_unreadable:{type(exc).__name__}")
    seal_path = package_dir / "seal_manifest.json"
    if not seal_path.is_file():
        blockers.append("formal_package_seal_missing")
    else:
        seal_result = verify_seal(package_dir)
        if seal_result.get("status") != "VERIFIED":
            blockers.append(f"formal_package_seal_not_verified:{seal_result.get('status')}")
        seal_file_sha = _sha(seal_path)
        identity["package_seal_manifest_file_sha256"] = seal_file_sha
        if seal_sha256 and seal_file_sha != seal_sha256:
            blockers.append("formal_package_seal_sha_mismatch")
    if not source_manifest_path.is_file():
        blockers.append("formal_package_source_manifest_missing")
    else:
        try:
            source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
            sources = source_manifest.get("sources") or {}
            required = {
                "market", "universe", "financial", "industry", "adjustment",
                "trade_calendar", "security_lifecycle", "corporate_actions",
            }
            missing = sorted(required - set(sources))
            if missing:
                blockers.append(f"formal_package_source_families_missing:{','.join(missing)}")
            for family in sorted(required & set(sources)):
                if not str((sources[family] or {}).get("parameter_sha256") or ""):
                    blockers.append(f"formal_package_parameter_sha_missing:{family}")
            identity["source_manifest_sha256"] = _sha(source_manifest_path)
        except Exception as exc:
            blockers.append(f"formal_package_source_manifest_unreadable:{type(exc).__name__}")
    return sorted(set(blockers)), identity


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


def _annual_positive_contribution(
    nav_series: list[dict[str, Any]], initial: float = INITIAL_CAPITAL_CNY
) -> tuple[float | None, float | None]:
    frame = pd.DataFrame(nav_series)
    if frame.empty:
        return None, None
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    frame = frame.dropna().sort_values("trade_date")
    if frame.empty:
        return None, None
    year_end = frame.set_index("trade_date")["nav"].resample("YE").last().dropna()
    if year_end.empty:
        return None, None
    previous = year_end.shift(1)
    previous.iloc[0] = float(initial)
    annual_returns = (year_end / previous - 1.0).dropna()
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


def _dsr_confidence(result: dict[str, Any], n_trials: int | None = None) -> float | None:
    nav = pd.DataFrame(result.get("nav_series") or [])
    if len(nav) < 3:
        return None
    values = pd.to_numeric(nav["nav"], errors="coerce").dropna()
    daily = values.pct_change().dropna()
    if len(daily) < 2 or daily.std() <= 0:
        return None
    mean = float(daily.mean())
    std = float(daily.std(ddof=1))
    sharpe = float(mean / std * np.sqrt(252.0))
    matrix_cells = max(int(n_trials or result.get("n_trials") or 1), 1)
    centered = daily - mean
    skew = float(centered.pow(3).mean() / max(std ** 3, 1e-18))
    kurtosis = float(centered.pow(4).mean() / max(std ** 4, 1e-18)) - 3.0
    # Lo (2002)-style finite-sample Sharpe variance with skew/kurtosis, plus a
    # registered-trials deflation penalty.  This remains diagnostic until a
    # full experiment return matrix is supplied to the statistical module.
    variance = (
        1.0
        - skew * sharpe
        + ((kurtosis + 2.0) / 4.0) * sharpe * sharpe
    ) / max(len(daily) - 1, 1)
    se = float(np.sqrt(max(variance, 1e-18)))
    z = sharpe / se - np.sqrt(2.0 * np.log(matrix_cells))
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


def _fit_factor_weights(
    train: pd.DataFrame,
    factor_names: tuple[str, ...],
    registered_signs: dict[str, float],
) -> tuple[dict[str, float], list[str]]:
    """Fit deterministic factor weights using training data only.

    The fit is deliberately small and transparent: mean daily Rank IC against
    the pre-computed 5-day forward label, with the pre-registered direction
    retained as a sign guard.  Missing labels do not get fabricated; the
    caller must mark the nested window blocked when the fit cannot be made.
    """
    blockers: list[str] = []
    weights: dict[str, float] = {}
    target = "fwd_5d_return"
    if target not in train.columns:
        return {}, ["nested_training_label_missing:fwd_5d_return"]
    for factor in factor_names:
        if factor not in train.columns:
            blockers.append(f"nested_training_factor_missing:{factor}")
            continue
        values = pd.to_numeric(train[factor], errors="coerce")
        labels = pd.to_numeric(train[target], errors="coerce")
        frame = pd.DataFrame({"trade_date": train["trade_date"], "factor": values, "label": labels}).dropna()
        daily_ic: list[float] = []
        for _, group in frame.groupby("trade_date", sort=True):
            if len(group) < 5 or group["factor"].nunique() < 2:
                continue
            ic = group["factor"].corr(group["label"], method="spearman")
            if pd.notna(ic):
                daily_ic.append(float(ic))
        if not daily_ic:
            blockers.append(f"nested_training_ic_missing:{factor}")
            continue
        mean_ic = float(np.mean(daily_ic))
        registered = float(registered_signs.get(factor, 1.0))
        # Keep the pre-registered economic direction.  The magnitude is
        # learned from training only and normalised below.
        weights[factor] = registered * max(abs(mean_ic), 1e-6)
    if blockers:
        return {}, sorted(set(blockers))
    total = sum(abs(value) for value in weights.values())
    if total <= 0:
        return {}, ["nested_training_weights_zero"]
    return {key: value / total for key, value in weights.items()}, []


def _nested_walk_forward_report(
    data: pd.DataFrame,
    *,
    challenger: str,
    top_k: int,
    rebalance_days: int,
    cost_rate: float,
    slippage_bps: int,
    max_stock_weight: float,
    config: LabConfig,
    windows: list[dict[str, str]],
) -> dict[str, Any]:
    """Run actual train→validation selection→test windows.

    Merely emitting date windows is not evidence of nested walk-forward.  Each
    window below fits weights on the training slice, chooses between the
    fitted and pre-registered weights on validation, then evaluates the frozen
    choice on the untouched test slice.
    """
    factor_names = config.challengers.get(challenger, ())
    registered = config.factor_signs.get(challenger, {})
    window_reports: list[dict[str, Any]] = []
    blockers: list[str] = []
    for window in windows:
        train_start = pd.Period(window["train_start"], freq="M").start_time
        train_end = pd.Period(window["train_end"], freq="M").end_time
        validation_end = pd.Period(window["validation_end"], freq="M").end_time
        test_end = pd.Period(window["test_end"], freq="M").end_time
        train = data[(data["trade_date"] >= train_start) & (data["trade_date"] <= train_end)]
        validation = data[(data["trade_date"] > train_end) & (data["trade_date"] <= validation_end)]
        test = data[(data["trade_date"] > validation_end) & (data["trade_date"] <= test_end)]
        learned, fit_blockers = _fit_factor_weights(train, factor_names, registered)
        if fit_blockers:
            blockers.extend(fit_blockers)
            window_reports.append({
                **window,
                "status": "BLOCKED",
                "blockers": fit_blockers,
                "selected_on": "validation_only",
            })
            continue
        candidates = {
            "trained_rank_ic": learned,
            "registered_direction": {
                factor: float(registered.get(factor, 1.0))
                for factor in factor_names
            },
        }
        validation_scores: dict[str, float] = {}
        for name, weights in candidates.items():
            validation_result = _simulate(
                pd.concat([train, validation], ignore_index=True),
                challenger=challenger,
                top_k=top_k,
                rebalance_days=rebalance_days,
                cost_rate=cost_rate,
                slippage_bps=slippage_bps,
                max_stock_weight=max_stock_weight,
                config=config,
                factor_weights=weights,
            )
            validation_nav = pd.DataFrame(validation_result.get("nav_series") or [])
            validation_nav["trade_date"] = pd.to_datetime(
                validation_nav.get("trade_date"), errors="coerce"
            )
            validation_nav["nav"] = pd.to_numeric(
                validation_nav.get("nav"), errors="coerce"
            )
            validation_nav = validation_nav[
                validation_nav["trade_date"] >= validation["trade_date"].min()
            ].dropna(subset=["trade_date", "nav"])
            validation_scores[name] = (
                float(validation_nav["nav"].iloc[-1] / validation_nav["nav"].iloc[0] - 1.0)
                if len(validation_nav) >= 2
                else -np.inf
            )
        selected_name = max(validation_scores, key=validation_scores.get)
        selected_weights = candidates[selected_name]
        test_result = _simulate(
            test,
            challenger=challenger,
            top_k=top_k,
            rebalance_days=rebalance_days,
            cost_rate=cost_rate,
            slippage_bps=slippage_bps,
            max_stock_weight=max_stock_weight,
            config=config,
            factor_weights=selected_weights,
        )
        test_return = test_result.get("total_return")
        if test_return is None:
            blockers.append(f"nested_test_result_missing:{window['test_end']}")
        window_reports.append({
            **window,
            "status": "PASS" if test_return is not None else "BLOCKED",
            "selected_parameter": selected_name,
            "selected_factor_weights": selected_weights,
            "validation_scores": validation_scores,
            "test_total_return": test_return,
            "test_annualized_return": test_result.get("annualized_return"),
            "test_nav_rows": test_result.get("nav_rows", 0),
            "selected_on": "validation_only",
        })
    valid = [item for item in window_reports if item.get("status") == "PASS"]
    returns = [float(item["test_total_return"]) for item in valid if item.get("test_total_return") is not None]
    return {
        "status": "PASS" if valid and not blockers else "BLOCKED",
        "windows": window_reports,
        "valid_window_count": len(valid),
        "positive_test_window_ratio": float(np.mean(np.asarray(returns) > 0)) if returns else None,
        "pbo_proxy": float(np.mean(np.asarray(returns) <= 0)) if returns else None,
        "pbo_qualified": False,
        "blockers": sorted(set(blockers)),
        "selection_contract": "train_fit_validation_select_test_evaluate_v1",
    }


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
    factor_signs: dict[str, float] | None = None,
    min_rank_ic: float = 0.02,
    min_information_ratio: float = 0.30,
    min_positive_ic_ratio: float = 0.55,
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
            sign = float((factor_signs or {}).get(factor, 1.0))
            target_values = pd.to_numeric(data[target], errors="coerce")
            frame = pd.DataFrame({
                "trade_date": data["trade_date"],
                "factor": factor_values,
                "target": target_values,
                "eligible": data.get(
                    "eligible_universe", pd.Series(True, index=data.index)
                ).fillna(False).astype(bool),
            })
            daily_ic: list[float] = []
            daily_coverages: list[float] = []
            for _, group in frame.groupby("trade_date", sort=True):
                eligible_group = group[group["eligible"]]
                denominator = len(eligible_group)
                group = eligible_group.dropna(subset=["factor", "target"])
                daily_coverages.append(
                    len(group) / denominator if denominator else 0.0
                )
                if len(group) < 5 or group["factor"].nunique() < 2:
                    continue
                value = group["factor"].corr(group["target"], method="spearman")
                if pd.notna(value):
                    daily_ic.append(float(value) * sign)
            coverage = float(min(daily_coverages)) if daily_coverages else 0.0
            mean_ic = float(np.mean(daily_ic)) if daily_ic else None
            # Rank IC observations for overlapping horizons are autocorrelated;
            # use a Newey-West/HAC long-run standard deviation instead of IID
            # sample std when forming IR.
            if len(daily_ic) > 1:
                values = np.asarray(daily_ic, dtype=float)
                centered = values - values.mean()
                lag = max(1, min(int(horizon) - 1, len(values) - 1))
                long_run = float(np.mean(centered * centered))
                for k in range(1, lag + 1):
                    gamma = float(np.mean(centered[k:] * centered[:-k]))
                    long_run += 2.0 * (1.0 - k / (lag + 1.0)) * gamma
                hac_std = float(np.sqrt(max(long_run, 0.0)))
            else:
                hac_std = None
            information_ratio = (
                float(mean_ic / hac_std)
                if mean_ic is not None and hac_std not in (None, 0.0)
                else None
            )
            positive_ratio = (
                float(np.mean(np.asarray(daily_ic) > 0)) if daily_ic else None
            )
            status = (
                "PASS"
                if (
                    coverage >= min_coverage
                    and mean_ic is not None
                    and mean_ic >= min_rank_ic
                    and information_ratio is not None
                    and information_ratio >= min_information_ratio
                    and positive_ratio is not None
                    and positive_ratio >= min_positive_ic_ratio
                )
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
                "positive_ic_ratio": positive_ratio,
                "coverage": coverage,
                "daily_observations": len(daily_ic),
                "min_daily_coverage": coverage,
                "factor_sign": sign,
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
        "minimum_rank_ic": min_rank_ic,
        "minimum_information_ratio": min_information_ratio,
        "minimum_positive_ic_ratio": min_positive_ic_ratio,
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
    factor_weights: dict[str, float] | None = None,
    exclude_symbols: set[str] | None = None,
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
    weights = factor_weights or signs
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
            score += rank * float(weights.get(factor, signs.get(factor, 1.0)))
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
    constraint_breaches: list[dict[str, Any]] = []
    event_ledger: list[dict[str, Any]] = []
    realized_pnl_by_symbol: dict[str, float] = {}
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
        if exclude_symbols:
            signal = signal[~signal["symbol"].astype(str).isin(exclude_symbols)]
        picks = signal.head(top_k).copy()
        selected_count += len(picks)
        desired = set(picks["symbol"].astype(str))
        execution = day_rows(execution_date)
        # Portfolio sizing is frozen from the T-day close.  Looking up the
        # execution day's close here would use an end-of-session value at
        # 09:30 and contaminate both sells and buys.
        equity_before = mark_equity(signal_date)

        # Sell undesired holdings first.  A limit-down/suspended row freezes
        # the position and never fabricates a fill.
        for symbol in list(positions):
            if symbol in desired:
                continue
            info = execution.loc[symbol] if symbol in execution.index else None
            execution_limit_status = str(
                info.get("open_limit_status", info.get("limit_status", "UNKNOWN"))
            ) if info is not None else "UNKNOWN"
            if info is None or execution_limit_status in {"LIMIT_DOWN", "SUSPENDED", "NO_TRADE"} or int(float(info.get("is_suspended", 0) or 0)) == 1:
                frozen_shares += int(positions[symbol]["shares"])
                rejected += 1
                continue
            price = float(pd.to_numeric(info.get("open"), errors="coerce")) if pd.notna(info.get("open")) else 0.0
            if price <= 0:
                frozen_shares += int(positions[symbol]["shares"])
                rejected += 1
                continue
            shares = int(positions[symbol]["shares"])
            adv = float(pd.to_numeric(info.get("adv_cny"), errors="coerce")) if pd.notna(info.get("adv_cny")) else float("nan")
            if not np.isfinite(adv) or adv <= 0:
                frozen_shares += shares
                rejected += 1
                continue
            max_sell_shares = int((adv * config.adv_fraction / price) // config.lot_size * config.lot_size)
            if max_sell_shares <= 0:
                frozen_shares += shares
                rejected += 1
                continue
            sell_shares = min(shares, max_sell_shares)
            if sell_shares < shares:
                adv_limited_orders += 1
            max_adv_participation = max(
                max_adv_participation,
                float(sell_shares * price / max(adv, 1e-12)),
            )
            gross = sell_shares * price * (1.0 - slip)
            fee = gross * cost_rate
            cost_basis = float(positions[symbol].get("cost_basis", 0.0))
            net_pnl = (gross - fee) - (cost_basis * sell_shares)
            realized_pnl_by_symbol[symbol] = realized_pnl_by_symbol.get(symbol, 0.0) + net_pnl
            cash += gross - fee
            turnover += sell_shares * price
            if sell_shares >= shares:
                positions.pop(symbol, None)
            else:
                positions[symbol]["shares"] = shares - sell_shares
                positions[symbol]["cost_basis"] = cost_basis
            event_ledger.append({
                "trade_date": pd.Timestamp(execution_date).strftime("%Y-%m-%d"),
                "signal_date": pd.Timestamp(signal_date).strftime("%Y-%m-%d"),
                "execution_time": "09:30:00+08:00",
                "symbol": symbol,
                "side": "SELL",
                "shares": sell_shares,
                "price": price,
                "adv_cny": adv,
                "adv_participation": float(sell_shares * price / max(adv, 1e-12)),
                "gross_notional": sell_shares * price,
                "fee": fee,
                "slippage_bps": slippage_bps,
                "status": "FILLED",
            })

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
            if held_weight > max_stock_weight + 1e-12:
                constraint_breaches.append({
                    "trade_date": pd.Timestamp(execution_date).strftime("%Y-%m-%d"),
                    "type": "STOCK_WEIGHT_BREACH",
                    "symbol": held_symbol,
                    "actual": held_weight,
                    "limit": max_stock_weight,
                    "reason": "frozen_or_untradeable_position",
                })
        for held_industry, weight_value in industry_weight.items():
            if weight_value > config.max_industry_weight + 1e-12:
                constraint_breaches.append({
                    "trade_date": pd.Timestamp(execution_date).strftime("%Y-%m-%d"),
                    "type": "INDUSTRY_WEIGHT_BREACH",
                    "industry": held_industry,
                    "actual": weight_value,
                    "limit": config.max_industry_weight,
                    "reason": "frozen_or_untradeable_position",
                })
        for row in picks.itertuples(index=False):
            symbol = str(row.symbol)
            if symbol in positions:
                continue
            if symbol not in execution.index:
                rejected += 1
                continue
            info = execution.loc[symbol]
            execution_limit_status = str(
                info.get("open_limit_status", info.get("limit_status", "UNKNOWN"))
            )
            if execution_limit_status in {"LIMIT_UP", "SUSPENDED", "NO_TRADE"} or int(float(info.get("is_suspended", 0) or 0)) == 1:
                rejected += 1
                continue
            price = float(pd.to_numeric(info.get("open"), errors="coerce")) if pd.notna(info.get("open")) else 0.0
            adv = float(pd.to_numeric(info.get("adv_cny"), errors="coerce")) if pd.notna(info.get("adv_cny")) else float("nan")
            if price <= 0 or not np.isfinite(adv) or adv <= 0:
                rejected += 1
                continue
            industry = str(getattr(row, "industry", "UNKNOWN"))
            weight = min(1.0 / max(top_k, 1), max_stock_weight)
            weight = min(weight, max(config.max_industry_weight - industry_weight.get(industry, 0.0), 0.0))
            unconstrained_notional = min(equity_before * weight, cash)
            # ADV is a trailing amount snapshot computed strictly from dates
            # before the execution date.  Never use the execution day's full
            # amount, which is only known after the session.
            target_notional = min(unconstrained_notional, adv * config.adv_fraction)
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
                float(shares * price / max(adv, 1e-12)),
            )
            positions[symbol] = {
                "shares": shares,
                "industry": industry,
                "last_price": price,
                "cost_basis": (gross + fee) / max(shares, 1),
            }
            industry_weight[industry] = industry_weight.get(industry, 0.0) + (shares * price / max(equity_before, 1e-12))
            event_ledger.append({
                "trade_date": pd.Timestamp(execution_date).strftime("%Y-%m-%d"),
                "signal_date": pd.Timestamp(signal_date).strftime("%Y-%m-%d"),
                "execution_time": "09:30:00+08:00",
                "symbol": symbol,
                "side": "BUY",
                "shares": shares,
                "price": price,
                "adv_cny": adv,
                "adv_participation": float(shares * price / max(adv, 1e-12)),
                "gross_notional": shares * price,
                "fee": fee,
                "slippage_bps": slippage_bps,
                "status": "FILLED",
            })

        # Keep the signal day itself in the NAV series.  The next rebalance is
        # executed on the following day, so excluding next_signal_index would
        # silently drop every intermediate signal-day mark.
        next_execution_index = (
            next_signal_index + 1 if next_signal_index < len(dates) else len(dates)
        )
        period = dates[execution_index:next_execution_index]
        for trade_date in period:
            nav_rows.append({
                "trade_date": pd.Timestamp(trade_date).strftime("%Y-%m-%d"),
                "nav": mark_equity(trade_date),
                "valuation": "close",
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
            "status": "PASS" if (
                max_adv_participation <= config.adv_fraction + 1e-12
                and not constraint_breaches
            ) else "BLOCKED",
        },
        "constraint_breaches": constraint_breaches,
        "constraint_status": "CONSTRAINT_BREACH" if constraint_breaches else "PASS",
        "frozen_shares": frozen_shares,
        "nav_rows": int(len(nav)),
        "nav_series": nav.to_dict("records"),
        "adv_source": "prior_trading_day_rolling20_amount",
        "event_ledger": event_ledger,
        "realized_pnl_by_symbol": realized_pnl_by_symbol,
        "factor_weights": {str(key): float(value) for key, value in weights.items()},
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
    package_dir: Path | None = None,
    formal_pit_run_id: str | None = None,
    package_id: str | None = None,
    seal_sha256: str | None = None,
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
    package_blockers: list[str] = []
    package_identity: dict[str, Any] = {}
    if evidence_origin == "HISTORICAL_REAL":
        package_blockers, package_identity = _validate_formal_package_binding(
            package_dir=package_dir,
            formal_pit_run_id=formal_pit_run_id,
            package_id=package_id,
            seal_sha256=seal_sha256,
        )
        if package_blockers:
            if "open_limit_status" not in market.columns:
                package_blockers = ["market_open_limit_status_missing", *package_blockers]
            return _blocked(
                output_dir,
                package_blockers,
                panel_path=panel_path,
                market_path=market_path,
                release=release,
                strategy=strategy,
            )

    # An end-of-day `limit_status` is not a valid 09:30 execution input.  Real
    # evidence must provide an opening snapshot; synthetic fixtures may use the
    # legacy field but are labelled as such and can never become E3.
    if "open_limit_status" not in market.columns:
        if evidence_origin == "HISTORICAL_REAL":
            return _blocked(
                output_dir,
                ["market_open_limit_status_missing"],
                panel_path=panel_path,
                market_path=market_path,
                release=release,
                strategy=strategy,
            )
        market["open_limit_status"] = market["limit_status"]
        market["execution_status_source"] = "synthetic_legacy_limit_status"
    else:
        market["execution_status_source"] = "open_limit_status"
    # Compute a trailing ADV from amounts strictly before each trade date.  A
    # same-day full-session amount would leak information into the 09:30 fill.
    market = market.sort_values(["symbol", "trade_date"]).copy()
    market["amount"] = pd.to_numeric(market["amount"], errors="coerce")
    market["adv_cny"] = market.groupby("symbol", sort=False)["amount"].transform(
        lambda series: series.shift(1).rolling(20, min_periods=1).mean()
    )

    panel["trade_date"] = pd.to_datetime(panel["trade_date"], errors="coerce").dt.normalize()
    market["trade_date"] = pd.to_datetime(market["trade_date"], errors="coerce").dt.normalize()
    panel["symbol"] = panel["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    market["symbol"] = market["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    merged = panel.merge(
        market,
        on=["trade_date", "symbol"],
        how="left",
        indicator=True,
        suffixes=("", "_market"),
    )
    eligible = merged["eligible_universe"].fillna(False).astype(bool)
    missing_market = eligible & merged["_merge"].ne("both")
    coverage_rows: list[dict[str, Any]] = []
    if eligible.any():
        for day, group in merged.loc[eligible].groupby("trade_date", sort=True):
            denom = len(group)
            matched = int(group["_merge"].eq("both").sum())
            coverage = matched / denom if denom else 0.0
            coverage_rows.append({
                "trade_date": pd.Timestamp(day).strftime("%Y-%m-%d"),
                "eligible_universe_rows": denom,
                "matched_market_rows": matched,
                "coverage": coverage,
            })
            if coverage < 0.95:
                package_blockers.append(
                    f"daily_market_coverage_below_95:{pd.Timestamp(day).date()}:{coverage:.4f}"
                )
    if missing_market.any():
        package_blockers.append(
            f"eligible_market_rows_dropped:{int(missing_market.sum())}"
        )
    data = merged.loc[merged["_merge"].eq("both")].drop(columns=["_merge"])
    if package_blockers:
        return _blocked(
            output_dir,
            package_blockers,
            panel_path=panel_path,
            market_path=market_path,
            release=release,
            strategy=strategy,
        )
    dates = sorted(data["trade_date"].dropna().unique())
    windows = _build_nested_windows([pd.Timestamp(value) for value in dates])
    holdout_start = _holdout_start([pd.Timestamp(value) for value in dates])
    results: list[dict[str, Any]] = []
    challengers = [challenger] if challenger else list(config.challengers)
    experiment_n_trials = max(
        len(challengers)
        * len(config.holdings)
        * len(config.rebalance_days)
        * len(config.cost_rates)
        * len(config.slippage_bps)
        * len(config.stock_weight_caps),
        1,
    )
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
            factor_signs=config.factor_signs.get(challenger_name, {}),
            min_rank_ic=config.min_rank_ic,
            min_information_ratio=config.min_information_ratio,
            min_positive_ic_ratio=config.min_positive_ic_ratio,
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
                                    slippage_bps=slippage_bps * 2,
                                    max_stock_weight=float(max_stock_weight),
                                    config=config,
                                )
                                result["two_x_cost_annualized_return"] = stress.get("annualized_return")
                                result["cost_stress_status"] = (
                                    "PASS"
                                    if stress.get("annualized_return") is not None
                                    and stress.get("constraint_status") == "PASS"
                                    else "BLOCKED"
                                )
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
                                nested = _nested_walk_forward_report(
                                    data,
                                    challenger=challenger_name,
                                    top_k=top_k,
                                    rebalance_days=rebalance_days,
                                    cost_rate=cost_rate,
                                    slippage_bps=slippage_bps,
                                    max_stock_weight=float(max_stock_weight),
                                    config=config,
                                    windows=windows,
                                )
                                result["nested_walk_forward"] = nested
                                result["positive_oos_window_ratio"] = nested.get(
                                    "positive_test_window_ratio"
                                )
                                result["pbo"] = nested.get("pbo_proxy")
                                result["pbo_method"] = "negative_oos_window_proxy"
                                result["pbo_qualified"] = bool(nested.get("pbo_qualified"))
                                result["n_trials"] = experiment_n_trials
                                result["dsr_confidence"] = _dsr_confidence(
                                    result, n_trials=experiment_n_trials
                                )
                                positive_year_ratio, max_year_contribution = _annual_positive_contribution(
                                    result.get("nav_series") or [], config.initial_capital_cny
                                )
                                result["positive_year_ratio"] = positive_year_ratio
                                result["max_positive_year_contribution"] = max_year_contribution
                                # Remove the five most profitable symbols from the
                                # sample and rerun the exact same challenger path.
                                reference_data = data
                                if holdout_start is not None:
                                    reference_data = data[data["trade_date"] < holdout_start]
                                reference_result = _simulate(
                                    reference_data,
                                    challenger=challenger_name,
                                    top_k=top_k,
                                    rebalance_days=rebalance_days,
                                    cost_rate=cost_rate,
                                    slippage_bps=slippage_bps,
                                    max_stock_weight=float(max_stock_weight),
                                    config=config,
                                )
                                realized = pd.Series(
                                    reference_result.get("realized_pnl_by_symbol") or {},
                                    dtype=float,
                                )
                                excluded = set(realized.nlargest(5).index.astype(str))
                                if len(excluded) < 5:
                                    result["top5_concentration_status"] = "BLOCKED"
                                    result["top5_concentration_blocker"] = "insufficient_realized_trade_pnl"
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
                                    exclude_symbols=excluded,
                                )
                                result["remove_top5_annualized_return"] = no_top5.get("annualized_return")
                                result["remove_top5_return_positive"] = bool(
                                    len(excluded) == 5
                                    and no_top5.get("annualized_return") is not None
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
                                    len(excluded) == 5
                                    and no_top5_oos.get("annualized_return") is not None
                                    and float(no_top5_oos.get("annualized_return")) > 0
                                )
                                result["excluded_top5_symbols"] = sorted(excluded)
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
            **package_identity,
        },
        "formal_pit_run_id": formal_pit_run_id,
        "package_id": package_id,
        "package_seal_sha256": seal_sha256,
        "market_coverage_by_date": coverage_rows,
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
        "execution_data_contract": {
            "limit_status_field": "open_limit_status",
            "limit_status_fallback": "synthetic_only",
            "adv_field": "adv_cny",
            "adv_definition": "prior_trading_day_rolling20_amount",
            "same_day_amount_used_for_decision": False,
            "same_day_close_used_for_decision": False,
        },
        "cost_model": {"cost_rates": config.cost_rates, "slippage_bps": config.slippage_bps},
        "challengers": {
            name: list(factors) for name, factors in config.challengers.items()
        },
        "factor_signs": config.factor_signs,
        "factor_ic_horizons": list(config.ic_horizons),
        "n_trials": experiment_n_trials,
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
            **package_identity,
        },
        "formal_pit_run_id": formal_pit_run_id,
        "package_id": package_id,
        "package_seal_sha256": seal_sha256,
        "market_coverage_by_date": coverage_rows,
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
            "selection_contract": "train_fit_validation_select_test_evaluate_v1",
            "windows_are_executed": True,
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
        "two_x_cost_stress_execution_pass": bool(best.get("cost_stress_status") == "PASS"),
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
    parser.add_argument("--package-dir", type=Path)
    parser.add_argument("--formal-pit-run-id", default=None)
    parser.add_argument("--package-id", default=None)
    parser.add_argument("--seal-sha256", default=None)
    args = parser.parse_args()
    result = run_topk_alpha_lab(
        panel_path=args.panel,
        market_path=args.market,
        output_dir=args.output_dir,
        evidence_origin=args.evidence_origin,
        challenger=args.challenger,
        release=args.release,
        strategy=args.strategy,
        package_dir=args.package_dir,
        formal_pit_run_id=args.formal_pit_run_id,
        package_id=args.package_id,
        seal_sha256=args.seal_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
