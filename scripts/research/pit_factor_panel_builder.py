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
from runtime.fail_closed import fail_closed
from runtime.pit_semantic_contract import (
    get_available_at_column,
    get_contract_sha256,
    get_source_families,
    get_required_columns,
    get_primary_key,
    get_canonical_execution_columns,
    get_economic_columns,
    signal_time_for_trade_dates,
    validate_explicit_timezone,
)


LEGACY_SOURCE_NAMES = (
    "market",
    "universe",
    "financial",
    "industry",
    "adjustment",
)
# The public source registry is canonical and contains all eight PIT families.
# The legacy five-family function signature is retained for synthetic research
# fixtures; the formal pipeline passes all families from this canonical list
# and is strict about their presence.
CANONICAL_SOURCE_NAMES = get_source_families()
SOURCE_NAMES = CANONICAL_SOURCE_NAMES
REQUIRED_COLUMNS = {name: get_required_columns(name) for name in SOURCE_NAMES}


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


def _asof_enrich(
    target: pd.DataFrame,
    source: pd.DataFrame,
    *,
    source_time: str,
    target_time: str,
    source_columns: list[str],
    validity_end: str | None = None,
    signal_time_column: str | None = None,
    source_join_time: str | None = None,
) -> pd.DataFrame:
    """Attach one point-in-time source row to each target row.

    Financial revisions and industry SCD records are dimension/event rows,
    not daily fact rows.  A direct `(trade_date, symbol)` merge would either
    duplicate the panel or select a future revision.  This helper performs a
    deterministic per-symbol backward as-of join and invalidates rows whose
    selected SCD interval or availability timestamp is not legal.
    """
    join_time = source_join_time or source_time
    required = {"symbol", source_time, join_time}
    if validity_end:
        required.add(validity_end)
    if not required.issubset(source.columns):
        missing = sorted(required - set(source.columns))
        raise ValueError(f"asof_source_columns_missing:{','.join(missing)}")
    if not {"symbol", target_time}.issubset(target.columns):
        missing = sorted({"symbol", target_time} - set(target.columns))
        raise ValueError(f"asof_target_columns_missing:{','.join(missing)}")

    left = target[["symbol", target_time]].copy()
    left["_row_id"] = target.index
    left["_target_ts"] = pd.to_datetime(left[target_time], errors="coerce", utc=True)
    # The join timestamp and the availability timestamp can differ (industry
    # SCD uses `valid_from` for membership and `industry_available_at` for
    # PIT legality).  Keep both explicit and avoid duplicate columns.
    source_columns = list(dict.fromkeys(source_columns))
    right_columns = [
        column for column in source_columns if column not in {"symbol", join_time}
    ]
    right = source[["symbol", join_time, *right_columns]].copy()
    right["_source_ts"] = pd.to_datetime(right[join_time], errors="coerce", utc=True)
    if validity_end:
        right["_valid_to_ts"] = pd.to_datetime(
            right[validity_end], errors="coerce", utc=True
        )
    left = left.dropna(subset=["_target_ts"]).sort_values(
        ["_target_ts", "symbol"]
    )
    right = right.dropna(subset=["_source_ts"]).sort_values(
        ["_source_ts", "symbol"]
    )
    # A source can carry several revisions observed at one timestamp.  Keep
    # the highest revision deterministically when the field is available;
    # the canonical schema still audits duplicate primary keys separately.
    dedupe_keys = ["symbol", "_source_ts"]
    sort_columns = ["symbol", "_source_ts"]
    if "revision_sequence" in right.columns:
        right["_revision_sequence_numeric"] = pd.to_numeric(
            right["revision_sequence"], errors="coerce"
        )
        sort_columns.append("_revision_sequence_numeric")
    if "revision_id" in right.columns:
        sort_columns.append("revision_id")
    right = right.sort_values(sort_columns).drop_duplicates(
        dedupe_keys, keep="last"
    )
    if left.empty or right.empty:
        enriched = target.copy()
        for column in source_columns:
            enriched[column] = pd.NaT
        return enriched
    joined = pd.merge_asof(
        left,
        right.sort_values(["_source_ts", "symbol"]),
        left_on="_target_ts",
        right_on="_source_ts",
        by="symbol",
        direction="backward",
        allow_exact_matches=True,
    )
    joined = joined.set_index("_row_id").reindex(target.index)
    enriched = target.copy()
    for column in source_columns:
        if column not in joined.columns:
            enriched[column] = pd.NaT
            continue
        values = joined[column].copy()
        legal = values.notna()
        if validity_end and "_valid_to_ts" in joined.columns:
            target_ts = joined["_target_ts"]
            valid_to = joined["_valid_to_ts"]
            legal &= valid_to.isna() | target_ts.lt(valid_to)
        if source_time in joined.columns:
            available = pd.to_datetime(joined[source_time], errors="coerce", utc=True)
            signal_column = signal_time_column or target_time
            signal = pd.to_datetime(target[signal_column], errors="coerce", utc=True)
            legal &= available.notna() & signal.notna() & available.le(signal)
        enriched[column] = values.where(legal)
    return enriched


