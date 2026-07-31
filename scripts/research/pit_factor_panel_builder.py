#!/usr/bin/env python3
"""Construct a long-horizon PIT factor panel from qualified frozen snapshots.

The builder never fetches or invents data.  All four snapshot families and a
release-scoped source manifest must be supplied.  Missing inputs, duplicate
keys, late availability, incomplete universe coverage, or insufficient
history produce a BLOCKED report and no qualified panel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha, load_validation_profile


SOURCE_NAMES = (
    "market",
    "universe",
    "financial",
    "industry",
    "adjustment",
)
REQUIRED_COLUMNS = {
    "market": {
        "trade_date",
        "symbol",
        "open",
        "close",
        "pre_close",
        "amount",
        "circ_mv",
        "market_return",
        "market_regime",
        "market_available_at",
    },
    "universe": {
        "trade_date",
        "symbol",
        "is_listed",
        "is_st",
        "is_suspended",
        "limit_status",
        "security_status_transition",
        "universe_available_at",
    },
    "financial": {
        "trade_date",
        "symbol",
        "pb",
        "financial_period_end",
        "announcement_date",
        "financial_available_at",
        "revision_id",
        "financial_source_snapshot_sha",
    },
    "industry": {
        "trade_date",
        "symbol",
        "industry",
        "industry_available_at",
    },
    "adjustment": {
        "trade_date",
        "symbol",
        "adj_factor",
        "corporate_action_type",
        "ex_date",
        "record_date",
        "adjustment_factor_version",
        "adjustment_available_at",
    },
}


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _rank_score(series: pd.Series, *, reverse: bool = False) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if reverse:
        numeric = -numeric
    return numeric.rank(method="average", pct=True) - 0.5


def _blocked_report(
    output_dir: Path,
    profile_name: str,
    blockers: list[str],
    source_paths: dict[str, Path | None],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "alpha_v4_7_pit_factor_panel_builder_v1",
        "profile": profile_name,
        "status": "BLOCKED",
        "evidence_level": "E0",
        "synthetic_evidence_level": "S0",
        "panel_qualified": False,
        "historical_evidence_qualified": False,
        "synthetic_contract_qualified": False,
        "source_paths": {
            name: str(path) if path is not None else None
            for name, path in source_paths.items()
        },
        "blockers": sorted(set(blockers)),
        "capital_authority": False,
        "automatic_short_panel_fallback": False,
    }
    report["content_sha256"] = canonical_sha(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pit_factor_panel_builder_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def build_pit_factor_panel(
    *,
    market_path: Path | None,
    universe_path: Path | None,
    financial_path: Path | None,
    industry_path: Path | None,
    adjustment_path: Path | None,
    source_manifest_path: Path | None,
    output_dir: Path,
    profile_name: str = "alpha_v4_7",
    adapter_report_path: Path | None = None,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    profile = load_validation_profile(profile_name)
    source_paths = {
        "market": market_path,
        "universe": universe_path,
        "financial": financial_path,
        "industry": industry_path,
        "adjustment": adjustment_path,
        "source_manifest": source_manifest_path,
    }
    missing = [name for name, path in source_paths.items() if path is None]
    missing.extend(
        name
        for name, path in source_paths.items()
        if path is not None and not path.exists()
    )
    if missing:
        return _blocked_report(
            output_dir,
            profile_name,
            [f"missing_input:{name}" for name in missing],
            source_paths,
        )
    assert (
        market_path
        and universe_path
        and financial_path
        and industry_path
        and adjustment_path
    )
    assert source_manifest_path
    frames = {
        "market": _read_table(market_path),
        "universe": _read_table(universe_path),
        "financial": _read_table(financial_path),
        "industry": _read_table(industry_path),
        "adjustment": _read_table(adjustment_path),
    }
    blockers: list[str] = []
    for name, frame in frames.items():
        absent = sorted(REQUIRED_COLUMNS[name] - set(frame.columns))
        blockers.extend(f"{name}_column_missing:{column}" for column in absent)
    if blockers:
        return _blocked_report(
            output_dir, profile_name, blockers, source_paths
        )
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if str(manifest.get("status")) != "QUALIFIED":
        blockers.append("source_manifest_not_qualified")
    manifest_sources = manifest.get("sources") or {}
    evidence_origin = str(manifest.get("evidence_origin") or "")
    if evidence_origin not in {"SYNTHETIC", "HISTORICAL_REAL"}:
        blockers.append("source_manifest_evidence_origin_invalid")
    if not str(manifest.get("schema_semantic_version") or ""):
        blockers.append("source_manifest_schema_semantic_version_missing")
    if not str(manifest.get("field_definition_hash") or ""):
        blockers.append("source_manifest_field_definition_hash_missing")
    # --- Adapter report binding (required for HISTORICAL_REAL, skipped in fixture_mode) ---
    if evidence_origin == "HISTORICAL_REAL" and not fixture_mode:
        if adapter_report_path is None or not adapter_report_path.exists():
            blockers.append("adapter_report_required_for_historical_real")
        else:
            adapter = json.loads(adapter_report_path.read_text(encoding="utf-8"))
            if str(adapter.get("status")) != "PASS":
                blockers.append("adapter_report_not_pass")
            if adapter.get("adapter_ready") is not True:
                blockers.append("adapter_not_ready")
            if str(adapter.get("historical_evidence_level")) != "E1":
                blockers.append("adapter_not_historical_e1")
            actual_manifest_sha = _file_sha(source_manifest_path)
            if str(adapter.get("manifest_sha256") or "") != actual_manifest_sha:
                blockers.append("adapter_manifest_sha_mismatch")
            adapter_config = adapter.get("config_path")
            if adapter_config and not Path(str(adapter_config)).exists():
                blockers.append("adapter_config_not_found")
        # Manifest self-hash integrity
        manifest_raw = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        recomputed_content = canonical_sha(
            {k: v for k, v in manifest_raw.items() if k != "content_sha256"}
        )
        if str(manifest_raw.get("content_sha256") or "") != recomputed_content:
            blockers.append("manifest_content_sha256_invalid")
    # --- End adapter binding ---
    file_map = {
        "market": market_path,
        "universe": universe_path,
        "financial": financial_path,
        "industry": industry_path,
        "adjustment": adjustment_path,
    }
    source_sha = {name: _file_sha(path) for name, path in file_map.items()}
    for name, sha in source_sha.items():
        if str((manifest_sources.get(name) or {}).get("sha256") or "") != sha:
            blockers.append(f"source_manifest_sha_mismatch:{name}")
    for name, frame in frames.items():
        frame["trade_date"] = pd.to_datetime(
            frame["trade_date"].astype(str), errors="coerce"
        ).dt.normalize()
        frame["symbol"] = (
            frame["symbol"]
            .astype(str)
            .str.extract(r"(\d+)", expand=False)
            .str.zfill(6)
            .str[-6:]
        )
        duplicate_count = int(frame.duplicated(["trade_date", "symbol"]).sum())
        if duplicate_count:
            blockers.append(f"duplicate_key:{name}:{duplicate_count}")
        if frame["trade_date"].isna().any() or frame["symbol"].isna().any():
            blockers.append(f"invalid_key:{name}")
    if blockers:
        return _blocked_report(
            output_dir, profile_name, blockers, source_paths
        )
    panel = frames["universe"].merge(
        frames["market"], on=["trade_date", "symbol"], how="left"
    )
    panel = panel.merge(
        frames["financial"], on=["trade_date", "symbol"], how="left"
    )
    panel = panel.merge(
        frames["industry"], on=["trade_date", "symbol"], how="left"
    )
    panel = panel.merge(
        frames["adjustment"], on=["trade_date", "symbol"], how="left"
    )
    availability_columns = [
        "market_available_at",
        "financial_available_at",
        "universe_available_at",
        "industry_available_at",
        "adjustment_available_at",
    ]
    for column in availability_columns:
        missing = panel[column].isna()
        if missing.any():
            blockers.append(f"missing_available_at:{column}:{int(missing.sum())}")
        parsed = pd.to_datetime(panel[column], errors="coerce", utc=True)
        panel[column] = parsed
        if parsed.isna().any():
            blockers.append(f"invalid_or_timezone_missing:{column}")
    signal_time = pd.to_datetime(
        panel["trade_date"].dt.strftime("%Y-%m-%d")
        + "T15:30:00+08:00",
        utc=True,
    )
    panel["signal_time"] = signal_time
    pb_numeric = pd.to_numeric(panel["pb"], errors="coerce")
    panel["pb"] = pb_numeric
    for column in availability_columns:
        if (panel[column] > signal_time).any():
            blockers.append(f"available_after_signal:{column}")
    # --- Semantic validation (HISTORICAL_REAL only) ---
    if evidence_origin == "HISTORICAL_REAL":
        # Market return must be cross-sectionally uniform per trade_date
        mr_dispersion = panel.groupby("trade_date")["market_return"].nunique()
        if (mr_dispersion > 1).any():
            n_bad = int((mr_dispersion > 1).sum())
            blockers.append(f"market_return_not_cross_sectional:{n_bad}_dates")
        # Financial time chain: period_end <= announcement <= availability <= signal
        fin_sub = panel[["financial_period_end", "announcement_date",
                          "financial_available_at", "trade_date"]].copy()
        for col in ["financial_period_end", "announcement_date"]:
            fin_sub[col] = pd.to_datetime(fin_sub[col].astype(str), errors="coerce")
        fin_sub = fin_sub.dropna(subset=["financial_period_end", "announcement_date"])
        if not fin_sub.empty:
            if (fin_sub["financial_period_end"] > fin_sub["announcement_date"]).any():
                n_bad = int((fin_sub["financial_period_end"] > fin_sub["announcement_date"]).sum())
                blockers.append(f"financial_period_after_announcement:{n_bad}")
        # PB non-negative
        if (panel["pb"].dropna() < 0).any():
            blockers.append("pb_negative_values_detected")
    listed = panel["is_listed"].fillna(False).astype(bool)
    required_coverage = {
        "market": panel["close"].notna(),
        "industry": panel["industry"].notna(),
        "adjustment": panel["adj_factor"].notna(),
        "financial": panel["pb"].notna()
        & panel["financial_available_at"].notna(),
        "universe_status": panel[
            ["is_st", "is_suspended", "limit_status"]
        ].notna().all(axis=1),
    }
    daily_coverage_rows: list[dict[str, Any]] = []
    for date, group in panel.loc[listed].groupby("trade_date", sort=True):
        row: dict[str, Any] = {
            "trade_date": date.date().isoformat(),
            "listed_symbols": int(len(group)),
        }
        for name, mask in required_coverage.items():
            row[f"{name}_coverage"] = float(mask.loc[group.index].mean())
        daily_coverage_rows.append(row)
    daily_coverage = pd.DataFrame(daily_coverage_rows)
    min_coverage = float(
        profile["economic_alpha_qualification"]["min_universe_coverage"]
    )
    for name in required_coverage:
        column = f"{name}_coverage"
        if daily_coverage.empty or float(daily_coverage[column].min()) < min_coverage:
            blockers.append(f"daily_coverage_below_threshold:{name}")
    eligible = (
        listed
        & ~panel["is_st"].fillna(True).astype(bool)
        & ~panel["is_suspended"].fillna(True).astype(bool)
        & panel["limit_status"].fillna("").isin({"NORMAL", "NONE"})
    )
    panel["eligible_universe"] = eligible
    panel["ret_1d"] = (
        pd.to_numeric(panel["close"], errors="coerce")
        / pd.to_numeric(panel["pre_close"], errors="coerce")
        - 1.0
    )
    panel = panel.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    panel["momentum_raw"] = panel.groupby("symbol")["close"].pct_change(20)
    panel["volatility_raw"] = panel.groupby("symbol")["ret_1d"].transform(
        lambda values: values.rolling(20, min_periods=10).std()
    )
    panel["amihud_raw"] = (
        panel["ret_1d"].abs()
        / pd.to_numeric(panel["amount"], errors="coerce").replace(0.0, np.nan)
    )
    panel["liquidity_raw"] = panel.groupby("symbol")["amihud_raw"].transform(
        lambda values: values.rolling(20, min_periods=10).mean()
    )
    market_return = pd.to_numeric(panel["market_return"], errors="coerce")
    beta_parts: list[pd.Series] = []
    for _, group in panel.groupby("symbol", sort=False):
        stock = group["ret_1d"]
        market = market_return.loc[group.index]
        beta_parts.append(
            stock.rolling(20, min_periods=10).cov(market)
            / market.rolling(20, min_periods=10).var().replace(0.0, np.nan)
        )
    panel["market_beta_raw"] = pd.concat(beta_parts).sort_index()
    raw_factors = {
        "market_beta": ("market_beta_raw", False),
        "size": ("circ_mv", True),
        "volatility": ("volatility_raw", True),
        "liquidity": ("liquidity_raw", True),
        "momentum": ("momentum_raw", False),
        "value": ("pb", True),
    }
    for factor, (column, reverse) in raw_factors.items():
        numeric = pd.to_numeric(panel[column], errors="coerce")
        if reverse:
            numeric = -numeric
        panel[factor] = numeric.groupby(panel["trade_date"]).rank(
            method="average", pct=True
        )
        panel[factor] = panel[factor] - 0.5
    dates = sorted(panel["trade_date"].dropna().unique())
    warmup_dates = set(dates[:20])
    qualified = panel.loc[
        panel["eligible_universe"] & ~panel["trade_date"].isin(warmup_dates)
    ].copy()
    factor_coverage = {
        factor: float(qualified[factor].notna().mean())
        for factor in raw_factors
    }
    for factor, coverage in factor_coverage.items():
        if coverage < min_coverage:
            blockers.append(f"factor_coverage_below_threshold:{factor}")
    unique_dates = int(qualified["trade_date"].nunique())
    min_days = int(profile["economic_alpha_qualification"]["min_trading_days"])
    if unique_dates < min_days:
        blockers.append(f"history_below_{min_days}_days")
    target_days = int(profile["economic_alpha_qualification"].get("target_trading_days", 504))
    target_met = unique_dates >= target_days
    min_regimes = int(profile["economic_alpha_qualification"].get("min_market_regimes", 3))
    regime_values = qualified["market_regime"].dropna().unique()
    if evidence_origin == "HISTORICAL_REAL":
        if len(regime_values) < min_regimes:
            blockers.append(f"market_regime_diversity_below_{min_regimes}:found_{len(regime_values)}")
    fdh = str(manifest.get("field_definition_hash") or "")
    if evidence_origin == "HISTORICAL_REAL":
        if fdh.startswith("matCHANGEME") or len(fdh) != 64:
            blockers.append("field_definition_hash_is_placeholder")
        if qualified["security_status_transition"].dropna().nunique() <= 1:
            blockers.append("security_status_transition_constant_or_missing")
        if qualified["corporate_action_type"].dropna().nunique() <= 1:
            blockers.append("corporate_action_type_constant_or_missing")
    elif fdh.startswith("matCHANGEME"):
        blockers.append("field_definition_hash_is_placeholder")
    status = "PASS" if not blockers else "BLOCKED"
    historical_qualified = (
        status == "PASS" and evidence_origin == "HISTORICAL_REAL"
    )
    synthetic_qualified = (
        status == "PASS" and evidence_origin == "SYNTHETIC"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_columns = [
        "trade_date",
        "symbol",
        "signal_time",
        "eligible_universe",
        "is_st",
        "is_suspended",
        "limit_status",
        "security_status_transition",
        "industry",
        "adj_factor",
        "corporate_action_type",
        "ex_date",
        "record_date",
        "adjustment_factor_version",
        "financial_period_end",
        "announcement_date",
        "revision_id",
        "financial_source_snapshot_sha",
        "market_regime",
        *raw_factors,
        *availability_columns,
    ]
    qualified["trade_date"] = qualified["trade_date"].dt.date.astype(str)
    for column in ["signal_time", *availability_columns]:
        qualified[column] = qualified[column].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Only write qualified panel on PASS; BLOCKED writes diagnostic only
    if status == "PASS":
        panel_path = output_dir / "factor_panel_daily.parquet"
        qualified[panel_columns].to_parquet(panel_path, index=False)
        panel_sha = _file_sha(panel_path)
    else:
        diag_dir = output_dir / "UNQUALIFIED_DIAGNOSTIC"
        diag_dir.mkdir(parents=True, exist_ok=True)
        panel_path = None
        panel_sha = None
        # Optional diagnostic panel for debugging
        diag_panel_path = diag_dir / "factor_panel_daily.parquet"
        qualified[panel_columns].to_parquet(diag_panel_path, index=False)
    coverage_path = output_dir / "complete_universe_coverage.csv"
    daily_coverage.to_csv(coverage_path, index=False)
    report: dict[str, Any] = {
        "schema_version": "alpha_v4_7_pit_factor_panel_builder_v1",
        "profile": profile_name,
        "status": status,
        "evidence_level": "E3" if historical_qualified else "E0",
        "synthetic_evidence_level": "S3" if synthetic_qualified else "S0",
        "panel_qualified": status == "PASS",
        "historical_evidence_qualified": historical_qualified,
        "synthetic_contract_qualified": synthetic_qualified,
        "evidence_origin": evidence_origin,
        "release": manifest.get("release"),
        "sample_start": (
            qualified["trade_date"].min() if not qualified.empty else None
        ),
        "sample_end": (
            qualified["trade_date"].max() if not qualified.empty else None
        ),
        "unique_dates": unique_dates,
        "symbols": int(qualified["symbol"].nunique()),
        "rows": int(len(qualified)),
        "minimum_daily_universe_coverage": min_coverage,
        "target_trading_days": target_days,
        "target_trading_days_met": target_met,
        "core_start_required": "2018-01-01",
        "core_start_met": (
            str(qualified["trade_date"].min()) <= "2018-01-01"
            if not qualified.empty else False
        ),
        "market_regime_count": len(regime_values),
        "market_regime_required": min_regimes,
        "factor_coverage": factor_coverage,
        "source_sha256": source_sha,
        "source_manifest_sha256": _file_sha(source_manifest_path),
        "panel_path": str(panel_path) if panel_path else None,
        "panel_sha256": panel_sha,
        "coverage_path": str(coverage_path),
        "coverage_sha256": _file_sha(coverage_path),
        "blockers": sorted(set(blockers)),
        "capital_authority": False,
        "automatic_short_panel_fallback": False,
    }
    report["content_sha256"] = canonical_sha(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    (output_dir / "pit_factor_panel_builder_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", type=Path)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--financial", type=Path)
    parser.add_argument("--industry", type=Path)
    parser.add_argument("--adjustment", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--adapter-report", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", default="alpha_v4_7")
    parser.add_argument("--fixture-mode", action="store_true", default=False)
    args = parser.parse_args()
    result = build_pit_factor_panel(
        market_path=args.market,
        universe_path=args.universe,
        financial_path=args.financial,
        industry_path=args.industry,
        adjustment_path=args.adjustment,
        source_manifest_path=args.source_manifest,
        output_dir=args.output_dir,
        profile_name=args.profile,
        adapter_report_path=args.adapter_report,
        fixture_mode=args.fixture_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
