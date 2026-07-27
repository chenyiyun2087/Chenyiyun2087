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


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
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


def evaluate(formal_manifest: Path, matrix_path: Path) -> dict[str, Any]:
    if not formal_manifest.exists():
        return _blocked(formal_manifest, matrix_path, ["formal_manifest_missing"])
    try:
        formal = json.loads(formal_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blocked(formal_manifest, matrix_path, ["formal_manifest_invalid"])
    if formal.get("status") != "VERIFIED":
        return _blocked(formal_manifest, matrix_path, ["formal_run_not_verified"])
    if not matrix_path.exists():
        return _blocked(formal_manifest, matrix_path, ["capacity_matrix_missing"])
    try:
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blocked(formal_manifest, matrix_path, ["capacity_matrix_invalid"])

    acceptance = _acceptance()
    sizes = {int(value) for value in acceptance["account_sizes"]}
    scenarios = {
        str(row["id"]): {
            "cost_rate": float(row["cost_rate"]),
            "slippage_bps": int(row["slippage_bps"]),
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
        for field in REQUIRED_METRICS:
            if not _is_number(cell.get(field)):
                blockers.append(f"metric_missing_or_invalid:{label}:{field}")
        artifact_sha = str(cell.get("artifact_sha256") or "")
        if len(artifact_sha) != 64 or any(
            character not in "0123456789abcdef" for character in artifact_sha
        ):
            blockers.append(f"artifact_sha256_invalid:{label}")
        # PR-H1: Verify artifact directory actually exists and files match SHAs
        formal_run_id = str(formal.get("formal_run_id") or "")
        if formal_run_id:
            artifact_base = Path(
                str(formal.get("_capacity_artifact_root") or "")
                or str(PROJECT_ROOT / "exports" / "execution_capacity")
            )
            artifact_dir = (
                artifact_base / formal_run_id / str(cell["account_size"]) / str(cell["scenario"])
            )
            if not artifact_dir.is_dir():
                blockers.append(f"capacity_artifact_dir_missing:{label}")
            else:
                for req_file in ("execution_metrics.json", "nav.csv", "artifact_manifest.json"):
                    fpath = artifact_dir / req_file
                    if not fpath.is_file():
                        blockers.append(f"capacity_artifact_file_missing:{label}:{req_file}")
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
    args = parser.parse_args()
    result = evaluate(args.formal_manifest, args.capacity_matrix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
