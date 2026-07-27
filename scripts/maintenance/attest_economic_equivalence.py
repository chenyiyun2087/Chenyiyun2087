"""Create the fail-closed PR-A economic-equivalence attestation.

The checker never infers equivalence from unit tests.  A PASS requires exact
canonical equality for every economic domain in both replay scopes and three
warm Web benchmark rounds with at least twenty samples per endpoint.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "economic_equivalence.yaml"


def _sha(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scalar(value: str) -> Any:
    if value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return value
    if not math.isfinite(parsed):
        return value
    return int(parsed) if parsed.is_integer() else parsed


def canonical_file(path: Path) -> tuple[str, int | None]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = [
                {key: _scalar(value) for key, value in row.items()}
                for row in csv.DictReader(handle)
            ]
        normalized = sorted(
            rows,
            key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )
        return _sha(normalized), len(normalized)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _sha(payload), None


def _compare_scope(
    name: str,
    scope: dict[str, Any],
    domains: dict[str, str],
) -> dict[str, Any]:
    baseline_raw = scope.get("baseline_dir")
    candidate_raw = scope.get("candidate_dir")
    result: dict[str, Any] = {
        "scope": name,
        "status": "BLOCKED",
        "expected_trade_days": int(scope.get("expected_trade_days", 0)),
        "domains": [],
        "blocking_reasons": [],
    }
    if not baseline_raw or not candidate_raw:
        result["blocking_reasons"].append("replay_directory_missing")
        return result
    baseline_dir = PROJECT_ROOT / str(baseline_raw)
    candidate_dir = PROJECT_ROOT / str(candidate_raw)
    for domain, relative in domains.items():
        left = baseline_dir / relative
        right = candidate_dir / relative
        row: dict[str, Any] = {
            "domain": domain,
            "baseline": str(left),
            "candidate": str(right),
            "equal": False,
        }
        if not left.exists() or not right.exists():
            row["reason"] = "file_missing"
            result["blocking_reasons"].append(f"{domain}:file_missing")
        else:
            left_sha, left_rows = canonical_file(left)
            right_sha, right_rows = canonical_file(right)
            row.update(
                {
                    "baseline_sha256": left_sha,
                    "candidate_sha256": right_sha,
                    "baseline_rows": left_rows,
                    "candidate_rows": right_rows,
                    "equal": left_sha == right_sha and left_rows == right_rows,
                }
            )
            if not row["equal"]:
                result["blocking_reasons"].append(f"{domain}:economic_diff")
        result["domains"].append(row)
    if not result["blocking_reasons"] and len(result["domains"]) == len(domains):
        result["status"] = "PASS"
    else:
        result["status"] = (
            "FAIL"
            if any("economic_diff" in reason for reason in result["blocking_reasons"])
            else "BLOCKED"
        )
    return result


def _endpoint_rounds(payload: dict[str, Any]) -> dict[str, list[list[float]]]:
    grouped: dict[str, list[list[float]]] = {}
    for item in payload.get("rounds", []):
        endpoint = str(item.get("endpoint") or "")
        samples = [float(value) for value in item.get("latencies_ms", [])]
        if endpoint:
            grouped.setdefault(endpoint, []).append(samples)
    return grouped


def _p95(samples: list[float]) -> float:
    values = sorted(samples)
    index = max(0, math.ceil(0.95 * len(values)) - 1)
    return values[index]


def _compare_web(scope: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "endpoints": [],
        "blocking_reasons": [],
    }
    baseline_raw = scope.get("baseline_json")
    candidate_raw = scope.get("candidate_json")
    if not baseline_raw or not candidate_raw:
        result["blocking_reasons"].append("web_benchmark_evidence_missing")
        return result
    paths = [PROJECT_ROOT / str(baseline_raw), PROJECT_ROOT / str(candidate_raw)]
    if any(not path.exists() for path in paths):
        result["blocking_reasons"].append("web_benchmark_file_missing")
        return result
    baseline = _endpoint_rounds(json.loads(paths[0].read_text(encoding="utf-8")))
    candidate = _endpoint_rounds(json.loads(paths[1].read_text(encoding="utf-8")))
    required_rounds = int(scope["required_rounds"])
    required_samples = int(scope["samples_per_endpoint_per_round"])
    max_regression = float(scope["max_p95_regression_ratio"])
    for endpoint in sorted(set(baseline) | set(candidate)):
        left = baseline.get(endpoint, [])
        right = candidate.get(endpoint, [])
        valid_shape = (
            len(left) >= required_rounds
            and len(right) >= required_rounds
            and all(len(row) >= required_samples for row in left[:required_rounds])
            and all(len(row) >= required_samples for row in right[:required_rounds])
        )
        left_median = (
            sorted(_p95(row) for row in left[:required_rounds])[required_rounds // 2]
            if valid_shape
            else None
        )
        right_median = (
            sorted(_p95(row) for row in right[:required_rounds])[required_rounds // 2]
            if valid_shape
            else None
        )
        regression = (
            right_median / left_median - 1.0
            if valid_shape and left_median and right_median is not None
            else None
        )
        passed = bool(valid_shape and regression is not None and regression <= max_regression)
        result["endpoints"].append(
            {
                "endpoint": endpoint,
                "baseline_median_p95_ms": left_median,
                "candidate_median_p95_ms": right_median,
                "regression_ratio": regression,
                "passed": passed,
            }
        )
        if not passed:
            result["blocking_reasons"].append(f"{endpoint}:web_p95_gate_failed")
    if result["endpoints"] and not result["blocking_reasons"]:
        result["status"] = "PASS"
    elif result["endpoints"]:
        result["status"] = "FAIL"
    return result


def build_attestation(config: dict[str, Any], *, code_commit: str) -> dict[str, Any]:
    scopes = config["scopes"]
    replay_results = [
        _compare_scope(name, scopes[name], config["domains"])
        for name in (
            "frozen_account_615d",
            "latest_10_complete_production_days",
        )
    ]
    web_result = _compare_web(scopes["web_benchmark"])
    statuses = [row["status"] for row in replay_results] + [web_result["status"]]
    status = "PASS" if all(value == "PASS" for value in statuses) else (
        "FAIL" if "FAIL" in statuses else "BLOCKED"
    )
    commit_chain = dict(config["commit_chain"])
    commit_chain["candidate_commit"] = code_commit
    payload = {
        "schema_version": config["schema_version"],
        "release_id": config["release_id"],
        "currency": config["currency"],
        "status": status,
        "capital_effect": "NONE",
        "production_route_changed": False,
        "commit_chain": commit_chain,
        "replay_scopes": replay_results,
        "web_benchmark": web_result,
        "blocking_reasons": sorted(
            {
                reason
                for row in replay_results + [web_result]
                for reason in row["blocking_reasons"]
            }
        ),
    }
    payload["attestation_sha256"] = _sha(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-commit")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    code_commit = args.code_commit or subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
    ).strip()
    payload = build_attestation(config, code_commit=code_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
