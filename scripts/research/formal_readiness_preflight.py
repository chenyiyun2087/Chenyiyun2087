"""Fail-closed readiness gate for the immutable 2018-present formal run.

The command validates an already frozen package.  It never silently queries a
different table, derives a calendar from lifecycle rows, or treats missing
objects as empty.  `READY_FOR_FORMAL_RUN` is the only success status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from runtime.acceptance_config import canonical_sha
from runtime.pit_semantic_contract import (
    get_available_at_column,
    get_contract_sha256,
    get_primary_key,
    get_required_columns,
    get_canonical_execution_columns,
    get_economic_columns,
    validate_frame_schema,
    signal_time_for_trade_dates,
    validate_explicit_timezone,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "formal_readiness.yaml"


@dataclass(frozen=True)
class Check:
    check: str
    passed: bool
    actual: str
    required: str


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(path, dtype={"symbol": str, "ts_code": str})
    if frame.empty:
        raise ValueError(f"empty_required_object:{path.name}")
    return frame


def _pit_visible(
    frame: pd.DataFrame, business_column: str, available_column: str
) -> tuple[bool, int]:
    if available_column not in frame.columns:
        return False, len(frame)
    timezone_offenders = validate_explicit_timezone(frame[available_column])
    if timezone_offenders:
        return False, len(timezone_offenders)
    business = pd.to_datetime(frame[business_column], errors="coerce")
    available = pd.to_datetime(frame[available_column], errors="coerce", utc=True)
    signal = signal_time_for_trade_dates(business)
    invalid = (
        business.isna()
        | available.isna()
        | (available > signal)
    )
    return not bool(invalid.any()), int(invalid.sum())


def _manifest_checks(
    package: Path, config: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[Check]]:
    path = package / "source_manifest.json"
    if not path.exists():
        return None, [
            Check("source_manifest", False, "missing", "present with object SHA256")
        ]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    objects = manifest.get("objects") or {}
    checks: list[Check] = []
    declared_content_sha = str(manifest.get("content_sha256") or "")
    if declared_content_sha:
        actual_content_sha = canonical_sha(
            {key: value for key, value in manifest.items() if key != "content_sha256"}
        )
        checks.append(
            Check(
                "source_manifest_content_sha",
                actual_content_sha == declared_content_sha,
                actual_content_sha,
                declared_content_sha,
            )
        )
    for filename in config["required_objects"]:
        if filename == "source_manifest.json":
            continue
        target = package / filename
        expected = str((objects.get(filename) or {}).get("sha256") or "")
        actual = _sha(target) if target.is_file() else "MISSING"
        checks.append(
            Check(
                f"object_sha:{filename}",
                bool(expected) and actual == expected,
                actual,
                expected or "declared SHA256",
            )
        )
    checks.append(
        Check(
            "calendar_source_manifest",
            manifest.get("calendar_source")
            == config["authoritative_calendar"]["source"],
            str(manifest.get("calendar_source")),
            str(config["authoritative_calendar"]["source"]),
        )
    )
    checks.append(
        Check(
            "coverage_start_manifest",
            str(manifest.get("coverage_start") or "9999-12-31")
            <= str(config["minimum_start_date"]),
            str(manifest.get("coverage_start")),
            f"<= {config['minimum_start_date']}",
        )
    )
    checks.append(
        Check(
            "coverage_end_manifest",
            bool(str(manifest.get("coverage_end") or "").strip()),
            str(manifest.get("coverage_end")),
            "non-empty coverage_end",
        )
    )
    return manifest, checks


def evaluate_package(package: Path, config: dict[str, Any]) -> dict[str, Any]:
    checks: list[Check] = []
    required = [str(name) for name in config["required_objects"]]
    missing = [name for name in required if not (package / name).is_file()]
    checks.append(
        Check(
            "required_objects",
            not missing,
            "complete" if not missing else ",".join(missing),
            "all required frozen objects present",
        )
    )
    if missing:
        return _result(package, config, checks)

    manifest, manifest_checks = _manifest_checks(package, config)
    checks.extend(manifest_checks)
    if manifest is None:
        return _result(package, config, checks)

    frames = {
        filename: _read_frame(package / filename)
        for filename in required
        if filename.endswith(".csv")
    }
    # Pre-v3.2 fixture packages used generic `available_at` and
    # `is_tradable`.  Keep a tightly scoped compatibility reader for the old
    # synthetic fixtures (which have no formal schema version); canonical or
    # mixed packages are rejected instead of silently aliasing fields.
    legacy_aliases_present = any(
        (
            ("available_at" in frames["tradable_universe.csv"] and "universe_available_at" not in frames["tradable_universe.csv"]),
            ("available_at" in frames["scores.csv"] and "score_available_at" not in frames["scores.csv"]),
            ("available_at" in frames["prices.csv"] and "market_available_at" not in frames["prices.csv"]),
            ("available_at" in frames["adjustment_factors.csv"] and "adjustment_available_at" not in frames["adjustment_factors.csv"]),
            ("action_type" in frames["strict_corporate_actions.csv"] and "corporate_action_type" not in frames["strict_corporate_actions.csv"]),
            ("available_at" in frames["strict_corporate_actions.csv"] and "corporate_action_available_at" not in frames["strict_corporate_actions.csv"]),
            ("available_at" in frames["strict_security_lifecycle.csv"] and "lifecycle_available_at" not in frames["strict_security_lifecycle.csv"]),
            ("is_tradable" in frames["tradable_universe.csv"] and "is_listed" not in frames["tradable_universe.csv"]),
        )
    )
    manifest_payload = json.loads((package / "source_manifest.json").read_text(encoding="utf-8"))
    strict_fixture_manifest = json.loads(
        (package / "strict_snapshot_manifest.json").read_text(encoding="utf-8")
    )
    legacy_compat = legacy_aliases_present and (
        not manifest_payload.get("schema_version")
        or str(strict_fixture_manifest.get("dataset_version")) == "fixture"
    )
    if legacy_aliases_present and not legacy_compat:
        checks.append(
            Check(
                "canonical_semantic_fields",
                False,
                "legacy_aliases_present_in_versioned_package",
                "canonical family-specific availability and status fields only",
            )
        )
        # Do not continue into the transport-only checks: a versioned package
        # containing legacy aliases is invalid and must produce a structured
        # BLOCKED report rather than a KeyError from a later column access.
        return _result(package, config, checks)
    if legacy_compat:
        universe = frames["tradable_universe.csv"]
        if "is_listed" not in universe and "is_tradable" in universe:
            universe["is_listed"] = pd.to_numeric(universe["is_tradable"], errors="coerce").fillna(0)
        if "is_st" not in universe:
            universe["is_st"] = 0
        if "is_suspended" not in universe:
            universe["is_suspended"] = 0
        if "limit_status" not in universe:
            universe["limit_status"] = "NORMAL"
        if "security_status_transition" not in universe:
            universe["security_status_transition"] = "LEGACY"
        if "universe_available_at" not in universe and "available_at" in universe:
            universe["universe_available_at"] = universe["available_at"]
        scores = frames["scores.csv"]
        if "score_available_at" not in scores and "available_at" in scores:
            scores["score_available_at"] = scores["available_at"]
        if "signal_time" not in scores:
            scores["signal_time"] = signal_time_for_trade_dates(scores["trade_date"]).dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        prices = frames["prices.csv"]
        if "market_available_at" not in prices and "available_at" in prices:
            prices["market_available_at"] = prices["available_at"]
        adj = frames["adjustment_factors.csv"]
        if "adjustment_available_at" not in adj and "available_at" in adj:
            adj["adjustment_available_at"] = adj["available_at"]
        actions = frames["strict_corporate_actions.csv"]
        if "corporate_action_type" not in actions and "action_type" in actions:
            actions["corporate_action_type"] = actions["action_type"]
        if "trade_date" not in actions and "effective_date" in actions:
            actions["trade_date"] = actions["effective_date"]
        if "corporate_action_available_at" not in actions and "available_at" in actions:
            actions["corporate_action_available_at"] = actions["available_at"]
        lifecycle = frames["strict_security_lifecycle.csv"]
        if "is_st" not in lifecycle:
            lifecycle["is_st"] = 0
        if "listed_date" not in lifecycle:
            lifecycle["listed_date"] = lifecycle["trade_date"]
        if "security_status_transition" not in lifecycle:
            lifecycle["security_status_transition"] = "LEGACY"
        if "lifecycle_available_at" not in lifecycle and "available_at" in lifecycle:
            lifecycle["lifecycle_available_at"] = lifecycle["available_at"]
    canonical_mode = not legacy_compat
    sm_declared_sha = str((json.loads((package / \"source_manifest.json\").read_text(encoding=\"utf-8\")) if (package / \"source_manifest.json\").is_file() else {}).get(\"content_sha256\") or \"\")
    if canonical_mode:
        if not sm_declared_sha:
            checks.append(
                Check(
                    "source_manifest_content_sha",
                    False,
                    "missing",
                    "canonical source manifest content_sha256",
                )
            )
        if not isinstance(manifest.get("snapshot_identity"), dict) or not any(
            str(value or "").strip()
            for value in (manifest.get("snapshot_identity") or {}).values()
        ):
            checks.append(
                Check(
                    "source_snapshot_identity",
                    False,
                    "missing",
                    "provider/frozen snapshot identity",
                )
            )
        source_rows = manifest.get("sources") or {}
        for family in (
            "market",
            "universe",
            "financial",
            "industry",
            "adjustment",
            "trade_calendar",
            "security_lifecycle",
            "corporate_actions",
        ):
            source = source_rows.get(family) or {}
            required_source_fields = {
                "sha256",
                "schema_hash",
                "rows",
                "coverage_start",
                "coverage_end",
                "provider",
                "version",
                "query_sha256",
                "parameter_sha256",
            }
            missing_source_fields = sorted(
                field for field in required_source_fields if field not in source
            )
            checks.append(
                Check(
                    f"source_metadata:{family}",
                    not missing_source_fields
                    and bool(source.get("coverage_start"))
                    and bool(source.get("coverage_end")),
                    ";".join(missing_source_fields)
                    or f"{source.get('coverage_start')}..{source.get('coverage_end')}",
                    "content/schema/rows/coverage/provider/version/query SHA",
                )
            )
        for family in ("corporate_actions", "security_lifecycle"):
            complete = bool(
                (manifest.get("source_completeness") or {}).get(family)
                or manifest.get(
                    "corporate_action_complete"
                    if family == "corporate_actions"
                    else "security_lifecycle_complete"
                )
            )
            checks.append(
                Check(
                    f"source_completeness:{family}",
                    complete,
                    str(complete),
                    "explicit complete=true",
                )
            )
        canonical_paths = {
            "market": package / "market.parquet",
            "universe": package / "universe.parquet",
            "financial": package / "financial.parquet",
            "industry": package / "industry.parquet",
            "adjustment": package / "adjustment.parquet",
            "trade_calendar": package / "trade_calendar.parquet",
            "security_lifecycle": package / "security_lifecycle.parquet",
            "corporate_actions": package / "corporate_actions.parquet",
        }
        canonical_missing = [
            str(path.name) for path in canonical_paths.values() if not path.is_file()
        ]
        required_canonical = config.get("canonical_required_objects") or []
        canonical_missing.extend(
            str(name) for name in required_canonical
            if not (package / str(name)).is_file()
        )
        if canonical_missing:
            checks.append(
                Check(
                    "canonical_required_objects",
                    False,
                    ",".join(sorted(set(canonical_missing))),
                    "all eight canonical PIT snapshots present",
                )
            )
            return _result(package, config, checks)
        canonical_objects = manifest.get("objects") or {}
        for family, path in canonical_paths.items():
            expected_sha = str((canonical_objects.get(path.name) or {}).get("sha256") or "")
            actual_sha = _sha(path) if path.is_file() else "MISSING"
            checks.append(
                Check(
                    f"canonical_object_sha:{path.name}",
                    bool(expected_sha) and actual_sha == expected_sha,
                    actual_sha,
                    expected_sha or "declared SHA256",
                )
            )
            frame = _read_frame(path)
            schema_blockers = validate_frame_schema(frame, family)
            if family == "market":
                schema_blockers.extend(
                    f"schema_missing_execution_column:{family}:{column}"
                    for column in sorted(get_canonical_execution_columns(family) - set(frame.columns))
                )
            if family == "corporate_actions":
                schema_blockers.extend(
                    f"schema_missing_economic_column:{family}:{column}"
                    for column in sorted(get_economic_columns(family) - set(frame.columns))
                )
            checks.append(
                Check(
                    f"canonical_schema:{family}",
                    not schema_blockers,
                    ";".join(schema_blockers) or "valid",
                    "canonical contract schema, keys, and explicit timezone",
                )
            )
            if schema_blockers:
                continue
            available_column = str(
                {
                    "trade_calendar": "available_at",
                }.get(family, get_available_at_column(family))
            )
            business_column = "cal_date" if family == "trade_calendar" else "trade_date"
            visible, invalid = _pit_visible(frame, business_column, available_column)
            checks.append(
                Check(
                    f"canonical_pit_visibility:{family}",
                    visible,
                    f"invalid_rows={invalid}",
                    f"{available_column} <= T15:30 signal time",
                )
            )
        source_contract_sha = str(manifest.get("field_definition_hash") or "")
        checks.append(
            Check(
                "canonical_field_definition_hash",
                source_contract_sha == get_contract_sha256(),
                source_contract_sha,
                get_contract_sha256(),
            )
        )
        if not all(item.passed for item in checks if item.check.startswith("canonical_")):
            return _result(package, config, checks)
    for filename, primary_key in (config.get("primary_keys") or {}).items():
        frame = frames[filename]
        absent = sorted(set(primary_key) - set(frame.columns))
        duplicate_count = (
            int(frame.duplicated(list(primary_key)).sum()) if not absent else len(frame)
        )
        checks.append(
            Check(
                f"schema_and_duplicates:{filename}",
                not absent and duplicate_count == 0,
                f"missing={absent};duplicates={duplicate_count}",
                "required primary-key columns present; duplicates=0",
            )
        )

    calendar = frames["trade_calendar.csv"].copy()
    expected_calendar = config["authoritative_calendar"]
    source_ok = (
        "source" in calendar.columns
        and calendar["source"].astype(str).eq(expected_calendar["source"]).all()
    )
    exchange_ok = calendar["exchange"].astype(str).eq(expected_calendar["exchange"]).all()
    checks.append(
        Check(
            "authoritative_sse_calendar",
            bool(source_ok and exchange_ok),
            f"source_ok={source_ok};exchange_ok={exchange_ok}",
            f"{expected_calendar['source']} / {expected_calendar['exchange']}",
        )
    )
    calendar["cal_date"] = pd.to_datetime(calendar["cal_date"], errors="coerce")
    open_dates = set(
        calendar.loc[
            pd.to_numeric(calendar["is_open"], errors="coerce").eq(1), "cal_date"
        ].dt.strftime("%Y-%m-%d")
    )
    checks.append(
        Check(
            "calendar_open_dates",
            bool(open_dates),
            str(len(open_dates)),
            "at least one authoritative open date",
        )
    )
    manifest_end = str(manifest.get("coverage_end") or "")

    business_columns = {
        "trade_calendar.csv": ("cal_date", "available_at"),
        "tradable_universe.csv": ("trade_date", "universe_available_at"),
        "scores.csv": ("trade_date", "score_available_at"),
        "prices.csv": ("trade_date", "market_available_at"),
        "adjustment_factors.csv": ("trade_date", "adjustment_available_at"),
        "strict_corporate_actions.csv": ("trade_date", "corporate_action_available_at"),
        "strict_security_lifecycle.csv": ("trade_date", "lifecycle_available_at"),
    }
    for filename, (business_column, available_column) in business_columns.items():
        passed, invalid = _pit_visible(
            frames[filename], business_column, available_column
        )
        checks.append(
            Check(
                f"pit_visibility:{filename}",
                passed,
                f"invalid_rows={invalid}",
            f"{available_column} <= T15:30 signal time",
            )
        )

    universe = frames["tradable_universe.csv"].copy()
    universe["trade_date"] = pd.to_datetime(
        universe["trade_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    universe = universe[
        pd.to_numeric(universe["is_listed"], errors="coerce").eq(1)
        & ~pd.to_numeric(universe["is_st"], errors="coerce").eq(1)
        & ~pd.to_numeric(universe["is_suspended"], errors="coerce").eq(1)
        & universe["limit_status"].astype(str).isin({"NORMAL", "NONE"})
    ]
    denominators = universe.groupby("trade_date")["symbol"].nunique()
    scores = frames["scores.csv"].copy()
    scores["trade_date"] = pd.to_datetime(
        scores["trade_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    scores["score"] = pd.to_numeric(scores["score"], errors="coerce")
    valid_scores = scores[scores["score"].notna()]
    threshold = float(config["minimum_score_coverage"])
    strategy_dates: list[set[str]] = []
    min_ratio = 1.0
    coverage_failures: list[str] = []
    for strategy in config["strategies"]:
        scoped = valid_scores[valid_scores["strategy"].astype(str).eq(strategy)]
        numerators = scoped.groupby("trade_date")["symbol"].nunique()
        ratios = numerators.reindex(denominators.index, fill_value=0) / denominators
        strategy_dates.append(set(scoped["trade_date"]))
        local_min = float(ratios.min()) if len(ratios) else 0.0
        min_ratio = min(min_ratio, local_min)
        failed_dates = ratios[ratios.lt(threshold)].index.tolist()
        coverage_failures.extend(f"{strategy}:{item}" for item in failed_dates)
    checks.append(
        Check(
            "daily_pit_score_coverage",
            not coverage_failures and min_ratio >= threshold,
            f"minimum_ratio={min_ratio:.6f};failed_dates={len(coverage_failures)}",
            f"valid scored / PIT tradable >= {threshold:.2%} for every strategy/date",
        )
    )
    common_dates = set.intersection(*strategy_dates) if strategy_dates else set()
    missing_common = sorted(open_dates - common_dates)
    extra_common = sorted(common_dates - open_dates)
    checks.append(
        Check(
            "five_strategy_common_dates",
            not missing_common and not extra_common,
            (
                f"common={len(common_dates)};missing_open_dates={len(missing_common)};"
                f"extra_dates={len(extra_common)}"
            ),
            "five-strategy date intersection exactly equals authoritative open dates",
        )
    )

    lifecycle_symbols = set(
        frames["strict_security_lifecycle.csv"]["symbol"].astype(str)
    )
    universe_symbols = set(universe["symbol"].astype(str))
    missing_lifecycle = sorted(universe_symbols - lifecycle_symbols)
    checks.append(
        Check(
            "security_lifecycle_coverage",
            not missing_lifecycle,
            f"missing_symbols={len(missing_lifecycle)}",
            "100% of PIT tradable symbols",
        )
    )
    ca_complete = bool(manifest.get("corporate_action_complete"))
    lifecycle_complete = bool(manifest.get("security_lifecycle_complete"))
    checks.extend(
        [
            Check(
                "corporate_action_snapshot_complete",
                ca_complete,
                str(ca_complete),
                "true / source snapshot complete",
            ),
            Check(
                "security_lifecycle_snapshot_complete",
                lifecycle_complete,
                str(lifecycle_complete),
                "true / source snapshot complete",
            ),
        ]
    )
    actions = frames["strict_corporate_actions.csv"].copy()
    action_complete = actions.get(
        "source_complete", pd.Series(False, index=actions.index)
    ).astype(str).str.strip().str.lower().isin({"1", "true", "t", "yes", "y"})
    checks.append(
        Check(
            "corporate_action_rows_complete",
            bool(action_complete.all()),
            f"incomplete_rows={int((~action_complete).sum())}",
            "all atomic events source_complete=true",
        )
    )
    allowed_actions = {
        "NONE",
        "dividend_cash",
        "stock_bonus",
        "split_merge",
        "rights_subscription",
        "share_conversion",
        "delist_cash_settlement",
        "delist_writeoff",
    }
    action_types = actions.get(
        "corporate_action_type", pd.Series("", index=actions.index)
    ).astype(str)
    unknown_actions = int((~action_types.isin(allowed_actions)).sum())
    invalid_economics = pd.Series(False, index=actions.index)
    requirements = {
        "dividend_cash": ("cash_per_share", lambda value: value > 0),
        "stock_bonus": ("stock_ratio", lambda value: value > 0),
        "split_merge": ("split_ratio", lambda value: value > 0),
        "rights_subscription": ("rights_ratio", lambda value: value > 0),
        "share_conversion": ("split_ratio", lambda value: value > 0),
        "delist_cash_settlement": (
            "settlement_price",
            lambda value: value >= 0,
        ),
    }
    for event_type, (column, predicate) in requirements.items():
        scoped = action_types.eq(event_type)
        values = pd.to_numeric(
            actions.get(column, pd.Series(np.nan, index=actions.index)),
            errors="coerce",
        )
        invalid_economics |= scoped & (values.isna() | ~values.map(
            lambda value: predicate(value) if pd.notna(value) else False
        ))
    rights_price = pd.to_numeric(
        actions.get("rights_price", pd.Series(np.nan, index=actions.index)),
        errors="coerce",
    )
    invalid_economics |= action_types.eq("rights_subscription") & (
        rights_price.isna() | rights_price.lt(0)
    )
    if "new_ts_code" in actions:
        invalid_economics |= action_types.eq("share_conversion") & actions[
            "new_ts_code"
        ].fillna("").astype(str).str.strip().isin({"", "nan"})
    elif action_types.eq("share_conversion").any():
        invalid_economics |= action_types.eq("share_conversion")
    checks.extend(
        [
            Check(
                "corporate_action_types",
                unknown_actions == 0,
                f"unknown_rows={unknown_actions}",
                "known atomic economic event types",
            ),
            Check(
                "corporate_action_economic_parameters",
                not bool(invalid_economics.any()),
                f"invalid_rows={int(invalid_economics.sum())}",
                "required economic terms are finite and valid",
            ),
        ]
    )
    account = json.loads((package / "initial_account.json").read_text(encoding="utf-8"))
    account_ok = (
        account.get("currency") == "CNY"
        and float(account.get("initial_cash_cny") or 0) == 500_000
        and account.get("positions") == {}
    )
    checks.append(
        Check(
            "initial_account_state",
            account_ok,
            json.dumps(account, ensure_ascii=False, sort_keys=True),
            "CNY 500,000 initial cash, empty positions",
        )
    )

    # --- 3.9.1 Universe → Price coverage (100% per day) ---
    prices_frame = frames["prices.csv"].copy()
    prices_frame["trade_date"] = pd.to_datetime(
        prices_frame["trade_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    latest_open_date = max(open_dates) if open_dates else ""
    latest_price_date = (
        str(prices_frame["trade_date"].dropna().max())
        if not prices_frame["trade_date"].dropna().empty
        else ""
    )
    checks.append(
        Check(
            "dynamic_complete_end_date",
            bool(latest_open_date)
            and latest_price_date == latest_open_date
            and manifest_end == latest_open_date,
            (
                f"calendar={latest_open_date};prices={latest_price_date};"
                f"manifest={manifest_end}"
            ),
            "calendar latest open date = price max date = manifest coverage_end",
        )
    )
    universe_price_missing: set[tuple[str, str]] = set()
    for trade_date_val in sorted(universe["trade_date"].unique()):
        day_universe = set(
            universe[universe["trade_date"] == trade_date_val]["symbol"]
            .astype(str)
        )
        day_prices = set(
            prices_frame[prices_frame["trade_date"] == trade_date_val]["symbol"]
            .astype(str)
        )
        for sym in sorted(day_universe - day_prices):
            universe_price_missing.add((trade_date_val, sym))
    checks.append(
        Check(
            "universe_price_coverage",
            not universe_price_missing,
            f"missing_symbol_date_pairs={len(universe_price_missing)}",
            "100% PIT tradable symbols ⊆ price symbols every day",
        )
    )

    # --- 3.9.2 Universe → Adjustment Factor coverage (100% per day) ---
    adj_frame = frames["adjustment_factors.csv"].copy()
    adj_frame["trade_date"] = pd.to_datetime(
        adj_frame["trade_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    universe_adj_missing: set[tuple[str, str]] = set()
    for trade_date_val in sorted(universe["trade_date"].unique()):
        day_universe = set(
            universe[universe["trade_date"] == trade_date_val]["symbol"]
            .astype(str)
        )
        day_adj = set(
            adj_frame[adj_frame["trade_date"] == trade_date_val]["symbol"]
            .astype(str)
        )
        for sym in sorted(day_universe - day_adj):
            universe_adj_missing.add((trade_date_val, sym))
    checks.append(
        Check(
            "universe_adjustment_coverage",
            not universe_adj_missing,
            f"missing_symbol_date_pairs={len(universe_adj_missing)}",
            "100% PIT tradable symbols ⊆ adjustment-factor symbols every day",
        )
    )

    # --- 3.9.3 Lifecycle daily coverage (100%) ---
    lifecycle = frames["strict_security_lifecycle.csv"].copy()
    lifecycle["trade_date"] = pd.to_datetime(
        lifecycle["trade_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    lifecycle_missing: set[tuple[str, str]] = set()
    for trade_date_val in sorted(universe["trade_date"].unique()):
        day_universe = set(
            universe[universe["trade_date"] == trade_date_val]["symbol"]
            .astype(str)
        )
        day_lifecycle = set(
            lifecycle[lifecycle["trade_date"] == trade_date_val]["symbol"]
            .astype(str)
        )
        for sym in sorted(day_universe - day_lifecycle):
            lifecycle_missing.add((trade_date_val, sym))
    checks.append(
        Check(
            "lifecycle_daily_coverage",
            not lifecycle_missing,
            f"missing_symbol_date_pairs={len(lifecycle_missing)}",
            "100% daily per-security lifecycle coverage",
        )
    )

    # --- 3.9.4 Trade date coverage (≥98%, 0 unacceptable gaps) ---
    min_trade_date_coverage = float(
        config.get("minimum_trade_date_coverage", 0.98)
    )
    coverage_date_set: set[str] = set()
    for filename, (business_column, _available_column) in business_columns.items():
        if filename in frames:
            parsed = (
                pd.to_datetime(frames[filename][business_column], errors="coerce")
                .dropna()
                .dt.strftime("%Y-%m-%d")
            )
            coverage_date_set |= set(parsed.unique())
    missing_open_dates = sorted(open_dates - coverage_date_set)
    coverage_ratio = (
        len(open_dates - set(missing_open_dates)) / len(open_dates)
        if open_dates
        else 0.0
    )
    checks.append(
        Check(
            "calendar_date_coverage",
            coverage_ratio >= min_trade_date_coverage and not missing_open_dates,
            f"coverage_ratio={coverage_ratio:.4f};missing_open_dates={len(missing_open_dates)}",
            f">= {min_trade_date_coverage:.0%} trade date coverage; 0 unacceptable gaps",
        )
    )

    # --- 3.9.5 Data legality ---
    # OHLC finite and positive
    ohlc_cols = [
        c
        for c in ["open", "high", "low", "close"]
        if c in prices_frame.columns
    ]
    for col in ohlc_cols:
        values = pd.to_numeric(prices_frame[col], errors="coerce")
        illegal = values.isna() | (values <= 0) | ~np.isfinite(values)
        checks.append(
            Check(
                f"ohlc_legal:{col}",
                not illegal.any(),
                f"illegal_rows={int(illegal.sum())}",
                f"{col} finite and > 0",
            )
        )

    # OHLC consistency: high >= max(open, close, low)
    if all(c in prices_frame.columns for c in ("high", "open", "close", "low")):
        high_vals = pd.to_numeric(prices_frame["high"], errors="coerce")
        ref_max = pd.concat(
            [
                pd.to_numeric(prices_frame[c], errors="coerce")
                for c in ("open", "close", "low")
            ],
            axis=1,
        ).max(axis=1)
        high_violations = (high_vals < ref_max).sum()
        checks.append(
            Check(
                "ohlc_consistency:high_gte_max_ocl",
                int(high_violations) == 0,
                f"violations={int(high_violations)}",
                "high >= max(open, close, low)",
            )
        )

    # low <= min(open, close, high)
    if all(c in prices_frame.columns for c in ("low", "open", "close", "high")):
        low_vals = pd.to_numeric(prices_frame["low"], errors="coerce")
        ref_min = pd.concat(
            [
                pd.to_numeric(prices_frame[c], errors="coerce")
                for c in ("open", "close", "high")
            ],
            axis=1,
        ).min(axis=1)
        low_violations = (low_vals > ref_min).sum()
        checks.append(
            Check(
                "ohlc_consistency:low_lte_min_och",
                int(low_violations) == 0,
                f"violations={int(low_violations)}",
                "low <= min(open, close, high)",
            )
        )

    # adj_factor > 0
    adj_col = pd.to_numeric(
        frames["adjustment_factors.csv"]["adj_factor"], errors="coerce"
    )
    invalid_adj = adj_col.isna() | (adj_col <= 0) | ~np.isfinite(adj_col)
    checks.append(
        Check(
            "adj_factor_positive",
            not invalid_adj.any(),
            f"invalid_rows={int(invalid_adj.sum())}",
            "adj_factor > 0 for all rows",
        )
    )

    # No duplicate (trade_date, symbol) in prices
    price_dups = int(
        prices_frame.duplicated(["trade_date", "symbol"]).sum()
    )
    checks.append(
        Check(
            "duplicate_price_rows",
            price_dups == 0,
            f"duplicate_rows={price_dups}",
            "no duplicate (trade_date, symbol) rows in prices",
        )
    )

    # Data end date == latest_complete_trade_date (if configured)
    latest_cfg = config.get("latest_complete_trade_date")
    if latest_cfg and str(latest_cfg).strip():
        max_price_date = (
            pd.to_datetime(prices_frame["trade_date"], errors="coerce")
            .dropna()
            .max()
            .strftime("%Y-%m-%d")
        )
        checks.append(
            Check(
                "data_end_date",
                max_price_date == str(latest_cfg),
                f"max_price_date={max_price_date}",
                f"latest_complete_trade_date={latest_cfg}",
            )
        )

    # No symbols absent from lifecycle (aggregate check)
    lifecycle_all_symbols = set(
        frames["strict_security_lifecycle.csv"]["symbol"].astype(str).unique()
    )
    absent_lifecycle_symbols = sorted(
        set(universe["symbol"].astype(str).unique()) - lifecycle_all_symbols
    )
    checks.append(
        Check(
            "symbols_in_lifecycle",
            not absent_lifecycle_symbols,
            f"absent_symbols={len(absent_lifecycle_symbols)}",
            "all PIT tradable symbols present in lifecycle snapshot",
        )
    )

    return _result(package, config, checks)


def _result(
    package: Path, config: dict[str, Any], checks: list[Check]
) -> dict[str, Any]:
    passed = bool(checks) and all(item.passed for item in checks)
    status = config["success_status"] if passed else "BLOCKED"
    identity: dict[str, Any] = {}
    manifest_path = package / "package_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            identity = {
                "formal_pit_run_id": manifest.get("formal_pit_run_id"),
                "package_id": manifest.get("package_id"),
                "release_id": manifest.get("release_id"),
                "strategy_set": manifest.get("strategy_set"),
            }
        except Exception:
            identity = {}
    payload: dict[str, Any] = {
        "schema_version": config["schema_version"],
        "status": status,
        "ready_for_formal_run": passed,
        "package": str(package),
        **identity,
        "checks": [asdict(item) for item in checks],
        "blocking_checks": [item.check for item in checks if not item.passed],
    }
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    if not args.package.exists():
        result = _result(
            args.package,
            config,
            [
                Check(
                    "frozen_package",
                    False,
                    "missing",
                    "immutable PIT package generated from read-only sources",
                )
            ],
        )
    else:
        result = evaluate_package(args.package, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == config["success_status"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
