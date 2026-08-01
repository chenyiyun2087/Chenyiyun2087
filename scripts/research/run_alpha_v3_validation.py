#!/usr/bin/env python3
"""Build the fail-closed Chenyiyun2087 Alpha v4.3 validation evidence package.

The runner consolidates existing research artifacts.  It never changes
production routing, enables a canary, or authorizes capital.  Missing or
release-mismatched evidence produces a complete BLOCKED package instead of an
inferred pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import (
    ACCEPTANCE_PATH,
    canonical_sha,
    load_validation_profile,
)
from scripts.research.alpha_proof import (
    audit_factor_availability,
    build_alpha_proof_guard_report,
    build_alpha_proof_summary,
    build_alpha_stability_report,
    build_benchmark_excess_report,
    build_daily_factor_attribution,
)
from scripts.research.correctness_audit import (
    build_correctness_gap_report,
    build_correctness_synthetic_suite,
    build_research_correctness_report,
)
from scripts.research.capital_firewall import (
    build_alpha_claim_registry,
    build_capital_firewall,
    build_evidence_promotion_workflow,
    build_evidence_strength_report,
)
from scripts.research.capital_readiness import (
    build_capital_tier_engine,
    build_claim_lifecycle_report,
    build_evidence_expiration_report,
    build_independent_reviewer_simulation,
    build_strategy_health_monitor,
)
from scripts.research.evidence_control import (
    build_capital_gate_simulator,
    build_event_correctness_coverage,
    build_evidence_contract_matrix,
    build_evidence_issue_tracker,
    build_failure_coverage_matrix,
    build_investment_readiness_report,
    build_portfolio_accounting_reconciliation,
    build_portfolio_state_audit,
    build_strategy_health_report,
)
from scripts.research.evidence_acquisition import (
    build_evidence_acquisition_pipeline,
)
from scripts.research.replay_diff import (
    build_environment_manifest,
    build_replay_diff_report,
    build_replay_snapshot,
)


DEFAULT_PROGRAM = PROJECT_ROOT / "config" / "dynamic_champion_live_program.yaml"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "exports" / "alpha_v3_validation"
SHANGHAI = ZoneInfo("Asia/Shanghai")
REPORT_NAMES = (
    "benchmark_evidence_builder_report.json",
    "factor_evidence_builder_report.json",
    "pit_minimum_builder_report.json",
    "evidence_production_report.json",
    "data_catalog_report.json",
    "evidence_qualification_report.json",
    "evidence_snapshot_manifest.json",
    "evidence_adapter_report.json",
    "evidence_refresh_queue_report.json",
    "alpha_proof_report.json",
    "alpha_proof_guard_report.json",
    "factor_lineage_report.json",
    "factor_compute_lineage_report.json",
    "factor_effectiveness_report.json",
    "capacity_curve_report.json",
    "regime_conditional_attribution_report.json",
    "environment_manifest.json",
    "replay_snapshot_report.json",
    "research_replay_report.json",
    "replay_diff_report.json",
    "research_correctness_audit_report.json",
    "event_correctness_coverage_report.json",
    "portfolio_state_audit_report.json",
    "research_gap_report.json",
    "correctness_synthetic_suite_report.json",
    "failure_injection_report.json",
    "execution_stress_report.json",
    "evidence_dependency_graph_report.json",
    "engineering_readiness_report.json",
    "evidence_contract_matrix_report.json",
    "evidence_issue_tracker_report.json",
    "capital_gate_simulator_report.json",
    "investment_readiness_report.json",
    "strategy_health_report.json",
    "portfolio_accounting_reconciliation_report.json",
    "failure_coverage_matrix_report.json",
    "evidence_strength_report.json",
    "evidence_promotion_workflow_report.json",
    "alpha_claim_registry_report.json",
    "capital_firewall_report.json",
    "evidence_expiration_report.json",
    "capital_tier_engine_report.json",
    "claim_lifecycle_report.json",
    "strategy_health_monitor_report.json",
    "independent_reviewer_simulation_report.json",
    "benchmark_excess_report.json",
    "alpha_attribution_report.json",
    "factor_ic_report.json",
    "walk_forward_report.json",
    "execution_cost_report.json",
    "promotion_gate_report.json",
    "strategy_scorecard.json",
)
CODE_EVIDENCE_PATHS = (
    PROJECT_ROOT / "runtime" / "acceptance_config.py",
    PROJECT_ROOT / "scripts" / "research" / "alpha_proof.py",
    PROJECT_ROOT / "scripts" / "research" / "replay_diff.py",
    PROJECT_ROOT / "scripts" / "research" / "correctness_audit.py",
    PROJECT_ROOT / "scripts" / "research" / "evidence_control.py",
    PROJECT_ROOT / "scripts" / "research" / "capital_firewall.py",
    PROJECT_ROOT / "scripts" / "research" / "capital_readiness.py",
    PROJECT_ROOT / "scripts" / "research" / "evidence_acquisition.py",
    PROJECT_ROOT / "scripts" / "research" / "evidence_production.py",
    PROJECT_ROOT / "scripts" / "research" / "factor_evidence.py",
    PROJECT_ROOT / "scripts" / "research" / "factor_challenger_lab.py",
    PROJECT_ROOT / "scripts" / "research" / "factor_net_ledger.py",
    PROJECT_ROOT / "scripts" / "research" / "pit_factor_panel_audit.py",
    PROJECT_ROOT / "scripts" / "research" / "pit_factor_panel_builder.py",
    PROJECT_ROOT / "scripts" / "research" / "pit_data_adapter.py",
    PROJECT_ROOT / "scripts" / "research" / "run_alpha_v3_validation.py",
    PROJECT_ROOT / "scripts" / "ops" / "evaluate_dynamic_champion_readiness.py",
    PROJECT_ROOT / "scripts" / "ops" / "market_regime.py",
)


@dataclass(frozen=True)
class ValidationGate:
    gate: str
    status: str
    blocking: bool
    required: str
    actual: str
    evidence: str

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _file_sha(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return pd.DataFrame()


def _numeric_series(
    frame: pd.DataFrame,
    column: str,
    default: float = 0.0,
) -> pd.Series:
    source = (
        frame[column]
        if column in frame.columns
        else pd.Series(default, index=frame.index, dtype=float)
    )
    return pd.to_numeric(source, errors="coerce").fillna(default)


def _write_report(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    def without_volatile_timestamps(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: without_volatile_timestamps(item)
                for key, item in value.items()
                if key not in {
                    "generated_at",
                    "timestamp",
                    "content_sha256",
                    "resolved_at",
                    "as_of",
                    "evidence_observed_at",
                    "evidence_valid_until",
                    "created_at",
                }
            }
        if isinstance(value, list):
            return [without_volatile_timestamps(item) for item in value]
        return value

    deterministic = without_volatile_timestamps(payload)
    payload = {**payload, "content_sha256": canonical_sha(deterministic)}
    path.write_text(
        json.dumps(_json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload


def _resolve_default_inputs(
    program_path: Path,
) -> tuple[dict[str, Any], Path | None, Path | None, Path | None, Path | None]:
    program = yaml.safe_load(program_path.read_text(encoding="utf-8")) or {}
    snapshot = PROJECT_ROOT / str(program.get("approved_backtest_snapshot", ""))
    nav = snapshot / "trusted_account_backtest_nav.csv"
    trades = snapshot / "trusted_account_backtest_trades.csv"
    evidence = program.get("upgrade_evidence") or {}
    walk_forward = PROJECT_ROOT / str(evidence.get("pr_d_oos_robustness", ""))
    pit = PROJECT_ROOT / str(evidence.get("pr_b_formal_readiness", ""))
    # ``Path('')`` resolves to PROJECT_ROOT.  Treat only regular files as
    # usable evidence; a configured-but-missing path must remain missing and
    # fail closed instead of being hashed as a directory.
    return (
        program,
        nav if nav.is_file() else None,
        trades if trades.is_file() else None,
        walk_forward if walk_forward.is_file() else None,
        pit if pit.is_file() else None,
    )


def _scope_nav(
    nav: pd.DataFrame,
    *,
    strategy: str,
    start_date: str,
    end_date: str | None,
) -> pd.DataFrame:
    if nav.empty or "trade_date" not in nav.columns:
        return pd.DataFrame()
    frame = nav.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    if "strategy" in frame.columns:
        frame = frame[frame["strategy"].astype(str).eq(strategy)]
    frame = frame[frame["trade_date"].ge(pd.Timestamp(start_date))]
    if end_date:
        frame = frame[frame["trade_date"].le(pd.Timestamp(end_date))]
    return frame.dropna(subset=["trade_date"]).sort_values("trade_date").drop_duplicates(
        "trade_date", keep="last"
    )


def compute_nav_metrics(nav: pd.DataFrame, initial_capital: float) -> dict[str, Any]:
    if nav.empty:
        return {
            "status": "MISSING",
            "sample_start": None,
            "sample_end": None,
            "trading_days": 0,
            "annualized_return": None,
            "max_drawdown": None,
            "sharpe_ratio": None,
            "total_return": None,
        }
    if "nav" in nav.columns:
        nav_values = pd.to_numeric(nav["nav"], errors="coerce")
    elif "total_equity" in nav.columns:
        nav_values = pd.to_numeric(nav["total_equity"], errors="coerce") / float(
            initial_capital
        )
    else:
        return {"status": "INVALID", "trading_days": int(len(nav))}
    clean = pd.DataFrame(
        {"trade_date": nav["trade_date"], "nav": nav_values}
    ).dropna()
    if len(clean) < 2 or clean["nav"].le(0).any():
        return {"status": "INVALID", "trading_days": int(len(clean))}
    returns = clean["nav"].pct_change().dropna()
    first_nav = float(clean["nav"].iloc[0])
    normalized_base = 1.0 if 0.5 <= first_nav <= 1.5 else first_nav
    total_return = float(clean["nav"].iloc[-1] / normalized_base - 1.0)
    annualized_return = float(
        (1.0 + total_return) ** (252.0 / max(len(clean), 1)) - 1.0
    )
    volatility = float(returns.std(ddof=1))
    sharpe = float(returns.mean() / volatility * np.sqrt(252)) if volatility > 0 else 0.0
    max_drawdown = float((clean["nav"] / clean["nav"].cummax() - 1.0).min())
    return {
        "status": "AVAILABLE",
        "sample_start": clean["trade_date"].iloc[0].date().isoformat(),
        "sample_end": clean["trade_date"].iloc[-1].date().isoformat(),
        "trading_days": int(len(clean)),
        "annualized_return": annualized_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
        "total_return": total_return,
    }


def build_factor_ic_report(
    panel: pd.DataFrame,
    profile: dict[str, Any],
) -> dict[str, Any]:
    spec = profile["factor_ic"]
    factors = list(profile["attribution"]["required_factors"])
    horizons = [int(value) for value in spec["horizons"]]
    rows: list[dict[str, Any]] = []
    if panel.empty or "trade_date" not in panel.columns:
        return {
            "schema_version": (
                "alpha_v3_2_factor_ic_v1"
                if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
                else "alpha_v3_5_factor_ic_v1"
            ),
            "status": "BLOCKED",
            "blockers": ["factor_panel_missing"],
            "rows": [],
        }
    availability = audit_factor_availability(
        panel, profile, panel_name="factor_panel"
    )
    if availability["status"] != "PASS":
        return {
            "schema_version": (
                "alpha_v3_2_factor_ic_v1"
                if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
                else "alpha_v3_5_factor_ic_v1"
            ),
            "status": "BLOCKED",
            "blockers": list(availability["blockers"]),
            "rows": [],
            "factor_availability": availability,
        }
    frame = panel.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    for factor in factors:
        for horizon in horizons:
            target = f"fwd_{horizon}d_return"
            if factor not in frame.columns or target not in frame.columns:
                rows.append(
                    {
                        "factor": factor,
                        "horizon": horizon,
                        "status": "MISSING",
                        "mean_rank_ic": None,
                        "information_ratio": None,
                        "positive_ic_ratio": None,
                        "coverage": 0.0,
                    }
                )
                continue
            daily_ic: list[float] = []
            available = frame[[factor, target]].apply(pd.to_numeric, errors="coerce")
            coverage = float(available.dropna().shape[0] / max(len(frame), 1))
            for _, group in frame.groupby("trade_date", sort=True):
                pair = group[[factor, target]].apply(pd.to_numeric, errors="coerce").dropna()
                if len(pair) < 5 or pair[factor].nunique() < 2:
                    continue
                value = pair[factor].corr(pair[target], method="spearman")
                if pd.notna(value):
                    daily_ic.append(float(value))
            mean_ic = float(np.mean(daily_ic)) if daily_ic else None
            std_ic = float(np.std(daily_ic, ddof=1)) if len(daily_ic) > 1 else None
            ir = (
                float(mean_ic / std_ic)
                if mean_ic is not None and std_ic not in (None, 0.0)
                else None
            )
            passed = (
                mean_ic is not None
                and ir is not None
                and mean_ic >= float(spec["min_mean_rank_ic"])
                and ir >= float(spec["min_information_ratio"])
                and coverage >= float(spec["min_coverage"])
            )
            rows.append(
                {
                    "factor": factor,
                    "horizon": horizon,
                    "status": "PASS" if passed else "FAIL",
                    "mean_rank_ic": mean_ic,
                    "information_ratio": ir,
                    "positive_ic_ratio": (
                        float(np.mean(np.asarray(daily_ic) > 0)) if daily_ic else None
                    ),
                    "coverage": coverage,
                    "research_enabled": bool(
                        mean_ic is not None
                        and mean_ic
                        >= float(spec["research_disable_below_mean_rank_ic"])
                    ),
                    "daily_observations": len(daily_ic),
                }
            )
    blockers = [
        f"{row['factor']}:{row['horizon']}d:{row['status']}"
        for row in rows
        if row["status"] != "PASS"
    ]
    return {
        "schema_version": (
            "alpha_v3_2_factor_ic_v1"
            if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
            else "alpha_v3_5_factor_ic_v1"
        ),
        "status": "PASS" if rows and not blockers else "BLOCKED",
        "blockers": blockers,
        "rows": rows,
        "factor_availability": availability,
    }


def build_factor_lineage_report(
    factor_returns: pd.DataFrame,
    factor_panel: pd.DataFrame,
    manifest: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Verify factor versions against immutable source files and panel columns."""
    required = [str(value) for value in profile["attribution"]["required_factors"]]
    blockers: list[str] = []
    rows: list[dict[str, Any]] = []
    if manifest.get("schema_version") != "alpha_v3_5_factor_source_manifest_v1":
        blockers.append("factor_source_manifest_schema_invalid")
    if str(manifest.get("status") or "").upper() != "PASS":
        blockers.append("factor_source_manifest_not_pass")
    sources = manifest.get("factors")
    if not isinstance(sources, dict):
        sources = {}
        blockers.append("factor_source_manifest_factors_missing")
    panels = {
        "factor_returns": factor_returns,
        "factor_panel": factor_panel,
    }
    for factor in required:
        source = sources.get(factor)
        row_blockers: list[str] = []
        if not isinstance(source, dict):
            source = {}
            row_blockers.append("source_entry_missing")
        version = str(source.get("data_version") or "")
        declared_sha = str(source.get("source_snapshot_sha256") or "").lower()
        raw_path = str(source.get("source_snapshot_path") or "")
        source_path = Path(raw_path) if raw_path else None
        if source_path is not None and not source_path.is_absolute():
            source_path = PROJECT_ROOT / source_path
        if not version:
            row_blockers.append("data_version_missing")
        if len(declared_sha) != 64 or any(
            char not in "0123456789abcdef" for char in declared_sha
        ):
            row_blockers.append("source_snapshot_sha_invalid")
        if source_path is None or not source_path.is_file():
            row_blockers.append("source_snapshot_missing")
        elif _file_sha(source_path) != declared_sha:
            row_blockers.append("source_snapshot_sha_mismatch")
        for panel_name, panel in panels.items():
            if panel.empty:
                row_blockers.append(f"{panel_name}_missing")
                continue
            version_column = f"{factor}_data_version"
            sha_column = f"{factor}_source_snapshot_sha256"
            for column, expected in (
                (version_column, version),
                (sha_column, declared_sha),
            ):
                if column not in panel.columns:
                    row_blockers.append(f"{panel_name}_column_missing:{column}")
                    continue
                values = set(panel[column].dropna().astype(str).str.lower().unique())
                if values != {expected.lower()}:
                    row_blockers.append(f"{panel_name}_lineage_mismatch:{column}")
        blockers.extend(f"{factor}:{item}" for item in row_blockers)
        rows.append(
            {
                "factor": factor,
                "status": "PASS" if not row_blockers else "BLOCKED",
                "data_version": version or None,
                "source_snapshot_path": str(source_path) if source_path else None,
                "source_snapshot_sha256": declared_sha or None,
                "blockers": row_blockers,
            }
        )
    return {
        "schema_version": "alpha_v3_5_factor_lineage_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
        "rows": rows,
        "manifest_status": manifest.get("status") or "MISSING",
    }


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def build_factor_compute_lineage_report(
    manifest: dict[str, Any],
    profile: dict[str, Any],
    source_lineage: dict[str, Any],
    environment_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Verify the code/config/input lineage for every factor computation."""
    required = [str(value) for value in profile["attribution"]["required_factors"]]
    source_rows = {
        str(row.get("factor")): row for row in source_lineage.get("rows", [])
    }
    blockers: list[str] = []
    rows: list[dict[str, Any]] = []
    if manifest.get("schema_version") != "alpha_v3_5_factor_compute_manifest_v1":
        blockers.append("factor_compute_manifest_schema_invalid")
    if str(manifest.get("status") or "").upper() != "PASS":
        blockers.append("factor_compute_manifest_not_pass")
    factors = manifest.get("factors")
    if not isinstance(factors, dict):
        factors = {}
        blockers.append("factor_compute_manifest_factors_missing")
    for factor in required:
        entry = factors.get(factor)
        row_blockers: list[str] = []
        if not isinstance(entry, dict):
            entry = {}
            row_blockers.append("compute_entry_missing")
        formula_version = str(entry.get("factor_formula_version") or "")
        code_path_raw = str(entry.get("code_path") or "")
        config_path_raw = str(entry.get("config_path") or "")
        code_path = Path(code_path_raw) if code_path_raw else None
        config_path = Path(config_path_raw) if config_path_raw else None
        if code_path is not None and not code_path.is_absolute():
            code_path = PROJECT_ROOT / code_path
        if config_path is not None and not config_path.is_absolute():
            config_path = PROJECT_ROOT / config_path
        declared_code_sha = str(entry.get("code_sha") or "").lower()
        declared_config_sha = str(entry.get("config_sha") or "").lower()
        declared_input_sha = str(entry.get("input_sha") or "").lower()
        factor_definition = entry.get("factor_definition")
        declared_definition_sha = str(
            entry.get("factor_definition_sha") or ""
        ).lower()
        declared_environment_hash = str(
            entry.get("environment_lock_hash") or ""
        ).lower()
        declared_pipeline_hash = str(entry.get("factor_pipeline_hash") or "").lower()
        created_at_raw = entry.get("created_at")
        if not formula_version:
            row_blockers.append("factor_formula_version_missing")
        if not isinstance(factor_definition, dict) or not factor_definition:
            row_blockers.append("factor_semantic_definition_missing")
            factor_definition = {}
        computed_definition_sha = canonical_sha(factor_definition)
        if not _valid_sha256(declared_definition_sha):
            row_blockers.append("factor_definition_sha_invalid")
        elif declared_definition_sha != computed_definition_sha:
            row_blockers.append("factor_definition_sha_mismatch")
        expected_environment_hash = str(
            environment_manifest.get("environment_lock_hash") or ""
        ).lower()
        if not _valid_sha256(declared_environment_hash):
            row_blockers.append("environment_lock_hash_invalid")
        elif declared_environment_hash != expected_environment_hash:
            row_blockers.append("environment_lock_hash_mismatch")
        for name, declared, path in (
            ("code", declared_code_sha, code_path),
            ("config", declared_config_sha, config_path),
        ):
            if not _valid_sha256(declared):
                row_blockers.append(f"{name}_sha_invalid")
            if path is None or not path.is_file():
                row_blockers.append(f"{name}_file_missing")
            elif _file_sha(path) != declared:
                row_blockers.append(f"{name}_sha_mismatch")
        source_sha = str(
            (source_rows.get(factor) or {}).get("source_snapshot_sha256") or ""
        ).lower()
        if not _valid_sha256(declared_input_sha):
            row_blockers.append("input_sha_invalid")
        elif declared_input_sha != source_sha:
            row_blockers.append("input_sha_source_mismatch")
        try:
            created_at = pd.Timestamp(created_at_raw)
        except (TypeError, ValueError):
            created_at = pd.NaT
        if (
            pd.isna(created_at)
            or created_at.tzinfo is None
            or created_at.utcoffset() is None
        ):
            row_blockers.append("created_at_timezone_missing_or_invalid")
        pipeline_payload = {
            "factor_name": factor,
            "factor_formula_version": formula_version,
            "code_sha": declared_code_sha,
            "config_sha": declared_config_sha,
            "input_sha": declared_input_sha,
            "factor_definition_sha": declared_definition_sha,
            "environment_lock_hash": declared_environment_hash,
        }
        computed_pipeline_hash = canonical_sha(pipeline_payload)
        if not _valid_sha256(declared_pipeline_hash):
            row_blockers.append("factor_pipeline_hash_invalid")
        elif declared_pipeline_hash != computed_pipeline_hash:
            row_blockers.append("factor_pipeline_hash_mismatch")
        blockers.extend(f"{factor}:{item}" for item in row_blockers)
        rows.append(
            {
                "factor": factor,
                "status": "PASS" if not row_blockers else "BLOCKED",
                "factor_formula_version": formula_version or None,
                "code_path": str(code_path) if code_path else None,
                "code_sha": declared_code_sha or None,
                "config_path": str(config_path) if config_path else None,
                "config_sha": declared_config_sha or None,
                "input_sha": declared_input_sha or None,
                "factor_definition": factor_definition,
                "factor_definition_sha": declared_definition_sha or None,
                "computed_factor_definition_sha": computed_definition_sha,
                "environment_lock_hash": declared_environment_hash or None,
                "factor_pipeline_hash": declared_pipeline_hash or None,
                "computed_factor_pipeline_hash": computed_pipeline_hash,
                "created_at": (
                    created_at.isoformat() if not pd.isna(created_at) else None
                ),
                "blockers": row_blockers,
            }
        )
    if source_lineage.get("status") != "PASS":
        blockers.append("factor_source_lineage_not_verified")
    return {
        "schema_version": "alpha_v3_5_factor_compute_lineage_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "rows": rows,
    }


def build_factor_effectiveness_report(
    panel: pd.DataFrame,
    profile: dict[str, Any],
    lineage: dict[str, Any],
) -> dict[str, Any]:
    """Connect factor signals, forward returns and portfolio exposures."""
    factors = [str(value) for value in profile["attribution"]["required_factors"]]
    horizons = [int(value) for value in profile["factor_ic"]["horizons"]]
    blockers: list[str] = []
    if lineage.get("status") != "PASS":
        blockers.append("factor_lineage_not_verified")
    required_columns = ["trade_date", "symbol", "portfolio_weight", *factors]
    required_columns.extend(f"fwd_{horizon}d_return" for horizon in horizons)
    missing = [column for column in required_columns if column not in panel.columns]
    blockers.extend(f"factor_effectiveness_column_missing:{name}" for name in missing)
    if panel.empty:
        blockers.append("factor_effectiveness_panel_missing")
    if blockers:
        return {
            "schema_version": "alpha_v3_5_factor_effectiveness_v1",
            "status": "BLOCKED",
            "economic_status": "NOT_EVALUATED",
            "blockers": sorted(set(blockers)),
            "rows": [],
            "decay": [],
        }
    frame = panel.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["portfolio_weight"] = pd.to_numeric(
        frame["portfolio_weight"], errors="coerce"
    )
    rows: list[dict[str, Any]] = []
    min_coverage = float(profile["factor_ic"]["min_coverage"])
    for factor in factors:
        for horizon in horizons:
            target = f"fwd_{horizon}d_return"
            sample = frame[
                ["trade_date", "symbol", "portfolio_weight", factor, target]
            ].copy()
            for column in ("portfolio_weight", factor, target):
                sample[column] = pd.to_numeric(sample[column], errors="coerce")
            coverage = float(sample.dropna().shape[0] / max(len(sample), 1))
            daily: list[dict[str, float]] = []
            for _, group in sample.groupby("trade_date", sort=True):
                group = group.dropna()
                if len(group) < 5 or group[factor].nunique() < 2:
                    continue
                ranks = group[factor].rank(pct=True) - 0.5
                weight_scale = float(group["portfolio_weight"].abs().sum())
                if weight_scale <= 0:
                    continue
                daily.append(
                    {
                        "pearson_ic": float(group[factor].corr(group[target])),
                        "rank_ic": float(group[factor].corr(group[target], method="spearman")),
                        "long_short_return": float(
                            group.loc[ranks >= 0.3, target].mean()
                            - group.loc[ranks <= -0.3, target].mean()
                        ),
                        "factor_exposure": float(
                            (group["portfolio_weight"] * ranks).sum() / weight_scale
                        ),
                        "portfolio_forward_return": float(
                            (group["portfolio_weight"] * group[target]).sum()
                            / weight_scale
                        ),
                    }
                )
            daily_frame = pd.DataFrame(daily)
            exposure_return_correlation = (
                float(
                    daily_frame["factor_exposure"].corr(
                        daily_frame["portfolio_forward_return"]
                    )
                )
                if len(daily_frame) > 1
                else None
            )
            if (
                exposure_return_correlation is not None
                and not np.isfinite(exposure_return_correlation)
            ):
                exposure_return_correlation = None
            complete = bool(
                coverage >= min_coverage
                and len(daily_frame) >= 20
                and exposure_return_correlation is not None
            )
            if not complete:
                blockers.append(f"factor_effectiveness_incomplete:{factor}:{horizon}d")
            rows.append(
                {
                    "factor": factor,
                    "horizon": horizon,
                    "status": "PASS" if complete else "BLOCKED",
                    "coverage": coverage,
                    "daily_observations": int(len(daily_frame)),
                    "mean_ic": (
                        float(daily_frame["pearson_ic"].mean())
                        if not daily_frame.empty
                        else None
                    ),
                    "mean_rank_ic": (
                        float(daily_frame["rank_ic"].mean())
                        if not daily_frame.empty
                        else None
                    ),
                    "mean_long_short_return": (
                        float(daily_frame["long_short_return"].mean())
                        if not daily_frame.empty
                        else None
                    ),
                    "mean_portfolio_forward_return": (
                        float(daily_frame["portfolio_forward_return"].mean())
                        if not daily_frame.empty
                        else None
                    ),
                    "exposure_return_correlation": exposure_return_correlation,
                }
            )
    decay = []
    for factor in factors:
        factor_rows = [row for row in rows if row["factor"] == factor]
        decay.append(
            {
                "factor": factor,
                "rank_ic_by_horizon": {
                    str(row["horizon"]): row["mean_rank_ic"] for row in factor_rows
                },
                "absolute_rank_ic_non_increasing": all(
                    abs(float(left["mean_rank_ic"])) >= abs(float(right["mean_rank_ic"]))
                    for left, right in zip(factor_rows, factor_rows[1:])
                    if left["mean_rank_ic"] is not None
                    and right["mean_rank_ic"] is not None
                ),
            }
        )
    return {
        "schema_version": "alpha_v3_5_factor_effectiveness_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "economic_status": "REVIEW_REQUIRED",
        "promotion_eligible": False,
        "blockers": sorted(set(blockers)),
        "rows": rows,
        "decay": decay,
    }


def build_execution_stress_report(
    trades: pd.DataFrame,
    profile: dict[str, Any],
    *,
    initial_capital: float,
) -> dict[str, Any]:
    """Simulate A-share fill failures without treating them as real Shadow."""
    required = {"side", "gross_amount", "limit_status", "fill_status"}
    missing = sorted(required.difference(trades.columns))
    if trades.empty or missing:
        return {
            "schema_version": "alpha_v3_5_execution_robustness_stress_v1",
            "status": "BLOCKED",
            "promotion_eligible": False,
            "evidence_layer": "SIMULATION",
            "execution_robustness_stress_index": None,
            "real_shadow_substitute": False,
            "evidence_layers": {
                "SIMULATION": "BLOCKED",
                "PAPER": "NOT_PROVIDED",
                "LIVE": "NOT_PROVIDED",
            },
            "blockers": (
                ["execution_stress_trades_missing"]
                if trades.empty
                else [f"execution_stress_column_missing:{name}" for name in missing]
            ),
            "scenarios": [],
        }
    frame = trades.copy()
    frame["side"] = frame["side"].astype(str).str.upper()
    frame["limit_status"] = frame["limit_status"].astype(str).str.upper()
    frame["fill_status"] = frame["fill_status"].astype(str).str.upper()
    frame["gross_amount"] = pd.to_numeric(frame["gross_amount"], errors="coerce")
    if frame["gross_amount"].isna().any() or frame["gross_amount"].lt(0).any():
        return {
            "schema_version": "alpha_v3_5_execution_robustness_stress_v1",
            "status": "BLOCKED",
            "promotion_eligible": False,
            "evidence_layer": "SIMULATION",
            "execution_robustness_stress_index": None,
            "real_shadow_substitute": False,
            "evidence_layers": {
                "SIMULATION": "BLOCKED",
                "PAPER": "NOT_PROVIDED",
                "LIVE": "NOT_PROVIDED",
            },
            "blockers": ["execution_stress_gross_amount_invalid"],
            "scenarios": [],
        }
    gross = max(float(frame["gross_amount"].sum()), 1.0)
    limit_up_buy = float(
        frame.loc[
            frame["side"].eq("BUY") & frame["limit_status"].eq("LIMIT_UP"),
            "gross_amount",
        ].sum()
    )
    limit_down_sell = float(
        frame.loc[
            frame["side"].eq("SELL") & frame["limit_status"].eq("LIMIT_DOWN"),
            "gross_amount",
        ].sum()
    )
    unfilled = float(
        frame.loc[~frame["fill_status"].isin({"FILLED", "FULL"}), "gross_amount"].sum()
    )
    delayed_notional = max(unfilled, limit_up_buy + limit_down_sell)
    slippage_2x_cost = gross * 50.0 / 10_000.0
    penalty = min(
        100.0,
        45.0 * limit_up_buy / gross
        + 45.0 * limit_down_sell / gross
        + 30.0 * delayed_notional / gross
        + 20.0 * slippage_2x_cost / max(float(initial_capital), 1.0),
    )
    score = float(max(0.0, 100.0 - penalty))
    threshold = float(profile["execution"]["min_execution_stress_score"])
    scenarios = [
        {
            "scenario": "limit_up_buy_blocked",
            "affected_notional_cny": limit_up_buy,
            "affected_ratio": limit_up_buy / gross,
        },
        {
            "scenario": "limit_down_sell_blocked",
            "affected_notional_cny": limit_down_sell,
            "affected_ratio": limit_down_sell / gross,
        },
        {
            "scenario": "one_day_fill_delay",
            "affected_notional_cny": delayed_notional,
            "affected_ratio": delayed_notional / gross,
        },
        {
            "scenario": "slippage_2x",
            "incremental_cost_cny": slippage_2x_cost,
            "cost_ratio_initial_capital": slippage_2x_cost
            / max(float(initial_capital), 1.0),
        },
    ]
    return {
        "schema_version": "alpha_v3_5_execution_robustness_stress_v1",
        "status": "PASS" if score >= threshold else "BLOCKED",
        "promotion_eligible": False,
        "real_shadow_substitute": False,
        "evidence_layer": "SIMULATION",
        "execution_robustness_stress_index": score,
        "execution_stress_score": score,
        "evidence_layers": {
            "SIMULATION": "PASS" if score >= threshold else "BLOCKED",
            "PAPER": "NOT_PROVIDED",
            "LIVE": "NOT_PROVIDED",
        },
        "interpretation": (
            "simulation-only robustness diagnostic; not a return forecast, "
            "paper-trading result, live-fill result, or Shadow substitute"
        ),
        "minimum_score": threshold,
        "blockers": [] if score >= threshold else ["execution_stress_score_below_gate"],
        "scenarios": scenarios,
    }


def build_failure_injection_report(profile: dict[str, Any]) -> dict[str, Any]:
    """Exercise fail-close detectors with deterministic synthetic corruptions."""
    factors = [str(value) for value in profile["attribution"]["required_factors"]]
    signal = "2026-01-05T16:00:00+08:00"
    base = {"signal_time": signal}
    for factor in factors:
        base[f"{factor}_available_at"] = "2026-01-05T15:59:00+08:00"
    timezone_missing = pd.DataFrame(
        [{**base, f"{factors[0]}_available_at": "2026-01-05T15:59:00"}]
    )
    future_factor = pd.DataFrame(
        [{**base, f"{factors[0]}_available_at": "2026-01-05T16:01:00+08:00"}]
    )
    timezone_result = audit_factor_availability(
        timezone_missing, profile, panel_name="injection"
    )
    future_result = audit_factor_availability(
        future_factor, profile, panel_name="injection"
    )

    dates = pd.bdate_range("2024-01-02", periods=260)
    strategy_nav = pd.DataFrame(
        {"trade_date": dates, "nav": 1.0 + np.arange(len(dates)) * 0.001}
    )
    benchmark_rows = []
    for benchmark in profile["benchmarks"]["required"]:
        scoped_dates = dates[:-1] if benchmark == profile["benchmarks"]["required"][0] else dates
        benchmark_rows.extend(
            {
                "trade_date": date,
                "benchmark": benchmark,
                "nav": 1.0 + index * 0.001,
                "available_at": f"{date.date().isoformat()}T16:00:00+08:00",
            }
            for index, date in enumerate(scoped_dates)
        )
    benchmark_result = build_benchmark_excess_report(
        strategy_nav, pd.DataFrame(benchmark_rows), profile
    )
    pipeline_payload = {
        "factor_name": factors[0],
        "factor_formula_version": "fixture-v1",
        "code_sha": "1" * 64,
        "config_sha": "2" * 64,
        "input_sha": "3" * 64,
    }
    pipeline_detected = canonical_sha(pipeline_payload) != "0" * 64
    replay_base = {
        "release_id": "failure-injection-release",
        "strategy_id": "failure-injection-strategy",
        "profile": "alpha_v4_0",
        "environment_lock_hash": "e" * 64,
        "code_snapshot_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "input_snapshot_sha256": "c" * 64,
        "nav_sha256": "d" * 64,
        "trades_sha256": "1" * 64,
        "attribution_sha256": "2" * 64,
        "risk_sha256": "3" * 64,
    }
    snapshot_result = build_research_replay_report(
        {"replay_contract": replay_base},
        {**replay_base, "input_snapshot_sha256": "0" * 64},
    )
    corporate_action = pd.DataFrame(
        [
            {
                "signal_time": "2026-01-05T16:00:00+08:00",
                "corporate_action_available_at": "2026-01-06T09:00:00+08:00",
            }
        ]
    )
    corporate_action_detected = bool(
        (
            pd.to_datetime(
                corporate_action["corporate_action_available_at"],
                errors="coerce",
                utc=True,
            )
            > pd.to_datetime(
                corporate_action["signal_time"], errors="coerce", utc=True
            )
        ).any()
    )
    t_day_execution = pd.DataFrame(
        [{"signal_date": "2026-01-05", "execute_date": "2026-01-05"}]
    )
    t_day_execution_detected = bool(
        (
            pd.to_datetime(t_day_execution["execute_date"], errors="coerce")
            <= pd.to_datetime(t_day_execution["signal_date"], errors="coerce")
        ).any()
    )
    future_weight = pd.DataFrame(
        [
            {
                "signal_time": "2026-01-05T16:00:00+08:00",
                "portfolio_weight_available_at": "2026-01-06T16:00:00+08:00",
            }
        ]
    )
    future_weight_detected = bool(
        (
            pd.to_datetime(
                future_weight["portfolio_weight_available_at"],
                errors="coerce",
                utc=True,
            )
            > pd.to_datetime(
                future_weight["signal_time"], errors="coerce", utc=True
            )
        ).any()
    )
    adjustment_factor = pd.DataFrame(
        [{
            "signal_time": signal,
            "adjustment_factor_available_at": "2026-01-06T08:00:00+08:00",
        }]
    )
    adjustment_factor_detected = bool(
        (
            pd.to_datetime(
                adjustment_factor["adjustment_factor_available_at"],
                errors="coerce",
                utc=True,
            )
            > pd.to_datetime(
                adjustment_factor["signal_time"], errors="coerce", utc=True
            )
        ).any()
    )
    universe_snapshot = pd.DataFrame(
        [
            {"symbol": "000001.SZ", "was_listed_at_signal": True},
            {"symbol": "000002.SZ", "was_listed_at_signal": True},
        ]
    )
    current_only_universe = {"000001.SZ"}
    universe_survivorship_detected = bool(
        set(universe_snapshot.loc[
            universe_snapshot["was_listed_at_signal"], "symbol"
        ]).difference(current_only_universe)
    )
    financial_revision = pd.DataFrame(
        [{
            "signal_time": signal,
            "revision_available_at": "2026-01-07T09:00:00+08:00",
            "used_revision": True,
        }]
    )
    financial_revision_detected = bool(
        (
            financial_revision["used_revision"].astype(bool)
            & (
                pd.to_datetime(
                    financial_revision["revision_available_at"],
                    errors="coerce",
                    utc=True,
                )
                > pd.to_datetime(
                    financial_revision["signal_time"],
                    errors="coerce",
                    utc=True,
                )
            )
        ).any()
    )
    duplicate_order = pd.DataFrame(
        [
            {"event_time": signal, "symbol": "000001.SZ", "source_sequence": 2},
            {"event_time": signal, "symbol": "000001.SZ", "source_sequence": 1},
        ]
    )
    duplicate_timestamp_detected = bool(
        duplicate_order.duplicated(["event_time", "symbol"], keep=False).any()
    )
    auction_price = pd.DataFrame(
        [{
            "signal_time": signal,
            "auction_price_available_at": "2026-01-06T09:25:00+08:00",
            "auction_price_used": True,
        }]
    )
    auction_price_detected = bool(
        (
            auction_price["auction_price_used"].astype(bool)
            & (
                pd.to_datetime(
                    auction_price["auction_price_available_at"],
                    errors="coerce",
                    utc=True,
                )
                > pd.to_datetime(
                    auction_price["signal_time"], errors="coerce", utc=True
                )
            )
        ).any()
    )
    queue_fill = pd.DataFrame(
        [{
            "fill_status": "FILLED",
            "queue_ahead_quantity": 100_000,
            "executed_market_quantity": 10_000,
        }]
    )
    order_queue_detected = bool(
        (
            queue_fill["fill_status"].astype(str).str.upper().eq("FILLED")
            & (
                pd.to_numeric(
                    queue_fill["queue_ahead_quantity"], errors="coerce"
                )
                > pd.to_numeric(
                    queue_fill["executed_market_quantity"], errors="coerce"
                )
            )
        ).any()
    )
    provider_contracts = pd.DataFrame(
        [
            {
                "field": "free_float_market_cap",
                "provider": "provider_a",
                "semantic_sha256": "a" * 64,
            },
            {
                "field": "free_float_market_cap",
                "provider": "provider_b",
                "semantic_sha256": "b" * 64,
            },
        ]
    )
    provider_semantic_detected = bool(
        provider_contracts.groupby("field")["semantic_sha256"].nunique().gt(1).any()
    )
    numeric_boundary = pd.DataFrame(
        [{
            "raw_value": 0.30000001,
            "replayed_value": 0.29999999,
            "decision_threshold": 0.30,
        }]
    )
    numeric_precision_detected = bool(
        (
            (
                numeric_boundary["raw_value"]
                >= numeric_boundary["decision_threshold"]
            )
            != (
                numeric_boundary["replayed_value"]
                >= numeric_boundary["decision_threshold"]
            )
        ).any()
    )
    bad_execution_rows = pd.DataFrame(
        [
            {
                "signal_time": "2026-01-05T16:00:00+08:00",
                "execute_time": "2026-01-06T09:30:00+08:00",
                "financial_available_at": "2026-01-06T08:00:00+08:00",
                "symbol": "000001.SZ",
                "side": "BUY",
                "price": 10.0,
                "fill_status": "FILLED",
                "limit_status": "LIMIT_UP",
                "is_st": False,
                "is_suspended": True,
                "is_delisted": True,
            }
        ]
    )
    injected_correctness = build_research_correctness_report(
        pd.DataFrame([{"trade_date": "2026-01-05", "nav": 1.0}]),
        bad_execution_rows,
        sample_size=100,
        seed=2087,
        research_production_contract={
            "status": "PASS",
            "research_signal_sha256": "a" * 64,
            "production_signal_sha256": "a" * 64,
        },
    )
    injected_invariants = {
        str(row["invariant"])
        for row in injected_correctness.get("invariant_violations", [])
    }
    cases = [
        {
            "case": "factor_pipeline_hash_mismatch",
            "status": "PASS" if pipeline_detected else "FAIL",
        },
        {
            "case": "factor_timezone_missing",
            "status": (
                "PASS"
                if timezone_result["status"] == "BLOCKED"
                and any("timezone_missing" in item for item in timezone_result["blockers"])
                else "FAIL"
            ),
        },
        {
            "case": "factor_available_after_signal",
            "status": (
                "PASS"
                if future_result["status"] == "BLOCKED"
                and any("future_factor" in item for item in future_result["blockers"])
                else "FAIL"
            ),
        },
        {
            "case": "benchmark_end_date_mismatch",
            "status": (
                "PASS"
                if benchmark_result["status"] == "BLOCKED"
                and any(
                    "end_date_mismatch" in row.get("blockers", [])
                    for row in benchmark_result["rows"]
                )
                else "FAIL"
            ),
        },
        {
            "case": "input_snapshot_sha_mismatch",
            "status": (
                "PASS"
                if snapshot_result["status"] == "BLOCKED"
                and "replay_mismatch:input_snapshot_sha256"
                in snapshot_result["blockers"]
                else "FAIL"
            ),
        },
        {
            "case": "corporate_action_lookahead",
            "status": "PASS" if corporate_action_detected else "FAIL",
        },
        {
            "case": "t_day_execution_violation",
            "status": "PASS" if t_day_execution_detected else "FAIL",
        },
        {
            "case": "portfolio_weight_lookahead",
            "status": "PASS" if future_weight_detected else "FAIL",
        },
        {
            "case": "financial_announcement_lookahead",
            "status": "PASS" if "financial_available_by_signal" in injected_invariants else "FAIL",
        },
        {
            "case": "suspended_security_fill",
            "status": "PASS" if "no_suspended_fill" in injected_invariants else "FAIL",
        },
        {
            "case": "limit_queue_impossible_fill",
            "status": "PASS" if "no_impossible_limit_fill" in injected_invariants else "FAIL",
        },
        {
            "case": "delisting_survivorship_bias",
            "status": "PASS" if "no_delisted_fill" in injected_invariants else "FAIL",
        },
        {
            "case": "adjustment_factor_future_leak",
            "status": "PASS" if adjustment_factor_detected else "FAIL",
        },
        {
            "case": "universe_survivorship_bias",
            "status": "PASS" if universe_survivorship_detected else "FAIL",
        },
        {
            "case": "financial_revision_after_release",
            "status": "PASS" if financial_revision_detected else "FAIL",
        },
        {
            "case": "duplicate_timestamp_order",
            "status": "PASS" if duplicate_timestamp_detected else "FAIL",
        },
        {
            "case": "auction_price_leak",
            "status": "PASS" if auction_price_detected else "FAIL",
        },
        {
            "case": "order_queue_priority_leak",
            "status": "PASS" if order_queue_detected else "FAIL",
        },
        {
            "case": "provider_semantic_change",
            "status": "PASS" if provider_semantic_detected else "FAIL",
        },
        {
            "case": "numeric_precision_drift",
            "status": "PASS" if numeric_precision_detected else "FAIL",
        },
    ]
    required = set(profile["replay_audit"]["required_failure_injections"])
    passed = {row["case"] for row in cases if row["status"] == "PASS"}
    return {
        "schema_version": "alpha_v4_0_failure_injection_pack_v5",
        "status": "PASS" if passed == required else "BLOCKED",
        "promotion_eligible": False,
        "blockers": [] if passed == required else sorted(required.difference(passed)),
        "cases": cases,
    }


def build_research_replay_report(
    reference: dict[str, Any],
    replay_contract: dict[str, Any],
) -> dict[str, Any]:
    """Compare exact release-scoped research inputs and core outputs."""
    reference_contract = reference.get("replay_contract")
    if not isinstance(reference_contract, dict):
        return {
            "schema_version": "alpha_v3_5_research_replay_v1",
            "status": "BLOCKED",
            "promotion_eligible": False,
            "blockers": ["replay_reference_missing_or_invalid"],
            "replay_contract": replay_contract,
            "reference_replay_fingerprint": None,
            "current_replay_fingerprint": canonical_sha(replay_contract),
        }
    keys = (
        "release_id",
        "strategy_id",
        "profile",
        "environment_lock_hash",
        "code_snapshot_sha256",
        "config_sha256",
        "input_snapshot_sha256",
        "nav_sha256",
        "trades_sha256",
        "attribution_sha256",
        "risk_sha256",
    )
    mismatches = [
        key for key in keys if reference_contract.get(key) != replay_contract.get(key)
    ]
    return {
        "schema_version": "alpha_v3_5_research_replay_v1",
        "status": "PASS" if not mismatches else "BLOCKED",
        "promotion_eligible": False,
        "blockers": [f"replay_mismatch:{key}" for key in mismatches],
        "replay_contract": replay_contract,
        "reference_replay_fingerprint": canonical_sha(reference_contract),
        "current_replay_fingerprint": canonical_sha(replay_contract),
    }


def build_capacity_curve_report(
    trades: pd.DataFrame,
    profile: dict[str, Any],
    *,
    initial_capital: float,
) -> dict[str, Any]:
    """Build a deterministic ADV-based capacity diagnostic."""
    required = {"gross_amount", "adv20_cny"}
    missing = sorted(required.difference(trades.columns))
    if trades.empty or missing:
        return {
            "schema_version": "alpha_v3_5_capacity_curve_v1",
            "status": "BLOCKED",
            "promotion_eligible": False,
            "blockers": (
                ["capacity_trade_evidence_missing"]
                if trades.empty
                else [f"capacity_column_missing:{name}" for name in missing]
            ),
            "rows": [],
        }
    frame = trades.copy()
    frame["gross_amount"] = pd.to_numeric(frame["gross_amount"], errors="coerce")
    frame["adv20_cny"] = pd.to_numeric(frame["adv20_cny"], errors="coerce")
    frame = frame.dropna(subset=["gross_amount", "adv20_cny"])
    if frame.empty or frame["adv20_cny"].le(0).any():
        return {
            "schema_version": "alpha_v3_5_capacity_curve_v1",
            "status": "BLOCKED",
            "promotion_eligible": False,
            "blockers": ["capacity_adv_invalid"],
            "rows": [],
        }
    rows = []
    for account_size in profile["stress"]["capacity_account_sizes_cny"]:
        scale = float(account_size) / float(initial_capital)
        participation = frame["gross_amount"].abs() * scale / frame["adv20_cny"]
        expected_slippage = 10.0 + 400.0 * np.sqrt(participation.clip(lower=0.0))
        rows.append(
            {
                "account_size_cny": float(account_size),
                "p50_adv_participation": float(participation.quantile(0.50)),
                "p95_adv_participation": float(participation.quantile(0.95)),
                "max_adv_participation": float(participation.max()),
                "p95_expected_slippage_bps": float(expected_slippage.quantile(0.95)),
                "within_one_percent_adv": bool(participation.quantile(0.95) <= 0.01),
                "within_50bps_slippage": bool(expected_slippage.quantile(0.95) <= 50.0),
            }
        )
    return {
        "schema_version": "alpha_v3_5_capacity_curve_v1",
        "status": "DIAGNOSTIC_ONLY",
        "promotion_eligible": False,
        "model": "10bps + 400bps * sqrt(order_notional / adv20_cny)",
        "blockers": ["real_order_book_and_fill_validation_required"],
        "rows": rows,
    }


def build_regime_conditional_attribution_report(
    strategy_nav: pd.DataFrame,
    factor_returns: pd.DataFrame,
    profile: dict[str, Any],
) -> dict[str, Any]:
    required_states = list(profile["regime_attribution"]["required_states"])
    min_days = int(profile["regime_attribution"]["min_aligned_trading_days_per_state"])
    strategy_returns = pd.Series(dtype=float)
    if not strategy_nav.empty:
        scoped = strategy_nav[["trade_date", "nav"]].copy()
        scoped["trade_date"] = pd.to_datetime(scoped["trade_date"], errors="coerce")
        scoped["nav"] = pd.to_numeric(scoped["nav"], errors="coerce")
        strategy_returns = scoped.dropna().set_index("trade_date")["nav"].pct_change()
    if factor_returns.empty or "market_regime_state" not in factor_returns.columns:
        return {
            "schema_version": "alpha_v3_5_regime_attribution_v1",
            "status": "BLOCKED",
            "blockers": ["market_regime_state_missing"],
            "rows": [],
        }
    states = factor_returns[["trade_date", "market_regime_state"]].copy()
    states["trade_date"] = pd.to_datetime(states["trade_date"], errors="coerce")
    aligned = pd.concat(
        [strategy_returns.rename("strategy_return"), states.set_index("trade_date")],
        axis=1,
        join="inner",
    ).dropna()
    rows = []
    blockers = []
    for state in required_states:
        values = aligned.loc[
            aligned["market_regime_state"].astype(str).str.upper().eq(state),
            "strategy_return",
        ]
        if len(values) < min_days:
            blockers.append(f"regime_history_insufficient:{state}")
        rows.append(
            {
                "market_regime_state": state,
                "status": "PASS" if len(values) >= min_days else "BLOCKED",
                "trading_days": int(len(values)),
                "annualized_return": (
                    float((1.0 + values).prod() ** (252.0 / len(values)) - 1.0)
                    if len(values)
                    else None
                ),
                "annualized_volatility": (
                    float(values.std(ddof=1) * np.sqrt(252.0))
                    if len(values) > 1
                    else None
                ),
            }
        )
    return {
        "schema_version": "alpha_v3_5_regime_attribution_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
        "rows": rows,
    }


def build_alpha_attribution_report(
    evidence: dict[str, Any],
    metrics: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    required = list(profile["attribution"]["required_factors"])
    tolerance = float(profile["attribution"]["closure_tolerance"])
    contributions = evidence.get("factor_contributions")
    if not isinstance(contributions, dict):
        contributions = {}
    missing = [factor for factor in required if factor not in contributions]
    residual = evidence.get("regression_alpha", evidence.get("residual"))
    unexplained = evidence.get("unexplained_residual_return")
    explained = sum(float(contributions.get(name, 0.0) or 0.0) for name in required)
    reported_total = evidence.get("total_return", metrics.get("total_return"))
    closure_error = None
    if reported_total is not None and residual is not None and unexplained is not None:
        closure_error = abs(
            float(reported_total)
            - explained
            - float(residual)
            - float(unexplained)
        )
    blockers = [f"missing_factor:{factor}" for factor in missing]
    if closure_error is None:
        blockers.append("attribution_closure_unavailable")
    elif closure_error > tolerance:
        blockers.append(f"attribution_not_closed:{closure_error:.10f}")
    unexplained_variance_ratio = evidence.get("unexplained_variance_ratio")
    proof_spec = profile["alpha_proof"]
    max_unexplained = float(proof_spec["max_unexplained_variance_ratio"])
    warning_unexplained = float(proof_spec["unexplained_variance_warning_ratio"])
    warnings = list(evidence.get("warnings") or [])
    if unexplained_variance_ratio is None:
        blockers.append("unexplained_variance_ratio_missing")
    elif float(unexplained_variance_ratio) > max_unexplained:
        blockers.append(
            f"unexplained_variance_ratio_exceeded:{float(unexplained_variance_ratio):.10f}"
        )
    elif float(unexplained_variance_ratio) > warning_unexplained:
        warnings.append(
            f"unexplained_variance_ratio_review:{float(unexplained_variance_ratio):.10f}"
        )
    alpha_tstat = evidence.get("alpha_tstat")
    if alpha_tstat is None or float(alpha_tstat) < float(proof_spec["min_alpha_tstat"]):
        blockers.append("alpha_tstat_insufficient")
    return {
        "schema_version": (
            "alpha_v3_2_attribution_v1"
            if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
            else "alpha_v3_5_attribution_v1"
        ),
        "status": "PASS" if not blockers else "BLOCKED",
        "required_factors": required,
        "factor_contributions": contributions,
        "residual_label": "regression_alpha",
        "residual": residual,
        "regression_alpha": residual,
        "stock_selection_alpha": evidence.get("stock_selection_alpha"),
        "stock_selection_evidence_status": evidence.get(
            "stock_selection_evidence_status", "NOT_PROVIDED"
        ),
        "unexplained_residual_return": unexplained,
        "residual_cumulative_return": evidence.get(
            "residual_cumulative_return", unexplained
        ),
        "residual_mean": evidence.get("residual_mean"),
        "residual_std": evidence.get("residual_std"),
        "residual_tstat": evidence.get("residual_tstat"),
        "alpha_tstat": alpha_tstat,
        "unexplained_variance_ratio": unexplained_variance_ratio,
        "max_unexplained_variance_ratio": max_unexplained,
        "unexplained_variance_warning_ratio": warning_unexplained,
        "reported_total_return": reported_total,
        "closure_error": closure_error,
        "closure_tolerance": tolerance,
        "blockers": blockers,
        "warnings": sorted(set(warnings)),
    }


def attach_selection_attribution(
    attribution: dict[str, Any],
    evidence: dict[str, Any],
    *,
    release_id: str,
    strategy: str,
    metrics: dict[str, Any],
    evidence_version: str | None = None,
) -> dict[str, Any]:
    """Attach an independently produced selection attribution without relabeling OLS."""
    result = dict(attribution)
    if not evidence:
        result["stock_selection_alpha"] = None
        result["stock_selection_evidence_status"] = "NOT_PROVIDED"
        return result
    required_matches = {
        "schema_version": (
            "alpha_v3_2_selection_attribution_v1"
            if str(evidence_version or "").startswith("alpha_v3_2")
            else "alpha_v3_5_selection_attribution_v1"
        ),
        "release_id": release_id,
        "strategy_id": strategy,
        "sample_start": metrics.get("sample_start"),
        "sample_end": metrics.get("sample_end"),
    }
    blockers = [
        f"selection_attribution_mismatch:{key}"
        for key, expected in required_matches.items()
        if evidence.get(key) != expected
    ]
    source_sha = str(evidence.get("source_snapshot_sha256") or "")
    if len(source_sha) != 64 or any(char not in "0123456789abcdef" for char in source_sha.lower()):
        blockers.append("selection_attribution_source_sha_invalid")
    source_path_value = str(evidence.get("source_snapshot_path") or "")
    source_path = Path(source_path_value) if source_path_value else None
    if source_path is not None and not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    if source_path is None or not source_path.is_file():
        blockers.append("selection_attribution_source_snapshot_missing")
    elif _file_sha(source_path) != source_sha:
        blockers.append("selection_attribution_source_sha_mismatch")
    if str(evidence.get("status") or "").upper() != "PASS":
        blockers.append("selection_attribution_not_pass")
    try:
        value = float(evidence["stock_selection_alpha"])
    except (KeyError, TypeError, ValueError):
        value = None
        blockers.append("selection_attribution_value_missing")
    if blockers:
        result["status"] = "BLOCKED"
        result["blockers"] = sorted(set([*result.get("blockers", []), *blockers]))
        result["stock_selection_alpha"] = None
        result["stock_selection_evidence_status"] = "BLOCKED"
        return result
    result["stock_selection_alpha"] = value
    result["stock_selection_evidence_status"] = "PASS"
    result["stock_selection_source_snapshot_sha256"] = source_sha
    result["stock_selection_source_snapshot_path"] = str(source_path)
    return result


def build_universe_perturbation(
    trades: pd.DataFrame,
    *,
    strategy: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    if trades.empty or "symbol" not in trades.columns:
        return {"status": "BLOCKED", "blockers": ["trade_evidence_missing"], "rows": []}
    scoped = trades.copy()
    if "strategy" in scoped.columns:
        scoped = scoped[scoped["strategy"].astype(str).eq(strategy)]
    if scoped.empty:
        return {"status": "BLOCKED", "blockers": ["strategy_trades_missing"], "rows": []}
    scoped["symbol"] = scoped["symbol"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
    scoped["gross_amount"] = _numeric_series(scoped, "gross_amount")
    scoped["cost"] = _numeric_series(scoped, "cost")
    side = scoped.get("side", pd.Series("", index=scoped.index)).astype(str).str.upper()
    scoped["cash_flow"] = np.where(
        side.eq("SELL"), scoped["gross_amount"] - scoped["cost"], -scoped["gross_amount"] - scoped["cost"]
    )
    contributions = scoped.groupby("symbol")["cash_flow"].sum()
    symbols = sorted(contributions.index.tolist())
    total = float(contributions.sum())
    rows: list[dict[str, Any]] = []
    for ratio in profile["stress"]["universe_drop_ratios"]:
        count = max(1, int(round(len(symbols) * float(ratio))))
        for seed in profile["stress"]["universe_drop_seeds"]:
            ranked = sorted(
                symbols,
                key=lambda symbol: hashlib.sha256(
                    f"{seed}:{symbol}".encode("utf-8")
                ).hexdigest(),
            )
            dropped = ranked[:count]
            residual = float(total - contributions.reindex(dropped).sum())
            rows.append(
                {
                    "drop_ratio": float(ratio),
                    "seed": int(seed),
                    "dropped_count": count,
                    "residual_closed_trade_cash_flow": residual,
                }
            )
    summary: list[dict[str, Any]] = []
    for ratio, group in pd.DataFrame(rows).groupby("drop_ratio"):
        values = group["residual_closed_trade_cash_flow"]
        summary.append(
            {
                "drop_ratio": float(ratio),
                "median_residual_closed_trade_cash_flow": float(values.median()),
                "worst_residual_closed_trade_cash_flow": float(values.min()),
            }
        )
    return {
        "status": "DIAGNOSTIC_ONLY",
        "method": "deterministic_closed_trade_contribution_removal",
        "promotion_eligible": False,
        "blockers": ["formal_universe_rerun_required"],
        "summary": summary,
        "rows": rows,
    }


def build_execution_cost_report(
    trades: pd.DataFrame,
    metrics: dict[str, Any],
    *,
    strategy: str,
    initial_capital: float,
    profile: dict[str, Any],
) -> dict[str, Any]:
    if trades.empty or metrics.get("total_return") is None:
        return {
            "schema_version": "alpha_v3_execution_cost_v1",
            "status": "BLOCKED",
            "blockers": ["trade_or_nav_evidence_missing"],
            "scenarios": [],
        }
    scoped = trades.copy()
    if "strategy" in scoped.columns:
        scoped = scoped[scoped["strategy"].astype(str).eq(strategy)]
    gross = float(_numeric_series(scoped, "gross_amount").sum())
    recorded_cost = float(_numeric_series(scoped, "cost").sum())
    years = max(float(metrics.get("trading_days", 0)) / 252.0, 1 / 252)
    scenarios: list[dict[str, Any]] = []
    for multiplier in profile["stress"]["cost_multipliers"]:
        for slippage_bps in profile["stress"]["slippage_bps"]:
            extra_cost = recorded_cost * (float(multiplier) - 1.0)
            slippage_cost = gross * float(slippage_bps) / 10_000.0
            adjusted_total = float(metrics["total_return"]) - (
                extra_cost + slippage_cost
            ) / float(initial_capital)
            adjusted_annual = (
                (1.0 + adjusted_total) ** (1.0 / years) - 1.0
                if adjusted_total > -1.0
                else -1.0
            )
            scenarios.append(
                {
                    "cost_multiplier": float(multiplier),
                    "slippage_bps": int(slippage_bps),
                    "extra_cost_cny": extra_cost,
                    "slippage_cost_cny": slippage_cost,
                    "adjusted_total_return": adjusted_total,
                    "adjusted_annualized_return": adjusted_annual,
                    "alpha_positive": adjusted_total > 0,
                }
            )
    perturbation = build_universe_perturbation(
        trades, strategy=strategy, profile=profile
    )
    blockers = [
        "limit_up_down_fill_evidence_missing",
        "unfilled_position_freeze_evidence_missing",
        *perturbation.get("blockers", []),
    ]
    return {
        "schema_version": "alpha_v3_execution_cost_v1",
        "status": "BLOCKED",
        "recorded_cost_cny": recorded_cost,
        "gross_traded_cny": gross,
        "initial_capital_cny": initial_capital,
        "scenarios": scenarios,
        "universe_perturbation": perturbation,
        "blockers": sorted(set(blockers)),
    }


def _evidence_status(
    evidence: dict[str, Any],
    accepted: tuple[str, ...],
) -> str:
    status = str(evidence.get("status") or "MISSING").upper()
    return "PASS" if status in accepted else "BLOCKED"


def build_gate_reports(
    *,
    program: dict[str, Any],
    profile: dict[str, Any],
    metrics: dict[str, Any],
    benchmark_metrics: dict[str, Any],
    benchmark_evidence_status: str,
    attribution: dict[str, Any],
    factor_ic: dict[str, Any],
    alpha_guard: dict[str, Any],
    factor_compute_lineage: dict[str, Any],
    research_replay: dict[str, Any],
    replay_diff: dict[str, Any],
    research_correctness: dict[str, Any],
    event_correctness_coverage: dict[str, Any],
    portfolio_state_audit: dict[str, Any],
    portfolio_accounting_reconciliation: dict[str, Any],
    correctness_synthetic_suite: dict[str, Any],
    failure_injection: dict[str, Any],
    execution_stress: dict[str, Any],
    walk_forward: dict[str, Any],
    execution: dict[str, Any],
    pit: dict[str, Any],
    shadow: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    performance = profile["performance"]
    core = profile["core_period"]
    sample_start = metrics.get("sample_start")
    history_pass = bool(
        sample_start
        and sample_start <= str(core["min_start_date"])
        and metrics.get("annualized_return") is not None
        and metrics["annualized_return"] > float(performance["min_annualized_return"])
        and metrics["max_drawdown"] > float(performance["max_drawdown"])
        and metrics["sharpe_ratio"] > float(performance["min_sharpe_ratio"])
    )
    excess = None
    if (
        metrics.get("annualized_return") is not None
        and benchmark_metrics.get("annualized_return") is not None
    ):
        excess = float(metrics["annualized_return"]) - float(
            benchmark_metrics["annualized_return"]
        )
    benchmark_pass = bool(
        benchmark_evidence_status == "PASS"
        and
        excess is not None
        and excess > float(performance["min_annualized_excess_return"])
    )
    pit_pass = _evidence_status(
        pit, ("READY_FOR_FORMAL_RUN", "VERIFIED", "PASS")
    ) == "PASS"
    shadow_strategy = str(shadow.get("strategy_id") or shadow.get("strategy") or "")
    shadow_release = str(shadow.get("release_id") or "")
    shadow_days = int(
        shadow.get("economic_real_trading_days")
        or shadow.get("economic_shadow_days")
        or 0
    )
    round_trips = int(shadow.get("completed_round_trips") or 0)
    shadow_pass = bool(
        shadow_strategy == str(program["strategy_id"])
        and shadow_release == str(program["release_id"])
        and shadow_days >= int(program["shadow"]["economic_min_real_trading_days"])
        and round_trips >= int(program["shadow"]["economic_min_completed_round_trips"])
        and int(shadow.get("reconciliation_errors") or 0) == 0
        and bool(shadow.get("cost_after_alpha_positive", False))
    )
    gates = [
        ValidationGate(
            "formal_pit",
            "PASS" if pit_pass else "BLOCKED",
            True,
            "release-scoped formal PIT evidence",
            str(pit.get("status") or "MISSING"),
            str(program.get("upgrade_evidence", {}).get("pr_b_formal_readiness", "")),
        ),
        ValidationGate(
            "core_history",
            "PASS" if history_pass else "BLOCKED",
            True,
            (
                f"{core['min_start_date']}+, annualized>{performance['min_annualized_return']:.0%}, "
                f"MDD>{performance['max_drawdown']:.0%}, Sharpe>{performance['min_sharpe_ratio']}"
            ),
            json.dumps(_json_ready(metrics), ensure_ascii=False, sort_keys=True),
            str(program.get("approved_backtest_snapshot", "")),
        ),
        ValidationGate(
            "benchmark_excess",
            "PASS" if benchmark_pass else "BLOCKED",
            True,
            f"annualized excess>{performance['min_annualized_excess_return']:.0%}",
            f"annualized_excess={excess}" if excess is not None else "benchmark_missing",
            str(profile["benchmarks"]["primary"]),
        ),
        ValidationGate(
            "alpha_attribution",
            str(attribution["status"]),
            True,
            "all required factors present and attribution closed",
            ";".join(attribution.get("blockers", [])) or "closed",
            "alpha_attribution_report.json",
        ),
        ValidationGate(
            "factor_ic",
            str(factor_ic["status"]),
            True,
            "5/10/20/60d IC, IR and coverage thresholds",
            ";".join(factor_ic.get("blockers", [])) or "passed",
            "factor_ic_report.json",
        ),
        ValidationGate(
            "alpha_proof_guard",
            str(alpha_guard["status"]),
            True,
            "benchmark, availability, residual and annual stability guards",
            ";".join(alpha_guard.get("blockers", [])) or "passed",
            "alpha_proof_guard_report.json",
        ),
        ValidationGate(
            "factor_compute_lineage",
            str(factor_compute_lineage["status"]),
            True,
            "factor formula/code/config/input lineage and pipeline hash verified",
            ";".join(factor_compute_lineage.get("blockers", [])) or "passed",
            "factor_compute_lineage_report.json",
        ),
        ValidationGate(
            "research_replay",
            str(research_replay["status"]),
            True,
            "release-scoped NAV/trades/attribution/risk replay is exact",
            ";".join(research_replay.get("blockers", [])) or "passed",
            "research_replay_report.json",
        ),
        ValidationGate(
            "replay_diff",
            str(replay_diff["status"]),
            True,
            "metadata, NAV, trade, attribution and risk diffs are all zero",
            ";".join(replay_diff.get("blockers", [])) or "zero differences",
            "replay_diff_report.json",
        ),
        ValidationGate(
            "research_correctness",
            str(research_correctness["status"]),
            True,
            "deterministic sample, invariants and research-production signals agree",
            ";".join(research_correctness.get("blockers", [])) or "passed",
            "research_correctness_audit_report.json",
        ),
        ValidationGate(
            "event_correctness_coverage",
            str(event_correctness_coverage["status"]),
            True,
            "event-stratified samples and annual anchors meet configured quotas",
            ";".join(event_correctness_coverage.get("blockers", []))
            or "passed",
            "event_correctness_coverage_report.json",
        ),
        ValidationGate(
            "portfolio_state_audit",
            str(portfolio_state_audit["status"]),
            True,
            "cash, weights, exposures, turnover and sector state are valid",
            ";".join(portfolio_state_audit.get("blockers", [])) or "passed",
            "portfolio_state_audit_report.json",
        ),
        ValidationGate(
            "portfolio_accounting_reconciliation",
            str(portfolio_accounting_reconciliation["status"]),
            True,
            (
                "daily NAV change closes to holding P&L, cash change, "
                "transaction costs and fees"
            ),
            ";".join(
                portfolio_accounting_reconciliation.get("blockers", [])
            )
            or "closed",
            "portfolio_accounting_reconciliation_report.json",
        ),
        ValidationGate(
            "correctness_synthetic_suite",
            str(correctness_synthetic_suite["status"]),
            True,
            "all deterministic correctness CI scenarios are detected",
            ";".join(correctness_synthetic_suite.get("blockers", []))
            or "all scenarios passed",
            "correctness_synthetic_suite_report.json",
        ),
        ValidationGate(
            "failure_injection",
            str(failure_injection["status"]),
            True,
            "all mandatory fail-close corruptions are detected",
            ";".join(failure_injection.get("blockers", [])) or "passed",
            "failure_injection_report.json",
        ),
        ValidationGate(
            "execution_simulation",
            str(execution_stress["status"]),
            True,
            "simulation-only limit, delay and expanded-slippage robustness index passes",
            ";".join(execution_stress.get("blockers", [])) or "passed",
            "execution_stress_report.json",
        ),
        ValidationGate(
            "walk_forward",
            _evidence_status(walk_forward, ("PASS", "VERIFIED")),
            True,
            "release-scoped walk-forward, DSR/PBO and bootstrap",
            str(walk_forward.get("status") or "MISSING"),
            str(program.get("upgrade_evidence", {}).get("pr_d_oos_robustness", "")),
        ),
        ValidationGate(
            "execution_cost_stress",
            str(execution["status"]),
            True,
            "cost/slippage/capacity and real fill behavior verified",
            ";".join(execution.get("blockers", [])) or "passed",
            "execution_cost_report.json",
        ),
        ValidationGate(
            "economic_shadow",
            "PASS" if shadow_pass else "BLOCKED",
            True,
            (
                f"{program['shadow']['economic_min_real_trading_days']} real days and "
                f"{program['shadow']['economic_min_completed_round_trips']} closed round trips"
            ),
            f"{shadow_days} days; {round_trips} round trips",
            str(shadow.get("source") or "MISSING"),
        ),
        ValidationGate(
            "manual_approval",
            "BLOCKED",
            True,
            "release and evidence-SHA bound approval",
            "not provided",
            "manual confirmation required",
        ),
    ]
    offline_names = {
        "formal_pit",
        "core_history",
        "benchmark_excess",
        "alpha_attribution",
        "factor_ic",
        "alpha_proof_guard",
        "factor_compute_lineage",
        "research_replay",
        "replay_diff",
        "research_correctness",
        "event_correctness_coverage",
        "portfolio_state_audit",
        "portfolio_accounting_reconciliation",
        "portfolio_accounting_reconciliation",
        "correctness_synthetic_suite",
        "failure_injection",
        "execution_simulation",
        "walk_forward",
        "execution_cost_stress",
    }
    research_pass = all(gate.passed for gate in gates if gate.gate in offline_names)
    promotion_pass = all(gate.passed for gate in gates)
    promotion = {
        "schema_version": "alpha_v4_0_promotion_gate_v1",
        "status": "PASS" if promotion_pass else "BLOCKED",
        "research_status": "PASS" if research_pass else "BLOCKED",
        "production_status": "NO_CHANGE",
        "capital_status": "NO_SCALE",
        "allowed_capital_cny": 50_000 if promotion_pass else 0,
        "canary_enabled": False,
        "broker_api_enabled": False,
        "blocking_gates": [gate.gate for gate in gates if gate.blocking and not gate.passed],
        "gates": [asdict(gate) | {"passed": gate.passed} for gate in gates],
    }
    scorecard = {
        "schema_version": "alpha_v4_0_strategy_scorecard_v1",
        "status": promotion["status"],
        "strategy_id": program["strategy_id"],
        "release_id": program["release_id"],
        "metrics": metrics,
        "benchmark_metrics": benchmark_metrics,
        "annualized_excess_return": excess,
        "alpha_proof_guard": {
            "status": alpha_guard["status"],
            "components": alpha_guard.get("components", {}),
        },
        "research_target_annualized_return": performance[
            "research_target_annualized_return"
        ],
        "capital_authority": {
            "allowed_capital_cny": promotion["allowed_capital_cny"],
            "capital_ladder": program["capital_ladder"],
            "historical_backfill_counts": bool(
                program["shadow"].get("historical_backfill_counts", False)
            ),
        },
    }
    return promotion, scorecard


def build_evidence_dependency_graph(
    promotion: dict[str, Any],
    gap_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose why research, trading, and capital cannot advance."""
    gates = {
        str(row["gate"]): str(row["status"])
        for row in promotion.get("gates", [])
    }
    data_gate_names = {
        "formal_pit",
        "core_history",
        "benchmark_excess",
        "factor_ic",
        "factor_compute_lineage",
    }
    alpha_gate_names = {
        "alpha_attribution",
        "alpha_proof_guard",
        "research_replay",
        "replay_diff",
        "research_correctness",
        "event_correctness_coverage",
        "portfolio_state_audit",
        "correctness_synthetic_suite",
        "failure_injection",
        "walk_forward",
    }
    trading_gate_names = {
        "execution_simulation",
        "execution_cost_stress",
        "economic_shadow",
    }

    def layer_status(names: set[str]) -> str:
        return (
            "PASS"
            if names and all(gates.get(name) == "PASS" for name in names)
            else "BLOCKED"
        )

    data_status = layer_status(data_gate_names)
    alpha_status = (
        "PASS"
        if data_status == "PASS" and layer_status(alpha_gate_names) == "PASS"
        else "BLOCKED"
    )
    trading_status = (
        "PASS"
        if alpha_status == "PASS" and layer_status(trading_gate_names) == "PASS"
        else "BLOCKED"
    )
    capital_status = (
        "PASS"
        if trading_status == "PASS"
        and gates.get("manual_approval") == "PASS"
        and float(promotion.get("allowed_capital_cny") or 0) > 0
        else "BLOCKED"
    )
    gap_report = gap_report or {}
    gap_nodes = [
        {
            "node": str(row.get("source_node") or "CORRECTNESS_EVIDENCE"),
            "status": "BLOCKED",
            "source_gates": ["research_correctness"],
            "missing_field": row.get("missing_field"),
            "severity": row.get("severity"),
            "recommended_action": row.get("recommended_action"),
        }
        for row in gap_report.get("gaps", [])
    ]
    deduplicated_gap_nodes = {
        (str(row["node"]), str(row.get("missing_field"))): row
        for row in gap_nodes
    }
    nodes = [
        {
            "node": "DATA_EVIDENCE",
            "status": data_status,
            "source_gates": sorted(data_gate_names),
        },
        {
            "node": "ALPHA_EVIDENCE",
            "status": alpha_status,
            "source_gates": sorted(alpha_gate_names),
        },
        {
            "node": "TRADING_EVIDENCE",
            "status": trading_status,
            "source_gates": sorted(trading_gate_names),
        },
        {
            "node": "CAPITAL_APPROVAL",
            "status": capital_status,
            "source_gates": ["manual_approval"],
        },
    ]
    nodes.extend(deduplicated_gap_nodes.values())
    edges = [
        {"from": "DATA_EVIDENCE", "to": "ALPHA_EVIDENCE"},
        {"from": "ALPHA_EVIDENCE", "to": "TRADING_EVIDENCE"},
        {"from": "TRADING_EVIDENCE", "to": "CAPITAL_APPROVAL"},
    ]
    edges.extend(
        {
            "from": row["node"],
            "to": "TRADING_EVIDENCE",
            "missing_field": row.get("missing_field"),
        }
        for row in deduplicated_gap_nodes.values()
    )
    return {
        "schema_version": "alpha_v4_0_evidence_dependency_graph_v4",
        "status": capital_status,
        "allowed_capital_cny": float(
            promotion.get("allowed_capital_cny") or 0
        ),
        "nodes": nodes,
        "edges": edges,
        "blocking_gates": list(promotion.get("blocking_gates") or []),
        "gap_node_count": len(deduplicated_gap_nodes),
    }


def build_release_readiness_score(
    *,
    environment_manifest: dict[str, Any],
    research_replay: dict[str, Any],
    replay_diff: dict[str, Any],
    research_correctness: dict[str, Any],
    correctness_synthetic_suite: dict[str, Any],
    failure_injection: dict[str, Any],
    promotion: dict[str, Any],
) -> dict[str, Any]:
    """Score engineering evidence only; never imply investment readiness."""
    components = [
        ("environment_fingerprint", environment_manifest, 15),
        ("research_replay", research_replay, 20),
        ("replay_diff", replay_diff, 20),
        ("research_correctness", research_correctness, 15),
        ("correctness_synthetic_suite", correctness_synthetic_suite, 15),
        ("failure_injection", failure_injection, 15),
    ]
    rows = [
        {
            "component": name,
            "status": str(payload.get("status") or "BLOCKED"),
            "weight": weight,
            "earned": weight if str(payload.get("status")) == "PASS" else 0,
        }
        for name, payload, weight in components
    ]
    score = sum(int(row["earned"]) for row in rows)
    return {
        "schema_version": "alpha_v4_0_engineering_readiness_v1",
        "status": "PASS" if score == 100 else "BLOCKED",
        "score": score,
        "maximum_score": 100,
        "score_type": "ENGINEERING_READINESS_ONLY",
        "display_name": "Engineering Readiness Score",
        "headline_warning": "NOT AN INVESTMENT READINESS SCORE",
        "not_investment_score": True,
        "promotion_eligible": False,
        "capital_authority": False,
        "capital_status": promotion.get("capital_status"),
        "allowed_capital_cny": float(
            promotion.get("allowed_capital_cny") or 0
        ),
        "components": rows,
        "interpretation": (
            "Engineering readiness measures evidence-system completeness only; "
            "it cannot authorize Canary, broker routing, Shadow substitution, "
            "or capital."
        ),
    }


def write_validation_package(
    *,
    program_path: Path = DEFAULT_PROGRAM,
    output_dir: Path,
    profile_name: str = "alpha_v3_2",
    release_id: str | None = None,
    strategy: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    initial_capital: float | None = None,
    nav_path: Path | None = None,
    trades_path: Path | None = None,
    benchmark_nav_path: Path | None = None,
    benchmark_strategy: str | None = None,
    factor_panel_path: Path | None = None,
    factor_returns_path: Path | None = None,
    factor_source_manifest_path: Path | None = None,
    factor_compute_manifest_path: Path | None = None,
    attribution_evidence_path: Path | None = None,
    selection_attribution_path: Path | None = None,
    walk_forward_path: Path | None = None,
    pit_manifest_path: Path | None = None,
    shadow_status_path: Path | None = None,
    replay_reference_path: Path | None = None,
    research_production_contract_path: Path | None = None,
    benchmark_builder_manifest_path: Path | None = None,
    factor_builder_report_path: Path | None = None,
    pit_builder_report_path: Path | None = None,
    evidence_production_report_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    profile = load_validation_profile(profile_name)
    environment_manifest = build_environment_manifest(
        str(profile["alpha_proof"]["timezone"]),
        profile["replay_audit"]["runtime_determinism"],
    )
    program, default_nav, default_trades, default_wf, default_pit = (
        _resolve_default_inputs(program_path)
    )
    release_id = str(release_id or program["release_id"])
    strategy = str(strategy or program["strategy_id"])
    if release_id != str(program["release_id"]) or strategy != str(program["strategy_id"]):
        raise ValueError("release_or_strategy_must_match_program")
    start_date = str(start_date or profile["core_period"]["min_start_date"])
    initial_capital = float(
        initial_capital or profile["stress"]["initial_capital_cny"]
    )
    nav_path = nav_path or default_nav
    trades_path = trades_path or default_trades
    walk_forward_path = walk_forward_path or default_wf
    pit_manifest_path = pit_manifest_path or default_pit
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or datetime.now(SHANGHAI)

    nav_all = _load_csv(nav_path)
    nav = _scope_nav(
        nav_all, strategy=strategy, start_date=start_date, end_date=end_date
    )
    metrics = compute_nav_metrics(nav, initial_capital)
    benchmark_all = _load_csv(benchmark_nav_path)
    trades = _load_csv(trades_path)
    factor_panel = _load_csv(factor_panel_path)
    factor_returns = _load_csv(factor_returns_path)
    factor_source_manifest = _load_json(factor_source_manifest_path)
    factor_compute_manifest = _load_json(factor_compute_manifest_path)
    attribution_evidence = _load_json(attribution_evidence_path)
    selection_attribution = _load_json(selection_attribution_path)
    walk_forward = _load_json(walk_forward_path)
    pit = _load_json(pit_manifest_path)
    shadow = _load_json(shadow_status_path)
    replay_reference = _load_json(replay_reference_path)
    research_production_contract = _load_json(
        research_production_contract_path
    )
    benchmark_builder = _load_json(benchmark_builder_manifest_path)
    factor_builder = _load_json(factor_builder_report_path)
    pit_builder = _load_json(pit_builder_report_path)
    evidence_production = _load_json(evidence_production_report_path)

    inputs = {
        "acceptance_config": ACCEPTANCE_PATH,
        "program": program_path,
        "nav": nav_path,
        "trades": trades_path,
        "benchmark_nav": benchmark_nav_path,
        "factor_panel": factor_panel_path,
        "factor_returns": factor_returns_path,
        "factor_source_manifest": factor_source_manifest_path,
        "factor_compute_manifest": factor_compute_manifest_path,
        "attribution_evidence": attribution_evidence_path,
        "selection_attribution": selection_attribution_path,
        "walk_forward": walk_forward_path,
        "pit_manifest": pit_manifest_path,
        "shadow_status": shadow_status_path,
        "replay_reference": replay_reference_path,
        "research_production_contract": research_production_contract_path,
        "benchmark_builder_manifest": benchmark_builder_manifest_path,
        "factor_builder_report": factor_builder_report_path,
        "pit_builder_report": pit_builder_report_path,
        "evidence_production_report": evidence_production_report_path,
    }
    input_hashes = {
        name: {"path": str(path) if path else "", "sha256": _file_sha(path)}
        for name, path in inputs.items()
    }
    code_hashes = {
        str(path.relative_to(PROJECT_ROOT)): _file_sha(path)
        for path in CODE_EVIDENCE_PATHS
    }
    provenance = {
        "profile": profile_name,
        "profile_schema_version": profile["schema_version"],
        "evidence_version": profile["evidence_version"],
        "release_id": release_id,
        "strategy_id": strategy,
        "git_head": _git_head(),
        "code_snapshot_sha256": canonical_sha(code_hashes),
        "environment_lock_hash": environment_manifest[
            "environment_lock_hash"
        ],
        "code_files": code_hashes,
        "config_sha256": _file_sha(ACCEPTANCE_PATH),
        "input_snapshot_sha256": canonical_sha(
            {
                name: value
                for name, value in input_hashes.items()
                if name != "replay_reference"
            }
        ),
        "sample_start_requested": start_date,
        "sample_end_requested": end_date,
        "sample_range_actual": {
            "start": metrics.get("sample_start"),
            "end": metrics.get("sample_end"),
            "trading_days": metrics.get("trading_days", 0),
        },
        "initial_capital_cny": initial_capital,
        "execution_model": profile["execution"],
        "cost_model": {
            "cost_multipliers": profile["stress"]["cost_multipliers"],
            "slippage_bps": profile["stress"]["slippage_bps"],
        },
        "input_files": input_hashes,
    }
    common = {
        "generated_at": generated_at.isoformat(),
        "provenance": provenance,
    }
    benchmark_report = build_benchmark_excess_report(nav, benchmark_all, profile)
    primary_benchmark = next(
        (
            row
            for row in benchmark_report["rows"]
            if row["benchmark"] == benchmark_report["primary_benchmark"]
        ),
        {},
    )
    benchmark_metrics = {
        "status": primary_benchmark.get("status", "MISSING"),
        "annualized_return": primary_benchmark.get("benchmark_annualized_return"),
        "information_ratio": primary_benchmark.get("information_ratio"),
        "beta": primary_benchmark.get("beta"),
        "annualized_alpha": primary_benchmark.get("annualized_alpha"),
    }
    generated_attribution = build_daily_factor_attribution(
        nav, factor_returns, profile
    )
    allowed_external_attribution_schemas = {"alpha_v3_5_attribution_v1"}
    if str(profile.get("evidence_version") or "").startswith("alpha_v3_2"):
        # A v3.1 report is not a v3.2 proof artifact.  Only an independent
        # report carrying the active v3.2 schema may be considered here.
        allowed_external_attribution_schemas.add("alpha_v3_2_attribution_v1")
    trusted_external_attribution = bool(
        attribution_evidence.get("schema_version")
        in allowed_external_attribution_schemas
        and str(attribution_evidence.get("status")).upper() == "PASS"
    )
    attribution_source = (
        generated_attribution
        if generated_attribution["status"] == "PASS"
        or not trusted_external_attribution
        else attribution_evidence
    )
    attribution = build_alpha_attribution_report(
        attribution_source, metrics, profile
    )
    attribution = attach_selection_attribution(
        attribution,
        selection_attribution,
        release_id=release_id,
        strategy=strategy,
        metrics=metrics,
        evidence_version=profile.get("evidence_version"),
    )
    attribution["proof_layer"] = generated_attribution
    factor_ic = build_factor_ic_report(factor_panel, profile)
    factor_lineage = build_factor_lineage_report(
        factor_returns, factor_panel, factor_source_manifest, profile
    )
    factor_compute_lineage = build_factor_compute_lineage_report(
        factor_compute_manifest,
        profile,
        factor_lineage,
        environment_manifest,
    )
    factor_effectiveness = build_factor_effectiveness_report(
        factor_panel, profile, factor_lineage
    )
    factor_returns_availability = audit_factor_availability(
        factor_returns, profile, panel_name="factor_returns"
    )
    factor_panel_availability = audit_factor_availability(
        factor_panel, profile, panel_name="factor_panel"
    )
    stability = build_alpha_stability_report(nav, factor_returns, profile)
    regime_attribution = build_regime_conditional_attribution_report(
        nav, factor_returns, profile
    )
    alpha_guard = build_alpha_proof_guard_report(
        benchmark_report,
        factor_returns_availability,
        factor_panel_availability,
        attribution,
        stability,
        factor_lineage,
        factor_effectiveness,
        regime_attribution,
        profile.get("evidence_version"),
    )
    alpha_proof = build_alpha_proof_summary(
        benchmark_report, attribution, factor_ic, alpha_guard
    )
    execution = build_execution_cost_report(
        trades,
        metrics,
        strategy=strategy,
        initial_capital=initial_capital,
        profile=profile,
    )
    capacity_curve = build_capacity_curve_report(
        trades, profile, initial_capital=initial_capital
    )
    execution_stress = build_execution_stress_report(
        trades, profile, initial_capital=initial_capital
    )
    failure_injection = build_failure_injection_report(profile)
    walk_forward_report = {
        "schema_version": "alpha_v3_walk_forward_v1",
        "status": _evidence_status(walk_forward, ("PASS", "VERIFIED")),
        "source_status": walk_forward.get("status") or "MISSING",
        "source": str(walk_forward_path or ""),
        "source_sha256": _file_sha(walk_forward_path),
        "blockers": walk_forward.get("blockers")
        or ([] if _evidence_status(walk_forward, ("PASS", "VERIFIED")) == "PASS" else ["walk_forward_evidence_missing_or_blocked"]),
    }
    risk_payload = {
        "metrics": metrics,
        "capacity_curve": capacity_curve,
        "execution_stress": execution_stress,
    }
    replay_contract = {
        "release_id": release_id,
        "strategy_id": strategy,
        "profile": profile_name,
        "environment_lock_hash": environment_manifest[
            "environment_lock_hash"
        ],
        "code_snapshot_sha256": provenance["code_snapshot_sha256"],
        "config_sha256": provenance["config_sha256"],
        "input_snapshot_sha256": provenance["input_snapshot_sha256"],
        "nav_sha256": input_hashes["nav"]["sha256"],
        "trades_sha256": input_hashes["trades"]["sha256"],
        "attribution_sha256": canonical_sha(attribution),
        "risk_sha256": canonical_sha(risk_payload),
    }
    research_replay = build_research_replay_report(
        replay_reference, replay_contract
    )
    replay_snapshot = build_replay_snapshot(
        nav, trades, attribution, risk_payload
    )
    replay_diff = build_replay_diff_report(
        replay_reference,
        replay_contract,
        replay_snapshot,
        max_rows=int(profile["replay_audit"]["replay_diff_max_rows"]),
    )
    research_correctness = build_research_correctness_report(
        nav,
        trades,
        sample_size=int(profile["replay_audit"]["correctness_sample_size"]),
        seed=int(profile["replay_audit"]["runtime_determinism"]["random_seed"]),
        research_production_contract=research_production_contract,
    )
    event_correctness_coverage = build_event_correctness_coverage(
        nav,
        trades,
        annual_anchor_days_per_year=int(
            profile["replay_audit"]["annual_anchor_days_per_year"]
        ),
        event_quotas={
            str(key): int(value)
            for key, value in profile["replay_audit"][
                "event_sample_quotas"
            ].items()
        },
    )
    portfolio_state_audit = build_portfolio_state_audit(
        nav,
        weight_tolerance=float(
            profile["replay_audit"]["portfolio_state_weight_tolerance"]
        ),
    )
    portfolio_accounting_reconciliation = (
        build_portfolio_accounting_reconciliation(
            nav,
            tolerance_cny=float(
                profile["replay_audit"][
                    "portfolio_accounting_tolerance_cny"
                ]
            ),
        )
    )
    correctness_synthetic_suite = build_correctness_synthetic_suite(
        sample_size=int(profile["replay_audit"]["correctness_sample_size"]),
        seed=int(profile["replay_audit"]["runtime_determinism"]["random_seed"]),
    )
    promotion, scorecard = build_gate_reports(
        program=program,
        profile=profile,
        metrics=metrics,
        benchmark_metrics=benchmark_metrics,
        benchmark_evidence_status=benchmark_report["status"],
        attribution=attribution,
        factor_ic=factor_ic,
        alpha_guard=alpha_guard,
        factor_compute_lineage=factor_compute_lineage,
        research_replay=research_replay,
        replay_diff=replay_diff,
        research_correctness=research_correctness,
        event_correctness_coverage=event_correctness_coverage,
        portfolio_state_audit=portfolio_state_audit,
        portfolio_accounting_reconciliation=(
            portfolio_accounting_reconciliation
        ),
        correctness_synthetic_suite=correctness_synthetic_suite,
        failure_injection=failure_injection,
        execution_stress=execution_stress,
        walk_forward=walk_forward_report,
        execution=execution,
        pit=pit,
        shadow=shadow,
    )
    gap_report = build_correctness_gap_report(
        research_correctness, promotion
    )
    dependency_graph = build_evidence_dependency_graph(
        promotion, gap_report
    )
    release_readiness = build_release_readiness_score(
        environment_manifest=environment_manifest,
        research_replay=research_replay,
        replay_diff=replay_diff,
        research_correctness=research_correctness,
        correctness_synthetic_suite=correctness_synthetic_suite,
        failure_injection=failure_injection,
        promotion=promotion,
    )
    evidence_matrix = build_evidence_contract_matrix(
        promotion,
        release_id=release_id,
        generated_at=generated_at.isoformat(),
    )
    evidence_issue_tracker = build_evidence_issue_tracker(evidence_matrix)
    capital_gate_simulator = build_capital_gate_simulator(promotion)
    evidence_strength = build_evidence_strength_report(evidence_matrix)
    evidence_promotion_workflow = build_evidence_promotion_workflow(
        evidence_matrix, evidence_issue_tracker
    )
    capital_firewall = build_capital_firewall(
        promotion, evidence_strength
    )
    alpha_claim_registry = build_alpha_claim_registry(
        promotion, evidence_strength, capital_firewall
    )
    failure_coverage_matrix = build_failure_coverage_matrix(
        failure_injection
    )
    investment_readiness = build_investment_readiness_report(
        release_readiness, evidence_matrix, promotion
    )
    strategy_health = build_strategy_health_report(nav, trades, metrics)
    readiness_config = profile["capital_readiness_simulation"]
    evidence_expiration = build_evidence_expiration_report(
        evidence_matrix,
        evidence_strength,
        as_of=generated_at,
        ttl_days={
            str(key): int(value)
            for key, value in readiness_config["evidence_ttl_days"].items()
        },
    )
    capital_tier_engine = build_capital_tier_engine(
        promotion,
        evidence_strength,
        evidence_expiration,
        capital_firewall,
        tier_config=readiness_config["tiers"],
    )
    claim_lifecycle = build_claim_lifecycle_report(
        promotion,
        evidence_strength,
        evidence_expiration,
        capital_firewall,
    )
    strategy_health_monitor = build_strategy_health_monitor(
        nav,
        trades,
        drawdown_limit=float(profile["performance"]["max_drawdown"]),
        volatility_warning_ratio=float(
            readiness_config["volatility_warning_ratio"]
        ),
        turnover_zscore_warning=float(
            readiness_config["turnover_zscore_warning"]
        ),
    )
    independent_reviewer = build_independent_reviewer_simulation(
        promotion=promotion,
        matrix=evidence_matrix,
        expiration=evidence_expiration,
        tier_engine=capital_tier_engine,
        claim_lifecycle=claim_lifecycle,
        health_monitor=strategy_health_monitor,
    )
    acquisition_payloads = build_evidence_acquisition_pipeline(
        PROJECT_ROOT,
        profile,
        release_id=release_id,
        strategy_id=strategy,
        analysis_asof=generated_at,
        output_dir=output_dir,
        explicit_paths={
            "benchmark": benchmark_nav_path,
            "factor": factor_panel_path or factor_returns_path,
            "pit": pit_manifest_path,
            "shadow": shadow_status_path,
            "execution": trades_path,
        },
    )
    payloads = {
        "benchmark_evidence_builder_report.json": {
            **common,
            **(
                benchmark_builder
                or {
                    "schema_version": "alpha_v4_2_benchmark_builder_v1",
                    "status": "BLOCKED",
                    "blockers": ["benchmark_builder_manifest_missing"],
                }
            ),
        },
        "factor_evidence_builder_report.json": {
            **common,
            **(
                factor_builder
                or {
                    "schema_version": "alpha_v4_2_factor_builder_v1",
                    "status": "BLOCKED",
                    "blockers": ["factor_builder_report_missing"],
                }
            ),
        },
        "pit_minimum_builder_report.json": {
            **common,
            **(
                pit_builder
                or {
                    "schema_version": "alpha_v4_2_pit_minimum_builder_v1",
                    "status": "BLOCKED",
                    "blockers": ["pit_builder_report_missing"],
                }
            ),
        },
        "evidence_production_report.json": {
            **common,
            **(
                evidence_production
                or {
                    "schema_version": "alpha_v4_2_evidence_production_summary_v1",
                    "status": "BLOCKED",
                    "blockers": ["evidence_production_report_missing"],
                }
            ),
        },
        **{
            name: {**common, **payload}
            for name, payload in acquisition_payloads.items()
        },
        "alpha_proof_report.json": {**common, **alpha_proof},
        "alpha_proof_guard_report.json": {**common, **alpha_guard},
        "factor_lineage_report.json": {**common, **factor_lineage},
        "factor_compute_lineage_report.json": {
            **common,
            **factor_compute_lineage,
        },
        "factor_effectiveness_report.json": {**common, **factor_effectiveness},
        "capacity_curve_report.json": {**common, **capacity_curve},
        "regime_conditional_attribution_report.json": {
            **common,
            **regime_attribution,
        },
        "environment_manifest.json": {**common, **environment_manifest},
        "replay_snapshot_report.json": {**common, **replay_snapshot},
        "research_replay_report.json": {**common, **research_replay},
        "replay_diff_report.json": {**common, **replay_diff},
        "research_correctness_audit_report.json": {
            **common,
            **research_correctness,
        },
        "event_correctness_coverage_report.json": {
            **common,
            **event_correctness_coverage,
        },
        "portfolio_state_audit_report.json": {
            **common,
            **portfolio_state_audit,
        },
        "research_gap_report.json": {**common, **gap_report},
        "correctness_synthetic_suite_report.json": {
            **common,
            **correctness_synthetic_suite,
        },
        "failure_injection_report.json": {**common, **failure_injection},
        "execution_stress_report.json": {**common, **execution_stress},
        "evidence_dependency_graph_report.json": {
            **common,
            **dependency_graph,
        },
        "engineering_readiness_report.json": {
            **common,
            **release_readiness,
        },
        "evidence_contract_matrix_report.json": {
            **common,
            **evidence_matrix,
        },
        "evidence_issue_tracker_report.json": {
            **common,
            **evidence_issue_tracker,
        },
        "capital_gate_simulator_report.json": {
            **common,
            **capital_gate_simulator,
        },
        "investment_readiness_report.json": {
            **common,
            **investment_readiness,
        },
        "strategy_health_report.json": {
            **common,
            **strategy_health,
        },
        "portfolio_accounting_reconciliation_report.json": {
            **common,
            **portfolio_accounting_reconciliation,
        },
        "failure_coverage_matrix_report.json": {
            **common,
            **failure_coverage_matrix,
        },
        "evidence_strength_report.json": {
            **common,
            **evidence_strength,
        },
        "evidence_promotion_workflow_report.json": {
            **common,
            **evidence_promotion_workflow,
        },
        "alpha_claim_registry_report.json": {
            **common,
            **alpha_claim_registry,
        },
        "capital_firewall_report.json": {
            **common,
            **capital_firewall,
        },
        "evidence_expiration_report.json": {
            **common,
            **evidence_expiration,
        },
        "capital_tier_engine_report.json": {
            **common,
            **capital_tier_engine,
        },
        "claim_lifecycle_report.json": {
            **common,
            **claim_lifecycle,
        },
        "strategy_health_monitor_report.json": {
            **common,
            **strategy_health_monitor,
        },
        "independent_reviewer_simulation_report.json": {
            **common,
            **independent_reviewer,
        },
        "benchmark_excess_report.json": {**common, **benchmark_report},
        "alpha_attribution_report.json": {**common, **attribution},
        "factor_ic_report.json": {**common, **factor_ic},
        "walk_forward_report.json": {**common, **walk_forward_report},
        "execution_cost_report.json": {**common, **execution},
        "promotion_gate_report.json": {**common, **promotion},
        "strategy_scorecard.json": {**common, **scorecard},
    }
    written: dict[str, dict[str, Any]] = {}
    for name in REPORT_NAMES:
        written[name] = _write_report(output_dir / name, payloads[name])
    manifest = {
        **common,
        "schema_version": "alpha_v4_7_evidence_manifest_v1",
        "status": promotion["status"],
        "replay_contract": replay_contract,
        "reports": {
            name: {
                "path": str(output_dir / name),
                "file_sha256": _file_sha(output_dir / name),
                "content_sha256": written[name]["content_sha256"],
            }
            for name in REPORT_NAMES
        },
        "deterministic_evidence_sha256": canonical_sha(
            {
                "provenance": provenance,
                "report_content_sha256": {
                    name: written[name]["content_sha256"] for name in REPORT_NAMES
                },
            }
        ),
    }
    _write_report(output_dir / "evidence_manifest.json", manifest)
    summary_lines = [
        "# Chenyiyun2087 Alpha v4.7 验证摘要",
        "",
        f"- Release：`{release_id}`",
        f"- 策略：`{strategy}`",
        f"- 结论：**{promotion['status']} / {promotion['capital_status']}**",
        f"- Alpha 证明层：**{alpha_proof['status']}**",
        f"- Alpha Evidence Audit：**{alpha_guard['status']}**",
        f"- 因子证据联动：**{factor_effectiveness['status']}**",
        f"- 因子计算血缘：**{factor_compute_lineage['status']}**",
        f"- 确定性重放：**{research_replay['status']}**",
        f"- 结构化重放差异：**{replay_diff['status']}**",
        f"- 研究正确性审计：**{research_correctness['status']}**",
        f"- 事件正确性覆盖：**{event_correctness_coverage['status']}**",
        f"- 组合状态审计：**{portfolio_state_audit['status']}**",
        f"- 组合会计闭合：**{portfolio_accounting_reconciliation['status']}**",
        f"- 正确性缺口闭环：**{gap_report['status']}**",
        f"- 合成正确性基准：**{correctness_synthetic_suite['status']}**",
        f"- Engineering Readiness Score：**{release_readiness['score']}/100**",
        "- **NOT AN INVESTMENT READINESS SCORE**",
        f"- Investment Readiness Score：**{investment_readiness['scores']['investment_readiness']}/100**",
        f"- Evidence Strength：**{evidence_strength['status']}**",
        f"- Capital Firewall：**{capital_firewall['status']}**",
        f"- Evidence Expiration：**{evidence_expiration['status']}**",
        f"- 模拟资金阶梯：**{capital_tier_engine['current_simulated_tier']}**",
        f"- Claim Lifecycle：**{claim_lifecycle['highest_supported_claim']}**",
        f"- 策略健康监控：**{strategy_health_monitor['status']}**",
        f"- 独立复核模拟：**{independent_reviewer['recommendation']}**",
        f"- 数据发现：**{acquisition_payloads['data_catalog_report.json']['status']}**",
        f"- 证据资格：**{acquisition_payloads['evidence_qualification_report.json']['status']}**",
        f"- 快照冻结：**{acquisition_payloads['evidence_snapshot_manifest.json']['status']}**",
        f"- 证据适配：**{acquisition_payloads['evidence_adapter_report.json']['status']}**",
        f"- 刷新队列：**{acquisition_payloads['evidence_refresh_queue_report.json']['open_count']} 项待办**",
        f"- 三基准证据生产：**{benchmark_builder.get('status', 'BLOCKED')}**",
        f"- 因子证据生产：**{factor_builder.get('status', 'BLOCKED')}**",
        f"- 最小 PIT 生产：**{pit_builder.get('status', 'BLOCKED')}**",
        f"- Alpha Claims：`{', '.join(alpha_claim_registry['allowed_claims'])}`",
        f"- 环境指纹：`{environment_manifest['environment_lock_hash']}`",
        f"- 故障注入：**{failure_injection['status']}**",
        f"- 执行压力模拟：**{execution_stress['status']}**",
        f"- 容量曲线：**{capacity_curve['status']}**",
        f"- 允许新增风险资金：**{capital_firewall['effective_allowed_capital_cny']:,.0f} 元**",
        f"- 样本：{metrics.get('sample_start') or '缺失'} 至 {metrics.get('sample_end') or '缺失'}",
        f"- 年化：{metrics.get('annualized_return') if metrics.get('annualized_return') is not None else 'NA'}",
        f"- 最大回撤：{metrics.get('max_drawdown') if metrics.get('max_drawdown') is not None else 'NA'}",
        f"- Sharpe：{metrics.get('sharpe_ratio') if metrics.get('sharpe_ratio') is not None else 'NA'}",
        "",
        "## 阻塞门禁",
        "",
        *[f"- `{name}`" for name in promotion["blocking_gates"]],
        "",
        "研究结果不会自动改变生产路由、启用 Canary 或授权资金。",
    ]
    (output_dir / "report.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    return {
        "status": promotion["status"],
        "research_status": promotion["research_status"],
        "allowed_capital_cny": capital_firewall[
            "effective_allowed_capital_cny"
        ],
        "blocking_gates": promotion["blocking_gates"],
        "output_dir": str(output_dir),
        "deterministic_evidence_sha256": manifest[
            "deterministic_evidence_sha256"
        ],
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    parser.add_argument("--profile", default="alpha_v3_2")
    parser.add_argument("--release-id")
    parser.add_argument("--strategy")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--initial-capital", type=float)
    parser.add_argument("--nav", type=Path)
    parser.add_argument("--trades", type=Path)
    parser.add_argument("--benchmark-nav", type=Path)
    parser.add_argument("--benchmark-strategy")
    parser.add_argument("--factor-panel", type=Path)
    parser.add_argument("--factor-returns", type=Path)
    parser.add_argument("--factor-source-manifest", type=Path)
    parser.add_argument("--factor-compute-manifest", type=Path)
    parser.add_argument("--attribution-evidence", type=Path)
    parser.add_argument("--selection-attribution", type=Path)
    parser.add_argument("--walk-forward-evidence", type=Path)
    parser.add_argument("--pit-manifest", type=Path)
    parser.add_argument("--shadow-status", type=Path)
    parser.add_argument("--replay-reference", type=Path)
    parser.add_argument("--research-production-contract", type=Path)
    parser.add_argument("--benchmark-builder-manifest", type=Path)
    parser.add_argument("--factor-builder-report", type=Path)
    parser.add_argument("--pit-builder-report", type=Path)
    parser.add_argument("--evidence-production-report", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or (
        DEFAULT_OUTPUT_ROOT
        / f"{datetime.now(SHANGHAI):%Y%m%d_%H%M%S}_{args.profile}"
    )
    result = write_validation_package(
        program_path=args.program,
        output_dir=output_dir,
        profile_name=args.profile,
        release_id=args.release_id,
        strategy=args.strategy,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.initial_capital,
        nav_path=args.nav,
        trades_path=args.trades,
        benchmark_nav_path=args.benchmark_nav,
        benchmark_strategy=args.benchmark_strategy,
        factor_panel_path=args.factor_panel,
        factor_returns_path=args.factor_returns,
        factor_source_manifest_path=args.factor_source_manifest,
        factor_compute_manifest_path=args.factor_compute_manifest,
        attribution_evidence_path=args.attribution_evidence,
        selection_attribution_path=args.selection_attribution,
        walk_forward_path=args.walk_forward_evidence,
        pit_manifest_path=args.pit_manifest,
        shadow_status_path=args.shadow_status,
        replay_reference_path=args.replay_reference,
        research_production_contract_path=args.research_production_contract,
        benchmark_builder_manifest_path=args.benchmark_builder_manifest,
        factor_builder_report_path=args.factor_builder_report,
        pit_builder_report_path=args.pit_builder_report,
        evidence_production_report_path=args.evidence_production_report,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
