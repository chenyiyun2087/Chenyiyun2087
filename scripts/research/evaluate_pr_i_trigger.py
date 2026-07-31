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

from runtime.acceptance_config import canonical_sha

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


# v5.1.2: Required evidence layers for formal PR-I evaluation
DEFAULT_SOURCES = {
    "pr_a_equivalence",
    "pr_b_formal_readiness",
    "pr_c_formal_run",
    "pr_d_oos_robustness",
    "pr_e_execution_capacity",
}


def evaluate(sources: dict[str, Path]) -> dict[str, Any]:
    """Evaluate PR-I trigger using the v5.1 verify_pr_i_chain().

    v5.1.2: Splits chain_status (technical) from alpha_redesign_trigger
    (economic decision).  Guards against None paths that would crash
    verify_pr_i_chain.  Capital Firewall requires chain_status == PASS,
    NOT PR_I_TRIGGERED.
    """
    from runtime.pr_chain_binding import verify_pr_i_chain

    # Build source manifest for audit trail
    source_manifest: dict[str, Any] = {}
    for key, path in sorted(sources.items()):
        if path is not None and path.is_file():
            source_manifest[key] = {"path": _display_path(path), "sha256": _sha(path)}

    # Map source keys to PR-B/C/D/E paths
    pr_b_path = sources.get("pr_b_formal_readiness")
    pr_c_path = sources.get("pr_c_formal_run")
    pr_d_path = sources.get("pr_d_oos_robustness")
    pr_e_path = sources.get("pr_e_execution_capacity")

    # v5.1.2: Guard against None paths — don't pass None to verify_pr_i_chain
    path_blockers: list[str] = []
    for label, path in [("pr_b", pr_b_path), ("pr_c", pr_c_path),
                         ("pr_d", pr_d_path), ("pr_e", pr_e_path)]:
        if path is None or not path.is_file():
            path_blockers.append(f"{label}_missing_or_not_a_file")

    if path_blockers:
        return _build_pr_i_report(
            pr_chain_status="BLOCKED",
            blockers=path_blockers,
            economic_status="UNDETERMINED",
            alpha_redesign_trigger="NOT_TRIGGERED",
            technical_evidence_complete=False,
            source_manifest=source_manifest,
        )

    # Run the formal chain verifier (all paths are valid at this point)
    chain_result = verify_pr_i_chain(
        pr_b_path=pr_b_path,
        pr_c_path=pr_c_path,
        pr_d_path=pr_d_path,
        pr_e_path=pr_e_path,
    )

    chain_passed = chain_result["status"] == "PASS"
    blockers = sorted(chain_result.get("blockers", []))

    # Check if all required layers exist
    missing_keys = []
    for key in sorted(DEFAULT_SOURCES):
        path = sources.get(key)
        if path is None or not path.is_file():
            missing_keys.append(key)

    technical_complete = chain_passed and not missing_keys

    # v5.1.2: Split economic decision from chain status
    economic_failed = False
    if chain_passed and pr_d_path.is_file():
        try:
            pr_d = json.loads(pr_d_path.read_text(encoding="utf-8"))
            if pr_d.get("status") == "ECONOMIC_FAILED":
                economic_failed = True
        except (OSError, json.JSONDecodeError):
            pass
    if chain_passed and pr_e_path.is_file():
        try:
            pr_e = json.loads(pr_e_path.read_text(encoding="utf-8"))
            if pr_e.get("status") == "ECONOMIC_FAILED":
                economic_failed = True
        except (OSError, json.JSONDecodeError):
            pass

    return _build_pr_i_report(
        pr_chain_status="PASS" if chain_passed else "BLOCKED",
        blockers=blockers + missing_keys,
        economic_status="ECONOMIC_FAILED" if economic_failed else "PASS",
        alpha_redesign_trigger="TRIGGERED" if economic_failed else "NOT_TRIGGERED",
        technical_evidence_complete=technical_complete,
        source_manifest=source_manifest,
    )


def _build_pr_i_report(
    *,
    pr_chain_status: str,
    blockers: list[str],
    economic_status: str,
    alpha_redesign_trigger: str,
    technical_evidence_complete: bool,
    source_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build standardized PR-I evaluation report with split semantics.

    v5.1.2: Capital Firewall requires pr_chain_status == PASS AND
    economic_status == PASS.  alpha_redesign_trigger is a separate
    research decision signal, NOT a capital gate condition.
    """
    result: dict[str, Any] = {
        "schema_version": "pr_i_evaluation_v5_1_2",
        "pr_chain_status": pr_chain_status,
        "economic_status": economic_status,
        "alpha_redesign_trigger": alpha_redesign_trigger,
        "capital_chain_ready": pr_chain_status == "PASS" and economic_status == "PASS",
        "technical_evidence_complete": technical_evidence_complete,
        "alpha_modified": False,
        "current_allowed_risk_capital_cny": 0,
        "broker_api_enabled": False,
        "blockers": sorted(set(blockers)),
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
