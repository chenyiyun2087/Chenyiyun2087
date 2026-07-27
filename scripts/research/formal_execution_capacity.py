#!/usr/bin/env python3
"""Validate the immutable 5×5 execution and capacity evidence matrix.

The validator never estimates missing production metrics.  It accepts only a
matrix bound to a VERIFIED formal run and distinguishes incomplete technical
evidence from complete evidence that fails an economic gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = PROJECT_ROOT / "config" / "production_acceptance.yaml"
REQUIRED_METRICS = (
    "max_order_adv_ratio",
    "expected_impact_bps_p95",
    "realized_slippage_bps",
    "turnover",
    "capital_utilization",
    "partial_fill_count",
    "delayed_fill_count",
    "failed_order_count",
    "order_count",
    "failure_rate",
    "cumulative_return",
    "max_drawdown",
    "drawdown_widening",
)


def _acceptance() -> dict[str, Any]:
    return (
        yaml.safe_load(ACCEPTANCE_PATH.read_text(encoding="utf-8")) or {}
    )["acceptance"]["execution"]


def _canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha(path: Path) -> str:
    """SHA-256 of a single file (streaming, 1 MiB chunks)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_non_negative_integer(value: Any) -> bool:
    """P0-5: Reject bool, float-with-decimals, negatives for discrete count fields."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value).is_integer()
        and float(value) >= 0
    )


def _blocked(
    formal_manifest: Path,
    matrix_path: Path,
    blockers: list[str],
) -> dict[str, Any]:
    result = {
        "schema_version": "formal_execution_capacity_v1",
        "status": "BLOCKED",
        "technical_evidence_complete": False,
        "economic_gates_passed": False,
        "formal_manifest": str(formal_manifest),
        "capacity_matrix": str(matrix_path),
        "blockers": sorted(set(blockers)),
        "gates": {},
    }
    result["evidence_sha256"] = _canonical_sha(result)
    return result


def evaluate(
    formal_manifest: Path, matrix_path: Path,
    *, artifact_root: Path | None = None,
) -> dict[str, Any]:
    if not formal_manifest.exists():
        return _blocked(formal_manifest, matrix_path, ["formal_manifest_missing"])
    try:
        formal = json.loads(formal_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blocked(formal_manifest, matrix_path, ["formal_manifest_invalid"])
    if formal.get("status") != "VERIFIED":
        return _blocked(formal_manifest, matrix_path, ["formal_run_not_verified"])
    # P0-8: Verify Formal Manifest self-hash (same pattern as OOS evaluator)
    if not formal.get("manifest_sha256"):
        return _blocked(formal_manifest, matrix_path, ["formal_manifest_incomplete_no_self_sha"])
    if not formal.get("frozen_bundle_sha256"):
        return _blocked(formal_manifest, matrix_path, ["formal_manifest_incomplete_no_frozen_bundle"])
    manifest_without_self = {k: v for k, v in formal.items() if k != "manifest_sha256"}
    computed_manifest_sha = hashlib.sha256(
        json.dumps(manifest_without_self, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if formal.get("manifest_sha256") != computed_manifest_sha:
        return _blocked(formal_manifest, matrix_path, ["formal_manifest_sha_mismatch"])
    # P0-1: Require explicit fixture_mode: false (same rule as OOS evaluator)
    if formal.get("fixture_mode") is not False:
        return _blocked(formal_manifest, matrix_path, ["formal_manifest_fixture_mode_required_false"])
    if not matrix_path.exists():
        return _blocked(formal_manifest, matrix_path, ["capacity_matrix_missing"])
    try:
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blocked(formal_manifest, matrix_path, ["capacity_matrix_invalid"])

    acceptance = _acceptance()
    sizes = {int(value) for value in acceptance["account_sizes"]}
    # P0-7 fix: include adv_limit_type in scenarios dict
    scenarios = {
        str(row["id"]): {
            "cost_rate": float(row["cost_rate"]),
            "slippage_bps": int(row["slippage_bps"]),
            "adv_limit_type": str(row.get("adv_limit_type", "base")),
        }
        for row in acceptance["scenarios"]
    }
    blockers: list[str] = []
    formal_run_id = str(formal.get("formal_run_id") or "")
    if not formal_run_id or matrix.get("formal_run_id") != formal_run_id:
        blockers.append("formal_run_identity_mismatch")
    if matrix.get("schema_version") != "formal_execution_capacity_matrix_v1":
        blockers.append("capacity_matrix_schema_invalid")
    cells = matrix.get("cells")
    if not isinstance(cells, list):
        return _blocked(formal_manifest, matrix_path, blockers + ["cells_missing"])

    expected = {(size, scenario) for size in sizes for scenario in scenarios}
    identities: list[tuple[int, str]] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            blockers.append(f"cell_not_object:{index}")
            continue
        try:
            identity = (int(cell.get("account_size")), str(cell.get("scenario")))
        except (TypeError, ValueError):
            blockers.append(f"cell_identity_invalid:{index}")
            continue
        identities.append(identity)
        label = f"{identity[0]}:{identity[1]}"
        specification = scenarios.get(identity[1])
        if not specification:
            blockers.append(f"scenario_unknown:{label}")
        else:
            if (
                not _is_number(cell.get("cost_rate"))
                or not math.isclose(
                    float(cell["cost_rate"]),
                    specification["cost_rate"],
                    rel_tol=0,
                    abs_tol=1e-12,
                )
            ):
                blockers.append(f"cost_rate_mismatch:{label}")
            if cell.get("slippage_bps") != specification["slippage_bps"]:
                blockers.append(f"slippage_mismatch:{label}")
            if int(specification["slippage_bps"]) <= 0:
                blockers.append(f"zero_slippage_forbidden:{label}")
        CELL_DISCRETE = {"order_count", "failed_order_count", "partial_fill_count", "delayed_fill_count"}
        for field in REQUIRED_METRICS:
            if field in CELL_DISCRETE:
                if not _is_non_negative_integer(cell.get(field)):
                    blockers.append(f"metric_missing_or_invalid:{label}:{field}")
            elif not _is_number(cell.get(field)):
                blockers.append(f"metric_missing_or_invalid:{label}:{field}")
        artifact_sha = str(cell.get("artifact_sha256") or "")
        if len(artifact_sha) != 64 or any(
            character not in "0123456789abcdef" for character in artifact_sha
        ):
            blockers.append(f"artifact_sha256_invalid:{label}")
        # PR-H1: Verify artifact directory actually exists and files match SHAs
        formal_run_id = str(formal.get("formal_run_id") or "")
        if formal_run_id:
            artifact_base = (
                artifact_root
                if artifact_root is not None
                else PROJECT_ROOT / "exports" / "execution_capacity"
            )
            artifact_dir = (
                artifact_base / formal_run_id / str(cell["account_size"]) / str(cell["scenario"])
            )
            if not artifact_dir.is_dir():
                blockers.append(f"capacity_artifact_dir_missing:{label}")
            else:
                # P0-12: Deep artifact verification — read manifest, verify SHAs, require orders+fills
                artifact_manifest_path = artifact_dir / "artifact_manifest.json"
                if not artifact_manifest_path.is_file():
                    blockers.append(f"capacity_artifact_manifest_missing:{label}")
                else:
                    try:
                        art_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
                        # P0-4: ALL key fields are mandatory — any missing → BLOCKED
                        art_run_id = str(art_manifest.get("formal_run_id") or "")
                        if not art_run_id:
                            blockers.append(f"capacity_artifact_missing_formal_run_id:{label}")
                        elif art_run_id != formal_run_id:
                            blockers.append(f"capacity_artifact_run_id_mismatch:{label}")
                        if not art_manifest.get("schema_version"):
                            blockers.append(f"capacity_artifact_missing_schema_version:{label}")
                        if str(art_manifest.get("account_size") or "") != str(cell["account_size"]):
                            blockers.append(f"capacity_artifact_account_size_mismatch:{label}")
                        if str(art_manifest.get("scenario") or "") != str(cell["scenario"]):
                            blockers.append(f"capacity_artifact_scenario_mismatch:{label}")
                        # P0-3: admission_candidate_strategy_id MANDATORY, no fallback
                        artifact_strategy_id = art_manifest.get("strategy_id")
                        if not artifact_strategy_id:
                            blockers.append(f"capacity_artifact_missing_strategy_id:{label}")
                        else:
                            admission_id = formal.get("admission_candidate_strategy_id")
                            if not isinstance(admission_id, str) or not admission_id:
                                blockers.append(f"capacity_formal_missing_admission_candidate:{label}")
                            elif str(artifact_strategy_id) != admission_id:
                                blockers.append(f"capacity_artifact_strategy_id_mismatch:{label}")
                        art_fm_sha = str(art_manifest.get("formal_manifest_sha256") or "")
                        if not art_fm_sha:
                            blockers.append(f"capacity_artifact_missing_formal_manifest_sha256:{label}")
                        elif art_fm_sha != computed_manifest_sha:
                            blockers.append(f"capacity_artifact_formal_manifest_sha_mismatch:{label}")
                        if art_manifest.get("schema_version") != "capacity_artifact_manifest_v1":
                            blockers.append(f"capacity_artifact_bad_schema_version:{label}")
                        spec = scenarios.get(str(cell["scenario"]), {})
                        if abs(float(art_manifest.get("cost_rate", -1) or -1) - float(cell.get("cost_rate", -2))) > 1e-12:
                            blockers.append(f"capacity_artifact_cost_rate_mismatch:{label}")
                        if int(art_manifest.get("slippage_bps", -1) or -1) != int(cell.get("slippage_bps", -2)):
                            blockers.append(f"capacity_artifact_slippage_bps_mismatch:{label}")
                        adv_type_manifest = str(art_manifest.get("adv_limit_type") or "")
                        adv_type_spec = str(spec.get("adv_limit_type", "base"))
                        if not adv_type_manifest:
                            blockers.append(f"capacity_artifact_missing_adv_limit_type:{label}")
                        elif adv_type_manifest != adv_type_spec:
                            blockers.append(f"capacity_artifact_adv_limit_type_mismatch:{label}")
                        gen_sha = str(art_manifest.get("generator_git_sha") or "")
                        if not gen_sha:
                            blockers.append(f"capacity_artifact_missing_generator_sha:{label}")
                        elif len(gen_sha) != 40 or any(c not in "0123456789abcdef" for c in gen_sha):
                            blockers.append(f"capacity_artifact_invalid_generator_sha:{label}")
                        else:
                            formal_cap_gen = str(formal.get("capacity_generator_git_sha") or "")
                            if not formal_cap_gen:
                                blockers.append(f"capacity_formal_missing_capacity_generator_sha:{label}")
                            elif gen_sha != formal_cap_gen:
                                blockers.append(f"capacity_artifact_generator_sha_mismatch:{label}")
                                        # P0-12: _capacity_artifact_root only allowed via explicit CLI arg, not manifest
                        # Verify artifact_sha256 matches the manifest's self-hash (MANDATORY)
                        manifest_files = art_manifest.get("files") or {}
                        manifest_self_sha = str(art_manifest.get("manifest_sha256") or "")
                        if not manifest_self_sha:
                            blockers.append(f"capacity_artifact_missing_manifest_sha:{label}")
                        else:
                            manifest_content = {k: v for k, v in art_manifest.items() if k != "manifest_sha256"}
                            computed_art_self_sha = hashlib.sha256(
                                json.dumps(manifest_content, sort_keys=True, separators=(",", ":")).encode()
                            ).hexdigest()
                            if computed_art_self_sha != manifest_self_sha:
                                blockers.append(f"capacity_artifact_manifest_sha_mismatch:{label}")
                            if artifact_sha != manifest_self_sha:
                                blockers.append(f"capacity_artifact_sha_vs_manifest_mismatch:{label}")
                        # Require all 4 artifact files with SHA verification
                        for req_file in ("execution_metrics.json", "nav.csv", "orders.csv", "fills.csv"):
                            fpath = artifact_dir / req_file
                            if not fpath.is_file():
                                blockers.append(f"capacity_artifact_file_missing:{label}:{req_file}")
                            elif req_file not in manifest_files:
                                blockers.append(f"capacity_artifact_file_not_in_manifest:{label}:{req_file}")
                            else:
                                declared_file_sha = str(manifest_files[req_file].get("sha256") or "")
                                if not declared_file_sha:
                                    blockers.append(f"capacity_artifact_file_sha_missing:{label}:{req_file}")
                                else:
                                    actual_file_sha = _sha(fpath)
                                    if actual_file_sha != declared_file_sha:
                                        blockers.append(
                                            f"capacity_artifact_file_sha_mismatch:{label}:{req_file}"
                                        )
                        # P0-10: NAV fail-closed — require valid NAV with ≥2 rows, no gaps
                        nav_path = artifact_dir / "nav.csv"
                        if not nav_path.is_file():
                            blockers.append(f"capacity_nav_missing:{label}")
                        else:
                            try:
                                nav_df = pd.read_csv(nav_path)
                                # P0-6: trade_date column is MANDATORY for NAV
                                if "trade_date" not in nav_df.columns:
                                    blockers.append(f"capacity_nav_missing_trade_date_column:{label}")
                                elif "nav" not in nav_df.columns:
                                    blockers.append(f"capacity_nav_missing_column:{label}")
                                elif len(nav_df) < 2:
                                    blockers.append(f"capacity_nav_insufficient_rows:{label}")
                                else:
                                    nav_vals = pd.to_numeric(nav_df["nav"], errors="coerce")
                                    if nav_vals.isna().any():
                                        blockers.append(f"capacity_nav_contains_nan:{label}")
                                    elif (nav_vals <= 0).any():
                                        blockers.append(f"capacity_nav_non_positive:{label}")
                                    else:
                                        # Check dates strictly increasing
                                        if "trade_date" in nav_df.columns:
                                            nav_dates = pd.to_datetime(nav_df["trade_date"], errors="coerce")
                                            if nav_dates.isna().any():
                                                blockers.append(f"capacity_nav_invalid_dates:{label}")
                                            elif not nav_dates.is_monotonic_increasing:
                                                blockers.append(f"capacity_nav_dates_not_increasing:{label}")
                                            elif nav_dates.duplicated().any():
                                                blockers.append(f"capacity_nav_duplicate_dates:{label}")
                                        nav_returns = nav_vals.pct_change().dropna()
                                        recomputed_return = float((1.0 + nav_returns).prod() - 1.0)
                                        recomputed_dd = float((nav_vals / nav_vals.cummax() - 1.0).min())
                                        cell_return = float(cell.get("cumulative_return", 0))
                                        cell_dd = float(cell.get("max_drawdown", 0))
                                        if not math.isclose(recomputed_return, cell_return, rel_tol=0, abs_tol=1e-6):
                                            blockers.append(
                                                f"capacity_nav_return_mismatch:{label}:"
                                                f"cell={cell_return:.6f}:nav={recomputed_return:.6f}"
                                            )
                                        if not math.isclose(recomputed_dd, cell_dd, rel_tol=0, abs_tol=1e-4):
                                            blockers.append(
                                                f"capacity_nav_drawdown_mismatch:{label}:"
                                                f"cell={cell_dd:.4f}:nav={recomputed_dd:.4f}"
                                            )
                            except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
                                blockers.append(f"capacity_nav_parse_error:{label}:{type(exc).__name__}")
                        # P0-F/G: Validate orders/fills counts against execution_metrics.json exactly
                        orders_path = artifact_dir / "orders.csv"
                        fills_path = artifact_dir / "fills.csv"
                        metrics_path = artifact_dir / "execution_metrics.json"
                        if orders_path.is_file() and fills_path.is_file() and metrics_path.is_file():
                            orders_df = pd.read_csv(orders_path)
                            fills_df = pd.read_csv(fills_path)
                            metrics_data = json.loads(metrics_path.read_text(encoding="utf-8"))
                            # P0-7: ALL execution metrics mandatory — any missing or invalid → BLOCKED
                            REQUIRED_METRICS_FIELDS = {
                                "order_count", "fill_count", "failed_order_count",
                                "partial_fill_count", "delayed_fill_count", "failure_rate",
                                "turnover", "realized_slippage_bps", "capital_utilization",
                                "max_order_adv_ratio", "expected_impact_bps_p95",
                            }
                            DISCRETE_COUNT_FIELDS = {
                                "order_count", "fill_count", "failed_order_count",
                                "partial_fill_count", "delayed_fill_count",
                            }
                            for mf in REQUIRED_METRICS_FIELDS:
                                if mf not in metrics_data:
                                    blockers.append(f"capacity_metrics_missing_or_invalid:{label}:{mf}")
                                elif not _is_number(metrics_data[mf]):
                                    blockers.append(f"capacity_metrics_missing_or_invalid:{label}:{mf}")
                                elif mf in DISCRETE_COUNT_FIELDS:
                                    v = metrics_data[mf]
                                    # P0-7: must be exact integer, no float truncation
                                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                                        blockers.append(f"capacity_metrics_not_integer:{label}:{mf}")
                                    elif isinstance(v, float) and not float(v).is_integer():
                                        blockers.append(f"capacity_metrics_not_integer:{label}:{mf}")
                                    elif int(v) < 0:
                                        blockers.append(f"capacity_metrics_negative:{label}:{mf}")
                            # P0-1: Only reconcile when metrics AND cell discrete fields are all valid
                            cell_metrics_valid = all(
                                _is_number(cell.get(f)) and (f not in CELL_DISCRETE or _is_non_negative_integer(cell.get(f)))
                                for f in REQUIRED_METRICS
                            )
                            metrics_all_valid = not any(f"capacity_metrics_missing_or_invalid:{label}" in b for b in blockers)
                            if metrics_all_valid and cell_metrics_valid:
                                # P0-8: Three-way exact match — raw orders == metrics == cell
                                raw_order_count = len(orders_df)
                                metrics_order_count = int(metrics_data["order_count"])
                                cell_order_count = int(cell.get("order_count", -1))
                                if raw_order_count != metrics_order_count:
                                    blockers.append(
                                        f"capacity_order_count_raw_vs_metrics:{label}:"
                                        f"raw={raw_order_count}:metrics={metrics_order_count}"
                                    )
                                if metrics_order_count != cell_order_count:
                                    blockers.append(
                                        f"capacity_order_count_metrics_vs_cell:{label}:"
                                        f"metrics={metrics_order_count}:cell={cell_order_count}"
                                    )
                                # P0-9: Cross-check fill/fail/partial counts
                                raw_fill_count = len(fills_df)
                                if raw_fill_count != int(metrics_data["fill_count"]):
                                    blockers.append(
                                        f"capacity_fill_count_raw_vs_metrics:{label}:"
                                        f"raw={raw_fill_count}:metrics={metrics_data['fill_count']}"
                                    )
                                for metric_key, cell_key in (
                                    ("failed_order_count", "failed_order_count"),
                                    ("partial_fill_count", "partial_fill_count"),
                                    ("delayed_fill_count", "delayed_fill_count"),
                                ):
                                    mv = int(metrics_data[metric_key])
                                    cv = int(cell.get(cell_key, -1))
                                    if mv != cv:
                                        blockers.append(
                                            f"capacity_{metric_key}_mismatch:{label}:"
                                            f"cell={cv}:metrics={mv}"
                                        )
                                # Cross-check continuous metrics
                                for metric_key, cell_key in (
                                    ("failure_rate", "failure_rate"),
                                    ("turnover", "turnover"),
                                    ("realized_slippage_bps", "realized_slippage_bps"),
                                    ("capital_utilization", "capital_utilization"),
                                ):
                                    mv = float(metrics_data[metric_key])
                                    cv = float(cell.get(cell_key, 0))
                                    if not math.isclose(mv, cv, rel_tol=0, abs_tol=1e-6):
                                        blockers.append(
                                            f"capacity_{metric_key}_mismatch:{label}:"
                                            f"cell={cv}:metrics={mv}"
                                        )
                    except (json.JSONDecodeError, OSError) as exc:
                        blockers.append(f"capacity_artifact_manifest_read_error:{label}:{type(exc).__name__}")
        if all(_is_number(cell.get(field)) for field in (
            "failed_order_count",
            "order_count",
            "failure_rate",
        )):
            order_count = int(cell["order_count"])
            failed = int(cell["failed_order_count"])
            if order_count <= 0 or failed < 0 or failed > order_count:
                blockers.append(f"order_counts_invalid:{label}")
            else:
                expected_rate = failed / order_count
                if not math.isclose(
                    float(cell["failure_rate"]),
                    expected_rate,
                    rel_tol=0,
                    abs_tol=1e-9,
                ):
                    blockers.append(f"failure_rate_mismatch:{label}")

    identity_set = set(identities)
    if identity_set != expected or len(identities) != len(expected):
        blockers.append("incomplete_or_duplicate_25_cell_grid")
    if blockers:
        return _blocked(formal_manifest, matrix_path, blockers)

    base_drawdowns = {
        int(cell["account_size"]): float(cell["max_drawdown"])
        for cell in cells
        if cell["scenario"] == "BASE_7P5_10"
    }
    gate_failures: list[str] = []
    gate_rows: list[dict[str, Any]] = []
    for cell in cells:
        size = int(cell["account_size"])
        scenario = str(cell["scenario"])
        label = f"{size}:{scenario}"
        # P0-7 fix: re-fetch specification for this cell (don't reuse stale from first loop)
        specification = scenarios.get(scenario, {})
        base_drawdown = base_drawdowns[size]
        calculated_widening = max(
            0.0,
            abs(float(cell["max_drawdown"])) - abs(base_drawdown),
        )
        if not math.isclose(
            float(cell["drawdown_widening"]),
            calculated_widening,
            rel_tol=0,
            abs_tol=1e-8,
        ):
            return _blocked(
                formal_manifest,
                matrix_path,
                [f"drawdown_widening_mismatch:{label}"],
            )
        # P0-13 fix: Use per-scenario adv_limit_type from config, not string-prefix matching
        stress_adv_limit = float(acceptance.get("max_single_order_adv_ratio_stress", 0.03))
        base_adv_limit = float(acceptance["max_single_order_adv_ratio_base"])
        adv_type = str(specification.get("adv_limit_type", "base"))
        limits = {
            "adv": stress_adv_limit if adv_type == "stress" else base_adv_limit,
            "impact": float(acceptance["max_expected_impact_bps_p95"]),
            "failure": (
                float(acceptance["max_unfilled_order_ratio_stress"])
                if scenario.startswith("EXTREME")
                else float(acceptance["max_unfilled_order_ratio_base"])
            ),
            "drawdown_widening": (
                float(acceptance["stress_max_dd_widening_extreme"])
                if scenario.startswith("EXTREME")
                else float(acceptance["stress_max_dd_widening_base"])
            ),
        }
        # PR-H1: Compute derived ratios
        order_count_val = int(cell.get("order_count", 0))
        partial_fill_ratio = (
            float(cell.get("partial_fill_count", 0)) / order_count_val
            if order_count_val > 0 else 0.0
        )
        delayed_fill_ratio = (
            float(cell.get("delayed_fill_count", 0)) / order_count_val
            if order_count_val > 0 else 0.0
        )
        checks = {
            "adv_limit": float(cell["max_order_adv_ratio"]) <= limits["adv"],
            "impact_limit": (
                float(cell["expected_impact_bps_p95"]) <= limits["impact"]
            ),
            "failure_rate": float(cell["failure_rate"]) <= limits["failure"],
            "drawdown": float(cell["max_drawdown"]) >= -0.35,
            "drawdown_widening": calculated_widening
            <= limits["drawdown_widening"],
            "capital_utilization": 0.0
            <= float(cell["capital_utilization"])
            <= 1.0,
            "realized_slippage": float(cell.get("realized_slippage_bps", 0))
            <= float(
                acceptance.get("max_realized_slippage_bps_stress", 150)
                if scenario.startswith("EXTREME")
                else acceptance.get("max_realized_slippage_bps", 50)
            ),
            "turnover": float(cell.get("turnover", 0))
            <= float(acceptance.get("max_turnover_ratio", 50)),
            "partial_fill_ratio": partial_fill_ratio
            <= float(acceptance.get("max_partial_fill_ratio", 0.05)),
            "delayed_fill_ratio": delayed_fill_ratio
            <= float(acceptance.get("max_delayed_fill_ratio", 0.05)),
            "extreme_positive_return": (
                float(cell["cumulative_return"]) > 0
                if scenario.startswith("EXTREME")
                else True
            ),
        }
        failed = [name for name, passed in checks.items() if not passed]
        gate_failures.extend(f"{label}:{name}" for name in failed)
        gate_rows.append(
            {
                "account_size": size,
                "scenario": scenario,
                "passed": not failed,
                "failed_checks": failed,
            }
        )

    result = {
        "schema_version": "formal_execution_capacity_v1",
        "status": "PASS" if not gate_failures else "ECONOMIC_FAILED",
        "technical_evidence_complete": True,
        "economic_gates_passed": not gate_failures,
        "formal_manifest": str(formal_manifest),
        "formal_run_id": formal_run_id,
        "capacity_matrix": str(matrix_path),
        "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest(),
        "cell_count": len(cells),
        "blockers": [],
        "gate_failures": gate_failures,
        "gates": {"cells": gate_rows},
    }
    result["evidence_sha256"] = _canonical_sha(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-manifest", type=Path, required=True)
    parser.add_argument("--capacity-matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity-artifact-root", type=Path, default=None)
    args = parser.parse_args()
    result = evaluate(
        args.formal_manifest, args.capacity_matrix,
        artifact_root=args.capacity_artifact_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
