"""Fail-closed PR-I Alpha v1.2 trigger evaluation.

PR-I is allowed only when all identity/data/ledger/execution evidence is
technically complete and the remaining failures are purely economic.  Missing
or blocked technical evidence always returns ``PR_I_NOT_TRIGGERED``.

Evidence paths are resolved from the formal evidence registry by default.
Legacy date-stamped paths are no longer supported as defaults.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from runtime.formal_contract import FORMAL_STRATEGIES, FORMAL_STRATEGY_SET, canonical_sha

DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "exports" / "formal_evidence_registry" / "active_formal_run.json"


def _load_registry_sources(registry_path: Path) -> dict[str, Path] | None:
    """Load PR-A through PR-E paths from the formal evidence registry.

    Returns None if the registry is missing, unparseable, or has no active run.
    """
    try:
        reg = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(reg, dict):
        return None

    mapping = {
        "pr_a_equivalence": reg.get("pr_a_path"),
        "pr_b_formal_readiness": reg.get("pr_b_path"),
        "pr_c_formal_run": reg.get("pr_c_path"),
        "pr_d_oos_robustness": reg.get("pr_d_path"),
        "pr_e_execution_capacity": reg.get("pr_e_path"),
    }
    # Only return if at least one path is set
    if not any(v for v in mapping.values()):
        return None

    return {
        key: PROJECT_ROOT / val
        for key, val in mapping.items()
        if isinstance(val, str) and val
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
        "pr_c_formal_run": lambda p: (
            p.get("status") == "VERIFIED"
            and isinstance(p.get("dual_ledger_results"), list)
            and len(p.get("dual_ledger_results", [])) == len(FORMAL_STRATEGIES)
            and all(isinstance(item, dict) for item in p.get("dual_ledger_results", []))
            and all(isinstance(item.get("strategy"), str) and item.get("strategy") for item in p.get("dual_ledger_results", []))
            and {item["strategy"] for item in p.get("dual_ledger_results", [])} == FORMAL_STRATEGY_SET
            and isinstance(p.get("strategy_ids"), list)
            and len(p.get("strategy_ids") or []) == len(FORMAL_STRATEGIES)
            and len(p.get("strategy_ids") or []) == len(set(p.get("strategy_ids") or []))
            and all(isinstance(s, str) and s for s in p.get("strategy_ids", []))
            and set(p.get("strategy_ids") or []) == FORMAL_STRATEGY_SET
            and all(
                item.get("status") == "VERIFIED"
                for item in p.get("dual_ledger_results", [])
            )
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

    # P0-2: Cross-validate formal_run_id — ALL must be present and identical
    run_ids: list[str | None] = []
    for pr_key in ("pr_c_formal_run", "pr_d_oos_robustness", "pr_e_execution_capacity"):
        rid = payloads.get(pr_key, {}).get("formal_run_id")
        run_ids.append(str(rid) if isinstance(rid, str) and rid else None)
    if any(x is None for x in run_ids):
        checks.append({
            "check": "pr_cross_formal_run_id_consistency",
            "passed": False,
            "actual": f"missing_ids={[pr for pr, rid in zip(('PR-C','PR-D','PR-E'), run_ids) if rid is None]}",
            "required": "PR-C/PR-D/PR-E must all have formal_run_id",
        })
    elif len(set(run_ids)) != 1:
        checks.append({
            "check": "pr_cross_formal_run_id_consistency",
            "passed": False,
            "actual": f"mismatched_ids={sorted(set(run_ids))}",
            "required": "PR-C/PR-D/PR-E must share the same formal_run_id",
        })

    # P0-3: Verify self-hash integrity for PR-C (manifest_sha256) and PR-D/E (evidence_sha256)
    HASH_FIELDS = {
        "pr_c_formal_run": "manifest_sha256",
        "pr_d_oos_robustness": "evidence_sha256",
        "pr_e_execution_capacity": "evidence_sha256",
    }
    for pr_key, hash_field in HASH_FIELDS.items():
        p = payloads.get(pr_key, {})
        declared_sha = str(p.get(hash_field) or "")
        if not declared_sha or len(declared_sha) != 64:
            checks.append({
                "check": f"{pr_key}_{hash_field}",
                "passed": False,
                "actual": "missing_or_invalid",
                "required": f"valid 64-char {hash_field}",
            })
        else:
            payload_without_sha = {k: v for k, v in p.items() if k != hash_field}
            try:
                computed = canonical_sha(payload_without_sha)
            except (TypeError, ValueError) as exc:
                checks.append({
                    "check": f"{pr_key}_{hash_field}",
                    "passed": False,
                    "actual": f"non_canonical_payload:{type(exc).__name__}",
                    "required": "finite canonical JSON payload",
                })
                continue
            if computed != declared_sha:
                checks.append({
                    "check": f"{pr_key}_{hash_field}",
                    "passed": False,
                    "actual": f"computed={computed[:16]}... != declared={declared_sha[:16]}...",
                    "required": f"{hash_field} matches canonical self-hash",
                })

    # PR-D/E must bind to PR-C's formal_manifest_sha256, frozen_bundle_sha256, acceptance_config_sha256
    BINDING_FIELDS = ("formal_manifest_sha256", "frozen_bundle_sha256", "acceptance_config_sha256")
    pr_c_binding = {
        "formal_manifest_sha256": payloads.get("pr_c_formal_run", {}).get("manifest_sha256"),
        "frozen_bundle_sha256": payloads.get("pr_c_formal_run", {}).get("frozen_bundle_sha256"),
        "acceptance_config_sha256": payloads.get("pr_c_formal_run", {}).get("acceptance_config_sha256"),
    }
    if pr_c_binding["formal_manifest_sha256"]:
        for pr_key in ("pr_d_oos_robustness", "pr_e_execution_capacity"):
            p_pr = payloads.get(pr_key, {})
            for field in BINDING_FIELDS:
                bound = str(p_pr.get(field) or "")
                expected_val = str(pr_c_binding[field] or "")
                if not bound:
                    checks.append({
                        "check": f"{pr_key}_{field}_binding",
                        "passed": False, "actual": "missing",
                        "required": f"must bind to PR-C {field}",
                    })
                elif not expected_val:
                    checks.append({
                        "check": f"{pr_key}_{field}_binding",
                        "passed": False, "actual": "pr_c_missing",
                        "required": f"PR-C missing {field}",
                    })
                elif bound != expected_val:
                    checks.append({
                        "check": f"{pr_key}_{field}_binding",
                        "passed": False,
                        "actual": f"bound={bound[:16]}... != pr_c={expected_val[:16]}...",
                        "required": f"PR-D/E {field} must equal PR-C value",
                    })

    # P0: Compute technical_complete ONCE after ALL checks (not before)
    technical_complete = not missing_keys and len(payloads) == len(expected) and all(
        item["passed"] for item in checks
    )

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
    parser.add_argument("--registry-path", type=Path, default=DEFAULT_REGISTRY_PATH)
    for key in ("pr_a_equivalence", "pr_b_formal_readiness", "pr_c_formal_run",
                 "pr_d_oos_robustness", "pr_e_execution_capacity"):
        parser.add_argument(f"--{key.replace('_', '-')}", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    # Resolve sources: CLI overrides take precedence over registry
    registry_sources = _load_registry_sources(args.registry_path) or {}
    sources = {}
    for key in ("pr_a_equivalence", "pr_b_formal_readiness", "pr_c_formal_run",
                 "pr_d_oos_robustness", "pr_e_execution_capacity"):
        cli_val = getattr(args, key)
        if cli_val is not None:
            sources[key] = cli_val
        elif key in registry_sources:
            sources[key] = registry_sources[key]

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
