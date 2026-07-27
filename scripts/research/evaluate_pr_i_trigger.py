"""Fail-closed PR-I Alpha v1.2 trigger evaluation.

PR-I is allowed only when all identity/data/ledger/execution evidence is
technically complete and the remaining failures are purely economic.  Missing
or blocked technical evidence always returns ``PR_I_NOT_TRIGGERED``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES = {
    "pr_a_equivalence": PROJECT_ROOT
    / "exports/economic_equivalence/20260727_pr_a/economic_equivalence_attestation.json",
    "pr_b_formal_readiness": PROJECT_ROOT
    / "exports/formal_readiness/20260727_pr_b/formal_readiness_preflight.json",
    "pr_c_formal_run": PROJECT_ROOT
    / "exports/formal_runs/20260727_pr_c/formal_run_precheck.json",
    "pr_d_oos_robustness": PROJECT_ROOT
    / "exports/formal_oos/20260727_pr_d/formal_oos_robustness.json",
    "pr_e_execution_capacity": PROJECT_ROOT
    / "exports/execution_capacity/20260727_pr_e/formal_execution_capacity.json",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"top_level_not_object:{path}")
    return value


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def evaluate(sources: dict[str, Path]) -> dict[str, Any]:
    expected = set(DEFAULT_SOURCES)
    missing_keys = sorted(expected - set(sources))
    checks: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    source_manifest: dict[str, Any] = {}
    for key in sorted(expected):
        path = sources.get(key)
        if path is None or not path.is_file():
            checks.append(
                {
                    "check": key,
                    "passed": False,
                    "actual": "MISSING",
                    "required": "technical evidence present and verified",
                }
            )
            continue
        try:
            payload = _load(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            checks.append(
                {
                    "check": key,
                    "passed": False,
                    "actual": f"INVALID:{exc}",
                    "required": "valid JSON technical evidence",
                }
            )
            continue
        payloads[key] = payload
        source_manifest[key] = {"path": _display_path(path), "sha256": _sha(path)}

    status_rules = {
        "pr_a_equivalence": lambda p: p.get("status") == "PASS",
        "pr_b_formal_readiness": lambda p: p.get("status") == "READY_FOR_FORMAL_RUN",
        "pr_c_formal_run": lambda p: p.get("status") == "VERIFIED"
        and bool(p.get("dual_ledger_results"))
        and all(
            item.get("status") == "VERIFIED"
            for item in p.get("dual_ledger_results", [])
        ),
        "pr_d_oos_robustness": lambda p: bool(p.get("technical_evidence_complete"))
        and p.get("status") in ("PASS", "ECONOMIC_FAILED"),
        "pr_e_execution_capacity": lambda p: bool(p.get("technical_evidence_complete"))
        and p.get("status") in {"PASS", "ECONOMIC_FAILED"},
    }
    for key, rule in status_rules.items():
        if key not in payloads:
            continue
        passed = bool(rule(payloads[key]))
        checks.append(
            {
                "check": key,
                "passed": passed,
                "actual": payloads[key].get("status", "UNKNOWN"),
                "required": "technical evidence complete",
            }
        )

    technical_complete = not missing_keys and len(payloads) == len(expected) and all(
        item["passed"] for item in checks
    )
    # Cross-validate formal_run_id identity across PR-C, PR-D, PR-E
    formal_run_ids: set[str] = set()
    for pr_key in ("pr_c_formal_run", "pr_d_oos_robustness", "pr_e_execution_capacity"):
        rid = str(payloads.get(pr_key, {}).get("formal_run_id") or "")
        if rid:
            formal_run_ids.add(rid)
    if len(formal_run_ids) > 1:
        technical_complete = False
        checks.append({
            "check": "pr_cross_formal_run_id_consistency",
            "passed": False,
            "actual": f"mismatched_ids={sorted(formal_run_ids)}",
            "required": "PR-C/PR-D/PR-E must share the same formal_run_id",
        })

    oos = payloads.get("pr_d_oos_robustness", {})
    capacity = payloads.get("pr_e_execution_capacity", {})
    economic_failed = (
        technical_complete
        and (
            oos.get("economic_gates_passed") is False
            or capacity.get("economic_gates_passed") is False
        )
    )
    decision = (
        "PR_I_TRIGGERED"
        if technical_complete and economic_failed
        else "PR_I_NOT_TRIGGERED"
    )
    blockers = sorted(
        {
            item["check"]
            for item in checks
            if not item["passed"]
        }
        | set(missing_keys)
    )
    if technical_complete and not economic_failed:
        blockers.append("economic_failure_not_established")
    result: dict[str, Any] = {
        "schema_version": "dynamic_champion_pr_i_trigger_v1",
        "decision": decision,
        "technical_evidence_complete": technical_complete,
        "economic_failure_only": economic_failed,
        "alpha_modified": False,
        "current_allowed_risk_capital_cny": 0,
        "broker_api_enabled": False,
        "checks": checks,
        "blockers": blockers,
        "source_manifest": source_manifest,
    }
    result["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    for key, default in DEFAULT_SOURCES.items():
        parser.add_argument(f"--{key.replace('_', '-')}", type=Path, default=default)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = {
        key: getattr(args, key)
        for key in DEFAULT_SOURCES
    }
    result = evaluate(sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
