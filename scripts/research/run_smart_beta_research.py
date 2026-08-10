#!/usr/bin/env python3
"""Research-plan runner for ``smart_beta_v1``.

This runner intentionally stops at a plan/diagnostic artifact.  It does not
invoke a broker, mutate a production release, promote a strategy or allocate
capital.  Historical 2022--2026 observations are labelled consumed; an
economic conclusion can only be attached to a future ``FORMAL_BLIND`` epoch
from ``config/forward_epochs.yaml``.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.advanced_statistical_validation import validate_nested_statistics
from scripts.research.experiment_registry import append_experiment, make_record
from scripts.research.orthogonal_factor_attribution import orthogonal_factor_attribution
from scripts.research.research_preregistration import (
    CONSUMED_DEVELOPMENT_SAMPLE,
    DEFAULT_FORWARD_EPOCHS,
    formal_blind_epoch_status,
    load_preregistration,
    validate_preregistration,
    validate_formal_evidence,
    verify_preregistration_source_bindings,
)


DEFAULT_CARD = PROJECT_ROOT / "config" / "alpha_challengers" / "smart_beta_v1.yaml"
DEFAULT_DEFINITION = PROJECT_ROOT / "config" / "strategy_definitions" / "smart_beta_v1.yaml"
SCHEMA_VERSION = "smart_beta_research_runner_v1"
STRATEGY_ID = "smart_beta_v1"


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"config_root_not_mapping:{path}")
    return dict(raw)


def build_smart_beta_plan(
    *,
    card_path: str | Path = DEFAULT_CARD,
    definition_path: str | Path = DEFAULT_DEFINITION,
    forward_epochs_path: str | Path = DEFAULT_FORWARD_EPOCHS,
) -> dict[str, Any]:
    """Load and validate the immutable plan without running statistics."""

    card = load_preregistration(card_path)
    validation = validate_preregistration(card)
    bindings = verify_preregistration_source_bindings(card, card_path=card_path)
    definition = _load_yaml(Path(definition_path))
    if str(card.get("strategy_id")) != STRATEGY_ID or str(definition.get("strategy_id")) != STRATEGY_ID:
        raise ValueError("smart_beta_strategy_identity_mismatch")
    if any("vls" in str(value).lower() for value in (card.get("strategy_id"), definition.get("strategy_id"))):
        raise ValueError("smart_beta_must_not_inherit_vls_identity")
    epoch = formal_blind_epoch_status(forward_epochs_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "hypothesis_id": str(card["hypothesis_id"]),
        "preregistration": validation,
        "source_bindings": bindings,
        "sample_policy": copy.deepcopy(card["sample_policy"]),
        "definition": {
            "formula": definition.get("formula"),
            "direction": definition.get("direction"),
            "hold_period": definition.get("hold_period"),
            "top_n": definition.get("top_n"),
            "risk_constraints": copy.deepcopy(definition.get("risk_constraints", {})),
            "cost_model": copy.deepcopy(definition.get("cost_model", {})),
            "benchmark": copy.deepcopy(definition.get("benchmark", {})),
        },
        "forward_epoch": epoch,
        "research_only": True,
        "promotion_status": "BLOCKED",
        "capital_cny": 0.0,
    }


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def run_smart_beta_research(
    *,
    card_path: str | Path = DEFAULT_CARD,
    definition_path: str | Path = DEFAULT_DEFINITION,
    forward_epochs_path: str | Path = DEFAULT_FORWARD_EPOCHS,
    returns: Sequence[float] | None = None,
    candidate_returns: Sequence[Sequence[float]] | None = None,
    factor_returns: Mapping[str, Sequence[float]] | None = None,
    data_status: str | None = None,
    e3_available: bool | None = None,
    pit_qualifier: Mapping[str, Any] | None = None,
    forward_evidence: Mapping[str, Any] | None = None,
    returns_dates: Sequence[str] | None = None,
    initial_capital: float = 0.0,
    output: str | Path | None = None,
    output_path: str | Path | None = None,
    registry_output: str | Path | None = None,
    seed: int = 20260810,
    n_bootstrap: int = 9999,
    n_permutations: int = 9999,
) -> dict[str, Any]:
    """Return a plan/diagnostic result; write only to explicit paths."""

    try:
        plan = build_smart_beta_plan(card_path=card_path, definition_path=definition_path, forward_epochs_path=forward_epochs_path)
    except Exception as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "strategy_id": STRATEGY_ID,
            "status": "BLOCKED",
            "conclusion": "BLOCKED_FORWARD_EVIDENCE",
            "reason": str(exc),
            "research_only": True,
            "promotion_status": "BLOCKED",
            "capital_cny": 0.0,
        }
        if output or output_path:
            _write_json(output or output_path, result)
        return result
    if float(initial_capital) != 0.0:
        return _finalize(
            {**plan, "status": "BLOCKED", "conclusion": "OBSERVE", "reason": "capital_change_forbidden", "capital_cny": 0.0},
            output=output or output_path,
            registry_output=registry_output,
            p_raw=None,
        )
    epoch = plan["forward_epoch"]
    if not epoch.get("formal_epoch"):
        result: dict[str, Any] = {
            **plan,
            "status": "BLOCKED_FORWARD_EVIDENCE",
            "conclusion": "BLOCKED_FORWARD_EVIDENCE",
            "reason": epoch.get("reason", "no_future_formal_blind_epoch"),
            "evidence_level": "E0",
            "data_status": "UNBOUND",
            "formal": False,
            "evidence_binding": {"status": "BLOCKED", "evidence_level": "E0", "reason": "no_formal_epoch"},
            "capital_cny": 0.0,
        }
        return _finalize(result, output=output or output_path, registry_output=registry_output, p_raw=None)
    evidence_binding = validate_formal_evidence(
        pit_qualifier=pit_qualifier,
        forward_evidence=forward_evidence,
        formal_epoch=epoch["formal_epoch"],
        returns_dates=returns_dates,
    )
    if evidence_binding.get("status") != "PASS":
        result = {
            **plan,
            "status": "OBSERVE",
            "conclusion": "OBSERVE",
            "reason": "independent_pit_or_forward_evidence_unbound",
            "evidence_level": "E0",
            "data_status": "UNBOUND",
            "formal": False,
            "evidence_binding": evidence_binding,
            "capital_cny": 0.0,
        }
        return _finalize(result, output=output or output_path, registry_output=registry_output, p_raw=None)
    diagnostics: dict[str, Any] = {}
    if returns is not None:
        diagnostics["statistics"] = validate_nested_statistics(
            returns,
            candidate_returns,
            n_trials=max(1, len(candidate_returns or [returns])),
            n_bootstrap=n_bootstrap,
            n_permutations=n_permutations,
            seed=seed,
        )
        if factor_returns is not None:
            diagnostics["attribution"] = orthogonal_factor_attribution(returns, factor_returns, strict=False)
    else:
        diagnostics["status"] = "OBSERVE"
        diagnostics["reason"] = "returns_not_supplied_plan_only"
    result = {
        **plan,
        "status": "OBSERVE",
        "conclusion": "OBSERVE",
        "reason": "formal_epoch_available_research_diagnostic_only",
        "evidence_level": "E3",
        "data_status": "E3_BOUND",
        "formal": False,
        "formal_evidence_bound": True,
        "evidence_binding": evidence_binding,
        "diagnostics": diagnostics,
        "sample_status": CONSUMED_DEVELOPMENT_SAMPLE,
        "capital_cny": 0.0,
    }
    return _finalize(result, output=output or output_path, registry_output=registry_output, p_raw=_extract_p(diagnostics))


def _extract_p(diagnostics: Mapping[str, Any]) -> float | None:
    stats = diagnostics.get("statistics") or {}
    value = (stats.get("permutation") or {}).get("p_value")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _finalize(
    result: dict[str, Any],
    *,
    output: str | Path | None,
    registry_output: str | Path | None,
    p_raw: float | None,
) -> dict[str, Any]:
    if registry_output is not None:
        row = make_record(
            hypothesis_id=str(result.get("hypothesis_id", "H-W4-SMART-BETA-001")),
            strategy_id=STRATEGY_ID,
            dataset_status=str(result.get("sample_status", CONSUMED_DEVELOPMENT_SAMPLE)),
            params={"strategy_id": STRATEGY_ID, "capital_cny": 0.0, "promotion_status": "BLOCKED"},
            tests=["nested_walk_forward", "bootstrap", "DSR", "CSCV_PBO", "BH_FDR", "permutation", "QR_attribution"],
            p_raw=p_raw,
            p_adjusted=None,
            decision="BLOCKED" if str(result.get("status")) == "BLOCKED_FORWARD_EVIDENCE" else "OBSERVE",
            sample_status=CONSUMED_DEVELOPMENT_SAMPLE,
            independent_oos=False,
            experiment_id="exp_wave4_smart_beta_v1",
        )
        result["registry"] = append_experiment(row, registry_output)
    if output is not None:
        _write_json(output, result)
        result["output"] = str(output)
    return result


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--registry-output", type=Path, default=None)
    parser.add_argument("--data-status", default=None)
    parser.add_argument("--e3-available", action="store_true")
    parser.add_argument("--returns", type=Path, default=None, help="JSON list; optional diagnostics")
    args = parser.parse_args()
    values = json.loads(args.returns.read_text(encoding="utf-8")) if args.returns else None
    result = run_smart_beta_research(
        returns=values,
        data_status=args.data_status,
        e3_available=args.e3_available,
        output=args.output,
        registry_output=args.registry_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") in {"OBSERVE", "PASS"} else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())


run_research = run_smart_beta_research

__all__ = ["STRATEGY_ID", "build_smart_beta_plan", "run_smart_beta_research", "run_research"]
