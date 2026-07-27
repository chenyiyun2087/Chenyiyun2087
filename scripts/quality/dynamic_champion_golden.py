"""Content-addressed Golden Regression for the dynamic champion release.

The baseline binds the frozen research outputs to exact semantic projections.
It is deliberately independent from production databases, networks and wall
clock time.  A baseline can only be written with an explicit new version,
approval attribution and change reason; an existing baseline is never
overwritten.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKTEST = (
    PROJECT_ROOT
    / "exports/signal_research/20260618_213002_059138_trusted_account_backtest"
)
DEFAULT_READINESS = (
    PROJECT_ROOT
    / "exports/dynamic_champion_live_readiness/20260727_pr_f_a_to_e_v2"
)
DEFAULT_LEDGER = PROJECT_ROOT / "regression/baselines/strict_ledger_core.v1.json"
DEFAULT_BASELINE = (
    PROJECT_ROOT / "regression/baselines/dynamic_champion_full_chain.v1.json"
)


CSV_DOMAINS: dict[str, tuple[str, tuple[str, ...]]] = {
    "candidates": (
        "trusted_account_backtest_candidates.csv",
        ("signal_date", "execution_date", "strategy", "rank", "symbol"),
    ),
    "ranking": (
        "trusted_account_backtest_candidates.csv",
        (
            "signal_date",
            "strategy",
            "rank",
            "symbol",
            "rank_score",
            "pattern_adjusted_rank_score",
        ),
    ),
    "weights": (
        "trusted_account_backtest_candidates.csv",
        (
            "signal_date",
            "strategy",
            "rank",
            "symbol",
            "raw_effective_weight",
            "adjusted_target_weight",
        ),
    ),
    "orders": (
        "trusted_account_backtest_trades.csv",
        (
            "strategy",
            "trade_date",
            "symbol",
            "side",
            "price",
            "shares",
            "gross_amount",
            "cost",
            "reason",
        ),
    ),
    "nav": (
        "trusted_account_backtest_nav.csv",
        (
            "strategy",
            "trade_date",
            "cash",
            "market_value",
            "total_equity",
            "nav",
            "position_count",
            "gross_exposure",
        ),
    ),
}


class GoldenError(ValueError):
    """Raised when a baseline or frozen source violates the golden contract."""


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoldenError(f"missing_required_source:{path}") from exc
    except json.JSONDecodeError as exc:
        raise GoldenError(f"invalid_json:{path}:{exc}") from exc


def _project_csv(path: Path, columns: Iterable[str]) -> dict[str, Any]:
    required = tuple(columns)
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except FileNotFoundError as exc:
        raise GoldenError(f"missing_required_source:{path}") from exc
    with handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        missing = [column for column in required if column not in fieldnames]
        if missing:
            raise GoldenError(
                f"missing_required_columns:{path.name}:{','.join(missing)}"
            )
        rows = [[row.get(column, "") for column in required] for row in reader]
    payload = {"columns": list(required), "rows": rows}
    return {
        "source": path.name,
        "source_sha256": _sha_bytes(path.read_bytes()),
        "columns": list(required),
        "row_count": len(rows),
        "semantic_sha256": _sha_bytes(_canonical_json(payload)),
        "order_sensitive": True,
    }


def _ledger_domain(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    try:
        expected = payload["expected"]
        projection = {
            "invariants": expected["invariants"],
            "metrics": expected["metrics"],
            "artifacts": {
                key: expected["artifacts"][key]
                for key in ("events", "orders", "primary_ledger", "independent_ledger")
            },
        }
    except (KeyError, TypeError) as exc:
        raise GoldenError(f"invalid_strict_ledger_baseline:{path}") from exc
    return {
        "source": str(path.relative_to(PROJECT_ROOT))
        if path.is_relative_to(PROJECT_ROOT)
        else str(path),
        "source_sha256": _sha_bytes(path.read_bytes()),
        "semantic_sha256": _sha_bytes(_canonical_json(projection)),
        "event_row_count": int(projection["artifacts"]["events"]["row_count"]),
        "dual_ledger_verified": bool(projection["invariants"]["dual_ledger_verified"]),
    }


def _json_domain(path: Path, projection: Any) -> dict[str, Any]:
    return {
        "source": path.name,
        "source_sha256": _sha_bytes(path.read_bytes()),
        "semantic_sha256": _sha_bytes(_canonical_json(projection)),
    }


def build_actual(
    *,
    backtest_dir: Path = DEFAULT_BACKTEST,
    readiness_dir: Path = DEFAULT_READINESS,
    ledger_baseline: Path = DEFAULT_LEDGER,
) -> dict[str, Any]:
    domains: dict[str, Any] = {
        name: _project_csv(backtest_dir / filename, columns)
        for name, (filename, columns) in CSV_DOMAINS.items()
    }
    domains["ledger"] = _ledger_domain(ledger_baseline)

    readiness_path = readiness_dir / "readiness.json"
    readiness = _load_json(readiness_path)
    try:
        gate_projection = {
            "decision": readiness["decision"],
            "gates": readiness["gates"],
            "program": {
                key: readiness["program"][key]
                for key in (
                    "strategy_id",
                    "release_id",
                    "current_lane",
                    "canary_enabled",
                    "broker_api_enabled",
                )
            },
        }
    except (KeyError, TypeError) as exc:
        raise GoldenError(f"invalid_readiness_payload:{readiness_path}") from exc
    domains["gate_conclusion"] = _json_domain(readiness_path, gate_projection)

    artifact_path = readiness_dir / "artifact.json"
    artifact = _load_json(artifact_path)
    if not isinstance(artifact, dict) or not {"manifest", "snapshot"} <= set(artifact):
        raise GoldenError(f"invalid_artifact_payload:{artifact_path}")
    domains["artifact"] = _json_domain(artifact_path, artifact)

    payload: dict[str, Any] = {
        "schema_version": "dynamic_champion_content_golden_v1",
        "domains": domains,
    }
    payload["content_set_sha256"] = _sha_bytes(_canonical_json(domains))
    return payload


def verify(baseline: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    if baseline.get("schema_version") != "dynamic_champion_content_golden_v1":
        raise GoldenError("unsupported_baseline_schema")
    expected = baseline.get("expected")
    if not isinstance(expected, dict):
        raise GoldenError("baseline_expected_missing")
    expected_domains = expected.get("domains")
    actual_domains = actual.get("domains")
    if not isinstance(expected_domains, dict) or not isinstance(actual_domains, dict):
        raise GoldenError("baseline_or_actual_domains_missing")

    required = {
        "candidates",
        "ranking",
        "weights",
        "orders",
        "ledger",
        "nav",
        "gate_conclusion",
        "artifact",
    }
    missing = sorted(required - set(actual_domains))
    failures: list[dict[str, Any]] = []
    if missing:
        failures.append({"domain": "*", "reason": "missing_domains", "actual": missing})
    for domain in sorted(required & set(expected_domains) & set(actual_domains)):
        if expected_domains[domain] != actual_domains[domain]:
            failures.append(
                {
                    "domain": domain,
                    "reason": "content_drift",
                    "expected": expected_domains[domain],
                    "actual": actual_domains[domain],
                }
            )
    missing_expected = sorted(required - set(expected_domains))
    if missing_expected:
        failures.append(
            {"domain": "*", "reason": "baseline_missing_domains", "expected": missing_expected}
        )
    if expected.get("content_set_sha256") != actual.get("content_set_sha256"):
        failures.append(
            {
                "domain": "content_set",
                "reason": "aggregate_hash_drift",
                "expected": expected.get("content_set_sha256"),
                "actual": actual.get("content_set_sha256"),
            }
        )
    return {
        "schema_version": "dynamic_champion_content_golden_report_v1",
        "baseline_id": baseline.get("baseline_id"),
        "baseline_version": baseline.get("baseline_version"),
        "status": "PASS" if not failures else "FAIL",
        "failure_count": len(failures),
        "failures": failures,
        "actual_content_set_sha256": actual.get("content_set_sha256"),
    }


def write_baseline(
    path: Path,
    actual: dict[str, Any],
    *,
    baseline_version: str,
    approved_by: str,
    change_reason: str,
) -> dict[str, Any]:
    for field, value in {
        "baseline_version": baseline_version,
        "approved_by": approved_by,
        "change_reason": change_reason,
    }.items():
        if not value.strip():
            raise GoldenError(f"{field}_required")
    if path.exists():
        raise GoldenError(f"baseline_overwrite_forbidden:{path}")
    payload = {
        "schema_version": "dynamic_champion_content_golden_v1",
        "baseline_id": f"dynamic-champion-full-chain.{baseline_version}",
        "baseline_version": baseline_version,
        "metadata": {
            "approved_by": approved_by,
            "change_reason": change_reason,
            "update_policy": "new_version_only_no_overwrite",
            "production_semantics_changed": False,
        },
        "expected": actual,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-dir", type=Path, default=DEFAULT_BACKTEST)
    parser.add_argument("--readiness-dir", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--ledger-baseline", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--baseline-version", default="")
    parser.add_argument("--approved-by", default="")
    parser.add_argument("--change-reason", default="")
    args = parser.parse_args()

    try:
        actual = build_actual(
            backtest_dir=args.backtest_dir,
            readiness_dir=args.readiness_dir,
            ledger_baseline=args.ledger_baseline,
        )
        if args.write_baseline:
            result = write_baseline(
                args.baseline,
                actual,
                baseline_version=args.baseline_version,
                approved_by=args.approved_by,
                change_reason=args.change_reason,
            )
            exit_code = 0
        else:
            baseline = _load_json(args.baseline)
            result = verify(baseline, actual)
            exit_code = 0 if result["status"] == "PASS" else 1
    except GoldenError as exc:
        result = {
            "schema_version": "dynamic_champion_content_golden_report_v1",
            "status": "FAIL",
            "failure_count": 1,
            "failures": [{"domain": "*", "reason": str(exc)}],
        }
        exit_code = 2

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