def _blocked_report(
    output_dir: Path,
    profile_name: str,
    blockers: list[str],
    source_paths: dict[str, Path | None],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Remove stale PASS artifacts (but NOT source manifest — that belongs to Adapter)
    for stale_name in ["factor_panel_daily.parquet"]:
        (output_dir / stale_name).unlink(missing_ok=True)
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
    profile_name: str = "alpha_v3_2",
    adapter_report_path: Path | None = None,
    fixture_mode: bool = False,
    trade_calendar_path: Path | None = None,
    security_lifecycle_path: Path | None = None,
    corporate_actions_path: Path | None = None,
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
    optional_paths = {
        "trade_calendar": trade_calendar_path,
        "security_lifecycle": security_lifecycle_path,
        "corporate_actions": corporate_actions_path,
    }
    source_paths.update({k: v for k, v in optional_paths.items() if v is not None})
    try:
        return _build_pit_factor_panel_impl(
            market_path=market_path, universe_path=universe_path,
            financial_path=financial_path, industry_path=industry_path,
            adjustment_path=adjustment_path,
            source_manifest_path=source_manifest_path,
            output_dir=output_dir, profile_name=profile_name,
            adapter_report_path=adapter_report_path, fixture_mode=fixture_mode,
            trade_calendar_path=trade_calendar_path,
            security_lifecycle_path=security_lifecycle_path,
            corporate_actions_path=corporate_actions_path,
            profile=profile, source_paths=source_paths,
        )
    except Exception as exc:
        return fail_closed(
            "pit_factor_panel_builder", "build",
            exc, output_dir=output_dir,
        )


def _build_pit_factor_panel_impl(
    *,
    market_path: Path | None,
    universe_path: Path | None,
    financial_path: Path | None,
    industry_path: Path | None,
    adjustment_path: Path | None,
    source_manifest_path: Path | None,
    output_dir: Path,
    profile_name: str,
    adapter_report_path: Path | None,
    fixture_mode: bool,
    trade_calendar_path: Path | None,
    security_lifecycle_path: Path | None,
    corporate_actions_path: Path | None,
    profile: dict[str, Any],
    source_paths: dict[str, Path | None],
) -> dict[str, Any]:
    # (body of original build_pit_factor_panel follows, minus the profile load + source_paths init)
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
    try:
        manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _blocked_report(
            output_dir,
            profile_name,
            [
                f"source_manifest_unreadable:{type(exc).__name__}",
                f"unhandled_manifest_exception:{type(exc).__name__}",
            ],
            source_paths,
        )
    evidence_origin = str(manifest.get("evidence_origin") or "")
    semantic_version = str(manifest.get("schema_semantic_version") or "")
    strict_contract = evidence_origin == "HISTORICAL_REAL" or any(
        path is not None
        for path in (trade_calendar_path, security_lifecycle_path, corporate_actions_path)
    )
    synthetic_compat = evidence_origin == "SYNTHETIC"
    legacy_fixture = synthetic_compat and semantic_version.startswith("fixture-")
    optional_paths = {
        "trade_calendar": trade_calendar_path,
        "security_lifecycle": security_lifecycle_path,
        "corporate_actions": corporate_actions_path,
    }
    strict_optional_blockers = (
        [f"{name}_snapshot_missing" for name, path in optional_paths.items() if path is None]
        if strict_contract
        else []
    )
    frames = {
        "market": _read_table(market_path),
        "universe": _read_table(universe_path),
        "financial": _read_table(financial_path),
        "industry": _read_table(industry_path),
        "adjustment": _read_table(adjustment_path),
    }
    for name, path in optional_paths.items():
        if path is not None:
            frames[name] = _read_table(path)
    blockers: list[str] = strict_optional_blockers
    for name, frame in frames.items():
        absent = sorted(get_required_columns(name) - set(frame.columns))
        # Legacy aliases/defaults are restricted to explicitly synthetic
        # fixture inputs.  A versioned formal source must carry the canonical
        # family-specific fields; accepting `available_at` or `is_tradable`
        # here would create a second authoritative semantic contract.
        if synthetic_compat:
            aliases = {
                "available_at": get_available_at_column(name),
                "is_tradable": "is_listed",
                "action_type": "corporate_action_type",
            }
            for old, new in aliases.items():
                if new in absent and old in frame.columns:
                    frame[new] = frame[old]
                    absent.remove(new)
            if name == "universe":
                for column, value in {
                    "is_st": 0,
                    "is_suspended": 0,
                    "limit_status": "NORMAL",
                    "security_status_transition": "LEGACY",
                }.items():
                    if column not in frame.columns:
                        frame[column] = value
            elif name == "financial" and "revision_sequence" not in frame.columns:
                frame["revision_sequence"] = 1
            elif name == "industry":
                if "valid_from" not in frame.columns:
                    frame["valid_from"] = frame.get("trade_date")
                if "valid_to" not in frame.columns:
                    frame["valid_to"] = "2099-12-31"
                if "industry_code" not in frame.columns:
                    frame["industry_code"] = frame.get("industry", "UNKNOWN")
                if "industry_name" not in frame.columns:
                    frame["industry_name"] = frame.get("industry", "UNKNOWN")
        if absent and (strict_contract or not legacy_fixture):
            blockers.extend(f"{name}_column_missing:{column}" for column in absent)
        if strict_contract and name == "market":
            blockers.extend(
                f"market_execution_column_missing:{column}"
                for column in sorted(get_canonical_execution_columns(name) - set(frame.columns))
            )
        if strict_contract and name == "corporate_actions":
            blockers.extend(
                f"corporate_action_economic_column_missing:{column}"
                for column in sorted(get_economic_columns(name) - set(frame.columns))
            )
    if str(manifest.get("status")) != "QUALIFIED":
        blockers.append("source_manifest_not_qualified")
    manifest_sources = manifest.get("sources") or {}
    if evidence_origin not in {"SYNTHETIC", "HISTORICAL_REAL"}:
        blockers.append("source_manifest_evidence_origin_invalid")
    if not str(manifest.get("schema_semantic_version") or ""):
        blockers.append("source_manifest_schema_semantic_version_missing")
    if not str(manifest.get("field_definition_hash") or ""):
        blockers.append("source_manifest_field_definition_hash_missing")
    if strict_contract and str(manifest.get("field_definition_hash") or "") != get_contract_sha256():
        blockers.append("field_definition_hash_mismatch_with_canonical_contract")
    # --- Fixture mode: forbidden for HISTORICAL_REAL, allowed for SYNTHETIC (S3 only) ---
    if fixture_mode and evidence_origin == "HISTORICAL_REAL":
        blockers.append("fixture_mode_forbidden_for_historical_real")
    # --- Adapter report binding (required for HISTORICAL_REAL) ---
    if evidence_origin == "HISTORICAL_REAL" and not fixture_mode:
        if adapter_report_path is None or not adapter_report_path.exists():
            blockers.append("adapter_report_required_for_historical_real")
        else:
            adapter = json.loads(adapter_report_path.read_text(encoding="utf-8"))
            # Verify Adapter report self-hash
            adapter_raw = {k: v for k, v in adapter.items() if k != "content_sha256"}
            if str(adapter.get("content_sha256") or "") != canonical_sha(adapter_raw):
                blockers.append("adapter_report_content_sha256_invalid")
            if str(adapter.get("status")) != "PASS":
                blockers.append("adapter_report_not_pass")
            if adapter.get("adapter_ready") is not True:
                blockers.append("adapter_not_ready")
            if str(adapter.get("historical_evidence_level")) != "E1":
                blockers.append("adapter_not_historical_e1")
            # Manifest path + SHA binding
            actual_manifest_sha = _file_sha(source_manifest_path)
            if str(adapter.get("manifest_sha256") or "") != actual_manifest_sha:
                blockers.append("adapter_manifest_sha_mismatch")
            if str(adapter.get("manifest_path") or "") != str(source_manifest_path):
                blockers.append("adapter_manifest_path_mismatch")
            # Config SHA chain — mandatory for HISTORICAL_REAL
            adapter_config_path = adapter.get("config_path")
            if not adapter_config_path or not Path(str(adapter_config_path)).exists():
                blockers.append("adapter_config_path_missing_or_not_found")
                blockers.append("adapter_config_path_missing")
            adapter_config_sha = str(adapter.get("config_sha256") or "")
            if not adapter_config_sha or len(adapter_config_sha) != 64:
                blockers.append("adapter_config_sha256_missing_or_invalid")
            manifest_config_sha = str(manifest.get("adapter_config_sha256") or "")
            if not manifest_config_sha:
                blockers.append("manifest_adapter_config_sha256_missing")
            if adapter_config_path and Path(str(adapter_config_path)).exists():
                actual_cfg_sha = _file_sha(Path(str(adapter_config_path)))
                if adapter_config_sha != actual_cfg_sha:
                    blockers.append("adapter_config_sha_mismatch_actual_file")
            if adapter_config_sha and manifest_config_sha and adapter_config_sha != manifest_config_sha:
                blockers.append("adapter_config_sha256_mismatch")
            if str(adapter.get("evidence_origin") or "") != evidence_origin:
                blockers.append("adapter_evidence_origin_mismatch")
            if adapter.get("blockers") and len(adapter["blockers"]) > 0:
                blockers.append("adapter_report_has_blockers")
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
    file_map.update({name: path for name, path in optional_paths.items() if path is not None})
    source_sha = {name: _file_sha(path) for name, path in file_map.items()}
    for name, sha in source_sha.items():
        declared = str(
            (manifest_sources.get(name) or {}).get("content_sha256")
            or (manifest_sources.get(name) or {}).get("sha256")
            or ""
        )
        if declared != sha:
            blockers.append(f"source_manifest_sha_mismatch:{name}")
    for name, frame in frames.items():
        business_column = "cal_date" if name == "trade_calendar" else "trade_date"
        if business_column not in frame.columns:
            blockers.append(f"invalid_key:{name}")
            continue
        frame["trade_date"] = pd.to_datetime(
            frame[business_column].astype(str), errors="coerce"
        ).dt.normalize()
        if business_column != "trade_date":
            frame["cal_date"] = frame["trade_date"]
        duplicate_key = get_primary_key(name)
        if duplicate_key:
            key_columns_present = all(column in frame.columns for column in duplicate_key)
            if key_columns_present and (
                strict_contract or (not legacy_fixture and evidence_origin != "SYNTHETIC")
            ):
                duplicate_count = int(frame.duplicated(duplicate_key).sum())
                if duplicate_count:
                    blockers.append(f"duplicate_key:{name}:{duplicate_count}")
            elif not key_columns_present and strict_contract:
                blockers.append(f"primary_key_columns_missing:{name}")
        if name != "trade_calendar":
            frame["symbol"] = (
                frame.get("symbol", pd.Series(index=frame.index, dtype="object"))
                .astype(str)
                .str.extract(r"(\d+)", expand=False)
                .str.zfill(6)
                .str[-6:]
            )
        invalid_business_time = frame["trade_date"].isna().any()
        invalid_symbol = (
            name != "trade_calendar" and frame["symbol"].isna().any()
        )
        if invalid_business_time or invalid_symbol:
            blockers.append(f"invalid_key:{name}")
    # Continue through semantic checks even when an upstream identity/schema
    # blocker exists.  This produces actionable diagnostics (availability,
    # history, regime, and revision-chain failures) without ever qualifying a
    # blocked package.
    panel = frames["universe"].merge(
        frames["market"], on=["trade_date", "symbol"], how="left"
    )
    # Make the canonical signal timestamp available to the as-of joins before
    # the rest of the derived panel columns are built.
    panel["signal_time"] = signal_time_for_trade_dates(panel["trade_date"])
    if strict_contract:
        # Canonical financial and industry snapshots are revision/SCD
        # dimensions.  Join them as-of instead of pretending each row is a
        # daily fact keyed by `(trade_date, symbol)`.
        try:
            financial_columns = [
                column
                for column in frames["financial"].columns
                if column not in {"trade_date", "symbol"}
            ]
            panel = _asof_enrich(
                panel,
                frames["financial"],
                source_time="financial_available_at",
                target_time="signal_time",
                source_columns=financial_columns,
            )
            industry_columns = [
                column
                for column in frames["industry"].columns
                if column not in {"trade_date", "symbol"}
            ]
            # Industry membership is selected by the SCD validity interval;
            # its own availability timestamp is checked by the same helper.
            panel = _asof_enrich(
                panel,
                frames["industry"],
                source_time="industry_available_at",
                target_time="trade_date",
                source_columns=industry_columns,
                validity_end="valid_to",
                signal_time_column="signal_time",
                source_join_time="valid_from",
            )
        except Exception as exc:
            blockers.append(f"point_in_time_join_failed:{type(exc).__name__}:{exc}")
    else:
        panel = panel.merge(
            frames["financial"], on=["trade_date", "symbol"], how="left"
        )
        panel = panel.merge(
            frames["industry"], on=["trade_date", "symbol"], how="left"
        )
    panel = panel.merge(
        frames["adjustment"], on=["trade_date", "symbol"], how="left"
    )
    # Bind the remaining canonical snapshots when supplied by the formal
    # pipeline.  Corporate actions can contain multiple events per day; keep
    # one deterministic diagnostic row so the factor panel does not fan out.
    for name in ("security_lifecycle",):
        if name not in frames:
            continue
        extra = frames[name].copy()
        extra_cols = [
            c for c in extra.columns
            if c not in {"trade_date", "symbol"} and c not in set(panel.columns)
        ]
        if extra_cols:
            panel = panel.merge(
                extra[["trade_date", "symbol", *extra_cols]],
                on=["trade_date", "symbol"], how="left",
            )
    # Keep diagnostics actionable when a legacy/historical fixture is missing
    # a newly canonicalized column.  The placeholders are NaN/empty only for
    # diagnostic calculations; the accumulated schema blockers still prevent
    # qualification and no formal panel is written.
    for column in (
        "is_listed",
        "is_st",
        "is_suspended",
        "limit_status",
        "industry",
        "pb",
        "adj_factor",
        "corporate_action_type",
        "ex_date",
        "record_date",
        "adjustment_factor_version",
        "financial_period_end",
        "announcement_date",
        "revision_id",
        "revision_sequence",
        "financial_source_snapshot_sha",
        "market_regime",
        "security_status_transition",
    ):
        if column not in panel.columns:
            panel[column] = "" if column == "limit_status" else np.nan
    availability_columns = [
        get_available_at_column(name)
        for name in ("market", "financial", "universe", "industry", "adjustment")
    ]
    for name in ("security_lifecycle",):
        if name in frames:
            availability_columns.append(get_available_at_column(name))
    for column in availability_columns:
        if column not in panel.columns:
            blockers.append(f"missing_available_at:{column}:0")
            panel[column] = pd.NaT
    # Event/calendar families are validated on their own rows.  They are not
    # daily stock facts, so requiring an event timestamp on every panel row
    # would incorrectly reject dates without a corporate action.
    for name in ("trade_calendar", "corporate_actions"):
        if name not in frames:
            continue
        source = frames[name]
        available_column = get_available_at_column(name)
        business_column = "cal_date" if name == "trade_calendar" else "trade_date"
        if available_column not in source.columns:
            blockers.append(f"missing_available_at:{available_column}")
            continue
        timezone_offenders = validate_explicit_timezone(source[available_column])
        if timezone_offenders:
            blockers.append(f"available_at_no_timezone:{available_column}")
        available = pd.to_datetime(source[available_column], errors="coerce", utc=True)
        signal = signal_time_for_trade_dates(source[business_column])
        if available.isna().any():
            blockers.append(f"invalid_or_timezone_missing:{available_column}")
        if (available > signal).any():
            blockers.append(f"available_after_signal:{available_column}")
    # v5.3: the coverage/eligibility facts are computed BEFORE the per-row
    # availability gates so those gates can target the rows the panel
    # actually delivers (PIT-complete core x eligible universe) instead of
    # every raw row of the merge.
    listed = panel.get(
        "is_listed", panel.get("is_tradable", pd.Series(False, index=panel.index))
    ).fillna(False).astype(bool)
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
    # v5.3: PIT-complete core semantics.  The raw PIT sources ramp in over a
    # REAL loading window (verified 2026-08-03: dws_fina_pit_daily coverage
    # climbs 0.4% -> 95% during 2020-01..2020-04 before the panel core; 72
    # brand-new 2026 IPOs have no financial rows yet).  Every date is still
    # reported in the coverage CSV (honest diagnostics), but a family only
    # BLOCKS when it dips below the threshold ON OR AFTER the first date all
    # families reached it — a mid-core gap is a genuine data break, a
    # loading ramp is documented pre-history.
    coverage_ready_date: str | None = None
    if daily_coverage.empty:
        blockers.append("daily_coverage_below_threshold:no_rows")
    else:
        coverage_matrix = pd.DataFrame(
            {name: daily_coverage[f"{name}_coverage"] for name in required_coverage}
        )
        ready_rows = coverage_matrix.ge(min_coverage).all(axis=1)
        ready_dates = daily_coverage.loc[ready_rows, "trade_date"]
        if ready_dates.empty:
            worst = coverage_matrix.min().idxmin()
            blockers.append(f"daily_coverage_below_threshold:{worst}")
        else:
            coverage_ready_date = str(ready_dates.iloc[0])
            core_window = daily_coverage.loc[
                daily_coverage["trade_date"] >= coverage_ready_date
            ]
            for name in required_coverage:
                if float(core_window[f"{name}_coverage"].min()) < min_coverage:
                    blockers.append(f"daily_coverage_below_threshold:{name}")
    eligible = (
        listed
        & ~panel["is_st"].fillna(True).astype(bool)
        & ~panel["is_suspended"].fillna(True).astype(bool)
        & panel["limit_status"].fillna("").isin({"NORMAL", "NONE"})
    )
    panel["eligible_universe"] = eligible
    delivered = eligible
    if evidence_origin == "HISTORICAL_REAL" and coverage_ready_date is not None:
        delivered = eligible & (
            panel["trade_date"] >= pd.to_datetime(coverage_ready_date)
        )
    for column in availability_columns:
        timezone_offenders = validate_explicit_timezone(panel[column])
        if timezone_offenders:
            blockers.append(f"available_at_no_timezone:{column}")
        raw = panel[column]
        parsed = pd.to_datetime(raw, errors="coerce", utc=True)
        panel[column] = parsed
        # Format violations on ANY row are a provider contract breach —
        # a non-empty value that cannot be parsed is always flagged.
        invalid = raw.notna() & parsed.isna()
        if invalid.any():
            blockers.append(f"invalid_or_timezone_missing:{column}")
        # v5.3: missing availability is rate-gated on the delivered rows
        # (PIT-complete core x eligible).  Pre-ramp history and ineligible
        # rows (ST/suspended/limit) are reported in the coverage CSV but do
        # not gate — their absence is honest data reality (e.g. brand-new
        # IPOs before their first statement), and the daily coverage gate
        # already enforces the profile threshold per day on listed rows.
        missing = parsed.isna() & delivered
        if delivered.any() and float(missing.mean()) > (1.0 - min_coverage):
            blockers.append(f"missing_available_at:{column}:{int(missing.sum())}")
    signal_time = signal_time_for_trade_dates(panel["trade_date"])
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
        # announcement_date <= financial_available_at (strip tz for comparison)
        fin_sub2 = panel[["announcement_date", "financial_available_at"]].copy()
        fin_sub2["_ann_ts"] = pd.to_datetime(
            fin_sub2["announcement_date"].astype(str), errors="coerce")
        if fin_sub2["_ann_ts"].isna().any() and evidence_origin == "HISTORICAL_REAL":
            # v5.3: same rate gate as missing_available_at — unparseable
            # announcement dates are the as-of absence rows (pre-ramp /
            # ineligible / no-statement-yet), all honest and excluded from
            # the delivered panel; only a rate above the profile tolerance
            # on the delivered rows blocks.
            fin_missing = fin_sub2["_ann_ts"].isna() & delivered
            if delivered.any() and float(fin_missing.mean()) > (1.0 - min_coverage):
                n_bad = int(fin_missing.sum())
                blockers.append(f"financial_announcement_date_unparseable:{n_bad}")
        fin_sub2 = fin_sub2.dropna(subset=["_ann_ts", "financial_available_at"])
        if not fin_sub2.empty:
            fin_avail_naive = fin_sub2["financial_available_at"].dt.tz_localize(None)
            n_bad = int((fin_sub2["_ann_ts"] > fin_avail_naive).sum())
            if n_bad > 0:
                blockers.append(f"financial_announcement_after_availability:{n_bad}")
        # PB non-negative
        if (panel["pb"].dropna() < 0).any():
            blockers.append("pb_negative_values_detected")
    # v5.3: returns use the RAW price regime (raw_close/raw_pre_close —
    # both from ods_daily).  The old close/pre_close pair mixed an adjusted
    # close with a raw pre_close, producing a fake jump on every dividend
    # ex-date.  The release now carries raw OHLC explicitly.
    if "raw_close" in panel.columns and "raw_pre_close" in panel.columns:
        panel["ret_1d"] = (
            pd.to_numeric(panel["raw_close"], errors="coerce")
            / pd.to_numeric(panel["raw_pre_close"], errors="coerce")
            - 1.0
        )
    else:
        panel["ret_1d"] = (
            pd.to_numeric(panel["close"], errors="coerce")
            / pd.to_numeric(panel["pre_close"], errors="coerce")
            - 1.0
        )
    panel = panel.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    # v5.3: adj_close IS the extractor's adjusted close (dwd_stock_daily_
    # standard adj_close).  The old multiplication by adj_factor double-
    # adjusted the series (adjusted x factor = raw x factor^2), distorting
    # momentum around ex-dates.
    panel["adj_close"] = pd.to_numeric(panel["close"], errors="coerce")
    panel["momentum_raw"] = panel.groupby("symbol")["adj_close"].pct_change(20)
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
        # Keep the raw Amihud liquidity direction intact here.  The formal
        # strategy definition owns the economic sign (liquidity=-1); applying
        # a reverse rank in the builder and then -1 in Score would invert it
        # twice.
        "liquidity": ("liquidity_raw", False),
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
    # Bind each derived factor to the family-specific PIT timestamp that was
    # actually available when the signal was formed.  The challenger lab
    # consumes these columns instead of inferring availability from a generic
    # source timestamp.
    factor_availability_sources = {
        "market_beta": ["market_available_at"],
        "size": ["market_available_at"],
        "volatility": ["market_available_at"],
        "liquidity": ["market_available_at"],
        "momentum": ["market_available_at", "adjustment_available_at"],
        "value": ["financial_available_at"],
        # These two factors are supplied by the canonical industry/market
        # state snapshots.  Keep their own availability columns even when a
        # downstream study later marks the factor as research-disabled.
        "industry": ["industry_available_at"],
        "market_regime": ["market_available_at"],
    }
    for factor, source_columns in factor_availability_sources.items():
        available = panel[source_columns[0]]
        for source_column in source_columns[1:]:
            available = pd.DataFrame({"left": available, "right": panel[source_column]}).max(axis=1)
        panel[f"{factor}_available_at"] = available
    dates = sorted(panel["trade_date"].dropna().unique())
    warmup_dates = set(dates[:20])
    qualified = panel.loc[
        panel["eligible_universe"] & ~panel["trade_date"].isin(warmup_dates)
    ].copy()
    if evidence_origin == "HISTORICAL_REAL" and coverage_ready_date is not None:
        # v5.3: the panel core = the PIT-complete window.  Pre-ramp dates are
        # honest history (reported in the coverage CSV), not factor rows.
        qualified = qualified.loc[
            qualified["trade_date"] >= pd.to_datetime(coverage_ready_date)
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
    # Core start enforcement (HISTORICAL_REAL only)
    required_core_start = str(profile.get("core_period", {}).get("min_start_date", "2018-01-01"))
    # v5.3: date-only comparison — str(Timestamp) carries " 00:00:00" and
    # compares after the date lexicographically (a false blocker even for a
    # valid start).
    sample_start_str = (
        str(pd.Timestamp(qualified["trade_date"].min()).date())
        if not qualified.empty else None
    )
    if evidence_origin == "HISTORICAL_REAL" and sample_start_str and sample_start_str > required_core_start:
        blockers.append(f"core_start_after_required:{sample_start_str}>{required_core_start}")
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
        # Financial revision chain
        rev_ids = qualified["revision_id"].dropna()
        if rev_ids.empty or rev_ids.nunique() <= 1:
            blockers.append("financial_revision_chain_missing_or_constant")
        fin_sha = qualified["financial_source_snapshot_sha"].dropna()
        if not fin_sha.empty:
            invalid_sha = fin_sha.apply(
                lambda x: len(str(x)) != 64 or not all(c in "0123456789abcdef" for c in str(x).lower())
            )
            if invalid_sha.any():
                blockers.append(f"financial_source_sha_invalid:{int(invalid_sha.sum())}")
        # adj_factor must be positive and finite
        adj = pd.to_numeric(qualified["adj_factor"], errors="coerce").dropna()
        if not adj.empty:
            if (adj <= 0).any():
                blockers.append(f"adj_factor_non_positive:{int((adj<=0).sum())}")
            if (~np.isfinite(adj)).any():
                blockers.append("adj_factor_non_finite")
        # Industry SCD: detect is_current=1 backfill (all industry_available_at identical)
        ind_avail = qualified["industry_available_at"].dropna()
        if not ind_avail.empty and ind_avail.nunique() <= 1:
            blockers.append("industry_scd_suspected_current_backfill")
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
    # Remove stale PASS artifacts from prior runs (NOT pit_source_manifest.json — Adapter's domain)
    for stale_name in ["factor_panel_daily.parquet"]:
        stale_path = output_dir / stale_name
        if stale_path.exists():
            stale_path.unlink()
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
        "revision_sequence",
        "financial_source_snapshot_sha",
        "market_regime",
        *raw_factors,
        *(f"{factor}_available_at" for factor in factor_availability_sources),
        *availability_columns,
    ]
    panel_columns = list(dict.fromkeys(
        column for column in panel_columns if column in qualified.columns
    ))
    qualified["trade_date"] = qualified["trade_date"].dt.date.astype(str)
    for column in [
        "signal_time",
        *availability_columns,
        *(f"{factor}_available_at" for factor in raw_factors),
    ]:
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
        "coverage_ready_date": coverage_ready_date,
        "unique_dates": unique_dates,
        "symbols": int(qualified["symbol"].nunique()),
        "rows": int(len(qualified)),
        "minimum_daily_universe_coverage": min_coverage,
        "target_trading_days": target_days,
        "target_trading_days_met": target_met,
        "core_start_required": str(profile.get("core_period", {}).get("min_start_date", "2018-01-01")),
        "core_start_met": (
            str(pd.Timestamp(qualified["trade_date"].min()).date()) <= str(profile.get("core_period", {}).get("min_start_date", "2018-01-01"))
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
        "execution_mode": "TEST_FIXTURE" if fixture_mode else "PRODUCTION",
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
    parser.add_argument("--trade-calendar", type=Path)
    parser.add_argument("--security-lifecycle", type=Path)
    parser.add_argument("--corporate-actions", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--adapter-report", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", default="alpha_v3_2")
    parser.add_argument("--fixture-mode", action="store_true", default=False)
    args = parser.parse_args()
    result = build_pit_factor_panel(
        market_path=args.market,
        universe_path=args.universe,
        financial_path=args.financial,
        industry_path=args.industry,
        adjustment_path=args.adjustment,
        trade_calendar_path=args.trade_calendar,
        security_lifecycle_path=args.security_lifecycle,
        corporate_actions_path=args.corporate_actions,
        source_manifest_path=args.source_manifest,
        output_dir=args.output_dir,
        profile_name=args.profile,
        adapter_report_path=args.adapter_report,
        fixture_mode=args.fixture_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
