"""Orchestrate one immutable five-strategy formal run and dual-ledger replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.run_dual_ledger_acceptance import run as run_dual_ledger
from scripts.research.run_full_history_strict_backtest import build_backtest_command
from scripts.research.formal_readiness_preflight import evaluate_package
from runtime.formal_contract import FORMAL_STRATEGIES
from runtime.formal_evidence_binding import (
    check_clean_worktree,
    compute_formal_run_id,
    freeze_inputs,
    head_unchanged,
    validate_package_reality,
)

DEFAULT_CONFIG = PROJECT_ROOT / "config" / "formal_readiness.yaml"
EXECUTION_MODEL_VERSION = "strict_t1_open_precommit_v1"
FORMAL_CORE_START_DATE = "2022-01-01"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()


def _blocked(
    preflight: Path,
    output_root: Path,
    reason: str,
    preflight_payload: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "immutable_formal_run_v2",
        "status": "BLOCKED",
        "formal_run_started": False,
        "reason": reason,
        "preflight": str(preflight),
        "preflight_status": preflight_payload.get("status"),
        "preflight_evidence_sha256": preflight_payload.get("evidence_sha256"),
    }
    payload.update(extra)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "formal_run_precheck.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def run(
    *,
    preflight: Path,
    package: Path,
    output_root: Path,
    end_date: str,
    dry_run: bool,
    config_path: Path | None = None,
    acceptance_config_path: Path | None = None,
    fixture_mode: bool = False,
    pit_run_id: str = "",
    package_id: str = "",
) -> dict[str, Any]:
    # ------------------------------------------------------------------
    # 3.6  Clean worktree — before (skipped in fixture mode)
    # ------------------------------------------------------------------
    git_sha_before: str
    tree_clean_before: bool
    if fixture_mode:
        git_sha_before = _git_sha()
        tree_clean_before = True  # fixture mode assumes clean
    else:
        git_sha_before, tree_clean_before = check_clean_worktree(PROJECT_ROOT)
        if not tree_clean_before:
            return _blocked(
                preflight,
                output_root,
                "dirty_worktree_before_run",
                json.loads(preflight.read_text(encoding="utf-8")),
                git_commit_sha_before=git_sha_before,
                git_tree_clean_before=tree_clean_before,
            )

    # ------------------------------------------------------------------
    # v5.1.5: Formal identity must be non-empty for non-fixture runs.  This
    # check follows the worktree guard so a dirty checkout is always reported
    # as the actionable failure first.
    # ------------------------------------------------------------------
    if not fixture_mode:
        if not pit_run_id:
            return _blocked(preflight, output_root, "pit_run_id_empty",
                            json.loads(preflight.read_text(encoding="utf-8")))
        if not package_id:
            return _blocked(preflight, output_root, "package_id_empty",
                            json.loads(preflight.read_text(encoding="utf-8")))

    # ------------------------------------------------------------------
    # Load config and preflight payload
    # ------------------------------------------------------------------
    cfg_path = config_path or DEFAULT_CONFIG
    acc_path = acceptance_config_path or (
        PROJECT_ROOT / "config" / "production_acceptance.yaml"
    )
    config = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    preflight_payload = json.loads(preflight.read_text(encoding="utf-8"))

    # Basic preflight status check
    if preflight_payload.get("status") != "READY_FOR_FORMAL_RUN":
        return _blocked(
            preflight, output_root, "preflight_not_ready", preflight_payload,
            git_commit_sha_before=git_sha_before,
            git_tree_clean_before=tree_clean_before,
        )

    # ------------------------------------------------------------------
    # 3.3  Input package re-validation (TOCTOU)
    # ------------------------------------------------------------------
    if not validate_package_reality(package):
        return _blocked(
            preflight,
            output_root,
            "FORMAL_INPUT_REVALIDATION_FAILED",
            preflight_payload,
            revalidation_detail="package_reality_check_failed",
            git_commit_sha_before=git_sha_before,
            git_tree_clean_before=tree_clean_before,
        )

    rechecked = evaluate_package(package.resolve(), config)
    if rechecked.get("status") != config.get("success_status", "READY_FOR_FORMAL_RUN"):
        return _blocked(
            preflight,
            output_root,
            "FORMAL_INPUT_REVALIDATION_FAILED",
            preflight_payload,
            revalidation_status=rechecked.get("status"),
            revalidation_blocking_checks=rechecked.get("blocking_checks"),
            git_commit_sha_before=git_sha_before,
            git_tree_clean_before=tree_clean_before,
        )

    if rechecked.get("evidence_sha256") != preflight_payload.get("evidence_sha256"):
        return _blocked(
            preflight,
            output_root,
            "EVIDENCE_SHA256_MISMATCH",
            preflight_payload,
            revalidation_detail="evidence_sha256_diverged",
            revalidated_sha=rechecked.get("evidence_sha256"),
            git_commit_sha_before=git_sha_before,
            git_tree_clean_before=tree_clean_before,
        )

    # Preflight file integrity is verified implicitly by evidence_sha match above:
    # if the preflight file were swapped, its embedded evidence_sha wouldn't match
    # the re-validated package evidence_sha.
    preflight_file_sha = _sha(preflight)

    # Package identity
    if Path(preflight_payload.get("package", "")).resolve() != package.resolve():
        return _blocked(
            preflight,
            output_root,
            "preflight_package_identity_mismatch",
            preflight_payload,
            git_commit_sha_before=git_sha_before,
            git_tree_clean_before=tree_clean_before,
        )

    # The runner must consume the exact package identities admitted by the
    # Readiness/Admission chain.  A READY report alone is not sufficient: a
    # caller cannot swap a different PIT run or package after preflight.
    package_manifest_path = package / "package_manifest.json"
    admission_id = str(preflight_payload.get("admission_id") or "")
    pr_b_sha256 = str(
        preflight_payload.get("pr_b_file_sha256")
        or preflight_payload.get("pr_b_sha256")
        or ""
    )
    if not package_manifest_path.is_file():
        if not fixture_mode:
            return _blocked(
                preflight, output_root, "package_manifest_not_found", preflight_payload,
                git_commit_sha_before=git_sha_before,
                git_tree_clean_before=tree_clean_before,
            )
    else:
        try:
            package_manifest = json.loads(package_manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return _blocked(
                preflight, output_root, "package_manifest_unreadable", preflight_payload,
                error=f"{type(exc).__name__}: {exc}",
                git_commit_sha_before=git_sha_before,
                git_tree_clean_before=tree_clean_before,
            )
        manifest_pit_run_id = str(package_manifest.get("formal_pit_run_id") or "")
        manifest_package_id = str(package_manifest.get("package_id") or "")
        if manifest_pit_run_id != str(pit_run_id):
            return _blocked(
                preflight, output_root, "package_manifest_pit_run_id_mismatch", preflight_payload,
                expected_pit_run_id=pit_run_id, actual_pit_run_id=manifest_pit_run_id,
                git_commit_sha_before=git_sha_before,
                git_tree_clean_before=tree_clean_before,
            )
        if manifest_package_id != str(package_id):
            return _blocked(
                preflight, output_root, "package_manifest_package_id_mismatch", preflight_payload,
                expected_package_id=package_id, actual_package_id=manifest_package_id,
                git_commit_sha_before=git_sha_before,
                git_tree_clean_before=tree_clean_before,
            )
        if not fixture_mode:
            # A READY report by itself is not a formal admission.  Locate the
            # sealed Admission/PR-B artifact that binds this exact PIT run
            # and package before the Runner can consume any input.
            admission_root = PROJECT_ROOT / "exports" / "formal_admissions"
            matches: list[Path] = []
            if admission_root.is_dir():
                for candidate in sorted(admission_root.iterdir()):
                    manifest_candidate = candidate / "admission_manifest.json"
                    if not manifest_candidate.is_file():
                        continue
                    try:
                        payload = json.loads(manifest_candidate.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if (
                        payload.get("formal_pit_run_id") == pit_run_id
                        and payload.get("package_id") == package_id
                        and payload.get("status") == "PASS"
                    ):
                        matches.append(candidate)
            if len(matches) != 1:
                return _blocked(
                    preflight, output_root,
                    "formal_admission_missing_or_ambiguous", preflight_payload,
                    admission_candidates=len(matches),
                    git_commit_sha_before=git_sha_before,
                    git_tree_clean_before=tree_clean_before,
                )
            admission_dir = matches[0]
            from runtime.artifact_seal import verify_seal
            admission_seal = verify_seal(admission_dir)
            if admission_seal.get("status") != "VERIFIED":
                return _blocked(
                    preflight, output_root,
                    "formal_admission_seal_not_verified", preflight_payload,
                    seal_result=admission_seal,
                    git_commit_sha_before=git_sha_before,
                    git_tree_clean_before=tree_clean_before,
                )
            admission_manifest = json.loads(
                (admission_dir / "admission_manifest.json").read_text(encoding="utf-8")
            )
            admission_id = str(admission_manifest.get("admission_id") or admission_dir.name)
            pr_b_path = admission_dir / str(admission_manifest.get("pr_b_path") or "")
            if not pr_b_path.is_file():
                return _blocked(
                    preflight, output_root,
                    "formal_admission_pr_b_missing", preflight_payload,
                    git_commit_sha_before=git_sha_before,
                    git_tree_clean_before=tree_clean_before,
                )
            pr_b_sha256 = _sha(pr_b_path)
            pr_b_payload = json.loads(pr_b_path.read_text(encoding="utf-8"))
            if pr_b_payload.get("formal_pit_run_id") != pit_run_id or pr_b_payload.get("status") != "PASS":
                return _blocked(
                    preflight, output_root,
                    "formal_admission_pr_b_identity_invalid", preflight_payload,
                    git_commit_sha_before=git_sha_before,
                    git_tree_clean_before=tree_clean_before,
                )

    # ------------------------------------------------------------------
    # Compute identity hashes
    # ------------------------------------------------------------------
    input_sha = str(preflight_payload["evidence_sha256"])
    readiness_config_sha = _sha(cfg_path)
    acceptance_config_sha = _sha(acc_path) if acc_path.exists() else "CONFIG_MISSING"
    required_objects = [str(n) for n in config.get("required_objects", [])]

    # ------------------------------------------------------------------
    # 3.4  Freeze inputs into the formal run directory
    # ------------------------------------------------------------------
    # Create a preliminary run_dir so we can freeze into it.
    # Use a temp run_id for the directory; we'll compute the final id after freezing.
    temp_run_dir = output_root / f"_freezing_{input_sha[:16]}"
    # Ensure uniqueness even across retries
    suffix = 0
    while temp_run_dir.exists():
        suffix += 1
        temp_run_dir = output_root / f"_freezing_{input_sha[:16]}_{suffix}"
    temp_run_dir.mkdir(parents=True)

    frozen_inputs_dir = temp_run_dir / "frozen_inputs"
    try:
        per_file_bindings = freeze_inputs(package, frozen_inputs_dir, required_objects)
    except FileNotFoundError as exc:
        shutil.rmtree(temp_run_dir, ignore_errors=True)
        return _blocked(
            preflight,
            output_root,
            "FROZEN_INPUT_MISSING",
            preflight_payload,
            missing_file=str(exc),
            git_commit_sha_before=git_sha_before,
            git_tree_clean_before=tree_clean_before,
        )

    # Hash each frozen file and compute bundle SHA
    frozen_bundle_hasher = hashlib.sha256()
    for name in sorted(per_file_bindings):
        frozen_bundle_hasher.update(name.encode())
        frozen_bundle_hasher.update(per_file_bindings[name]["sha256"].encode())
    frozen_bundle_sha = frozen_bundle_hasher.hexdigest()

    # ------------------------------------------------------------------
    # 3.7  Enhanced formal run ID
    # ------------------------------------------------------------------
    run_id = compute_formal_run_id(
        evidence_sha256=input_sha,
        git_sha=git_sha_before,
        acceptance_config_sha=acceptance_config_sha,
        readiness_config_sha=readiness_config_sha,
        frozen_bundle_sha=frozen_bundle_sha,
        start_date=FORMAL_CORE_START_DATE,
        end_date=end_date,
        strategy_ids=list(FORMAL_STRATEGIES),
        formal_pit_run_id=str(pit_run_id or preflight_payload.get("formal_pit_run_id") or ""),
        package_id=str(package_id or preflight_payload.get("package_id") or ""),
        admission_id=admission_id,
        pr_b_sha256=pr_b_sha256,
    )

    # Rename temp dir to final run_dir
    run_dir = output_root / run_id
    if run_dir.exists():
        shutil.rmtree(temp_run_dir, ignore_errors=True)
        raise FileExistsError(f"immutable_formal_run_exists:{run_id}")
    temp_run_dir.rename(run_dir)
    frozen_inputs_dir = run_dir / "frozen_inputs"

    account_output = run_dir / "account_backtest"

    # ------------------------------------------------------------------
    # Build backtest command pointing at frozen_inputs/
    # ------------------------------------------------------------------
    # v5.2: Auto-detect strategies from frozen scores
    frozen_scores = pd.read_csv(frozen_inputs_dir / "scores.csv")
    detected_strategies = sorted(
        frozen_scores["strategy"].dropna().unique().tolist()
    ) if "strategy" in frozen_scores.columns else list(FORMAL_STRATEGIES)
    active_strategies = detected_strategies or list(FORMAL_STRATEGIES)
    command = build_backtest_command(
        FORMAL_CORE_START_DATE,
        end_date,
        strategy=",".join(active_strategies),
        cost_rate=0.00075,
        slippage_bps=10,
        initial_cash=_read_initial_capital(package),
        output_dir=str(account_output),
        scores_snapshot=str(frozen_inputs_dir / "scores.csv"),
        prices_snapshot=str(frozen_inputs_dir / "prices.csv"),
        tradable_universe_snapshot=str(frozen_inputs_dir / "tradable_universe.csv"),
        adjustment_factor_snapshot=str(frozen_inputs_dir / "adjustment_factors.csv"),
        corporate_action_snapshot=str(frozen_inputs_dir / "strict_corporate_actions.csv"),
        corporate_action_manifest=str(frozen_inputs_dir / "strict_snapshot_manifest.json"),
        security_lifecycle_snapshot=str(frozen_inputs_dir / "strict_security_lifecycle.csv"),
        security_lifecycle_manifest=str(frozen_inputs_dir / "strict_snapshot_manifest.json"),
        trade_calendar_snapshot=str(frozen_inputs_dir / "trade_calendar.csv"),
        formal_mode=True,
    )

    # ------------------------------------------------------------------
    # Execute backtest (or dry-run)
    # ------------------------------------------------------------------
    ledger_results: list[dict[str, Any]] = []
    status = "DRY_RUN"
    return_code: int | None = None
    if not dry_run:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        (run_dir / "formal_backtest.log").write_text(
            completed.stdout or "", encoding="utf-8"
        )
        return_code = completed.returncode
        status = "BACKTEST_FAILED" if completed.returncode else "BACKTEST_COMPLETE"
        if completed.returncode == 0:
            for strategy in active_strategies:
                ledger_package = account_output / "dual_ledger_packages" / strategy
                ledger_output = run_dir / "dual_ledger" / strategy
                if not ledger_package.is_dir():
                    ledger_results.append(
                        {
                            "strategy": strategy,
                            "status": "BLOCKED",
                            "reason": "dual_ledger_package_missing",
                        }
                    )
                    continue
                report = run_dual_ledger(ledger_package, ledger_output)
                ledger_results.append(
                    {"strategy": strategy, "status": report["status"]}
                )
            status = (
                "VERIFIED"
                if len(ledger_results) == len(active_strategies)
                and all(item["status"] == "VERIFIED" for item in ledger_results)
                else "LEDGER_BLOCKED"
            )

    # ------------------------------------------------------------------
    # 3.6  Clean worktree — after (skipped in fixture mode)
    # ------------------------------------------------------------------
    git_tree_clean_after = tree_clean_before
    git_sha_after = git_sha_before
    if not dry_run and not fixture_mode:
        git_sha_after, git_tree_clean_after_candidate = check_clean_worktree(PROJECT_ROOT)
        git_tree_clean_after = git_tree_clean_after_candidate
        if not git_tree_clean_after:
            if status == "VERIFIED":
                status = "WORKTREE_POSTRUN_DIRTY"
        if not head_unchanged(git_sha_before, PROJECT_ROOT):
            if status == "VERIFIED":
                status = "HEAD_CHANGED_DURING_RUN"

    # ── Canonical initial account identity must be bound before hashing ──
    initial_account_path = package / "initial_account.json"
    initial_account_sha = ""
    initial_cash_cny = 500_000.0
    if initial_account_path.exists():
        try:
            initial_account_payload = json.loads(
                initial_account_path.read_text(encoding="utf-8")
            )
            initial_account_sha = hashlib.sha256(
                initial_account_path.read_bytes()
            ).hexdigest()
            initial_cash_cny = float(
                initial_account_payload.get("initial_cash_cny", 500_000)
            )
        except Exception:
            initial_account_payload = {
                "currency": "CNY",
                "initial_cash_cny": 500_000.0,
                "positions": {},
            }
    else:
        initial_account_payload = {
            "currency": "CNY",
            "initial_cash_cny": 500_000.0,
            "positions": {},
        }

    # ------------------------------------------------------------------
    # 3.5  Expanded formal run manifest (final bytes before sealing)
    # ------------------------------------------------------------------
    manifest: dict[str, Any] = {
        "schema_version": "immutable_formal_run_v3",
        "formal_run_id": run_id,
        "formal_pit_run_id": pit_run_id,
        "package_id": package_id,
        "admission_id": admission_id,
        "pr_b_sha256": pr_b_sha256,
        "status": status,
        "dry_run": dry_run,
        "strategy_ids": active_strategies,
        "dynamic_champion_role": "ADMISSION_CANDIDATE",
        "comparison_strategy_role": "MATCH_ONLY",
        "period_start": FORMAL_CORE_START_DATE,
        "period_end": end_date,
        "cost_rate_one_way": 0.00075,
        "slippage_bps_one_way": 10,
        "git_commit_sha_before": git_sha_before,
        "git_commit_sha_after": git_sha_after,
        "git_tree_clean_before": tree_clean_before,
        "git_tree_clean_after": git_tree_clean_after,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "initial_account_sha256": initial_account_sha,
        "initial_cash_cny": initial_cash_cny,
        "preflight_path": str(preflight),
        "preflight_file_sha256": preflight_file_sha,
        "preflight_evidence_sha256": input_sha,
        "formal_readiness_config_sha256": readiness_config_sha,
        "acceptance_config_sha256": acceptance_config_sha,
        "frozen_bundle_sha256": frozen_bundle_sha,
        "input_objects": per_file_bindings,
        "command": command,
        "return_code": return_code,
        "dual_ledger_results": ledger_results,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "immutable": True,
        "seal_requested": True,
        "fixture_mode": fixture_mode,
    }
    # Self-hash: manifest_sha256 is the canonical self-hash
    # (content_sha256 not duplicated — dual hashes break verification)
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (run_dir / "formal_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_file_sha256 = _sha(run_dir / "formal_run_manifest.json")

    # ── v5.1.3: Frozen Bundle Manifest (standalone JSON) ──
    frozen_bundle_manifest = {
        "schema_version": "frozen_bundle_manifest_v5_1_3",
        "status": "PASS",
        "formal_pit_run_id": manifest["formal_pit_run_id"],
        "package_id": manifest["package_id"],
        "formal_run_id": run_id,
        "frozen_bundle_sha256": frozen_bundle_sha,
        "input_objects": per_file_bindings,
        "fixture_mode": fixture_mode,
        "capital_authority": False,
    }
    frozen_bundle_manifest["content_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in frozen_bundle_manifest.items() if k != "content_sha256"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    (run_dir / "frozen_bundle_manifest.json").write_text(
        json.dumps(frozen_bundle_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # ── v5.1.6: Seal Formal Run ──
    from runtime.artifact_seal import seal_directory as seal_formal_run
    formal_run_id = run_id
    seal_result: dict[str, Any] | None = None
    registry_error: str | None = None
    try:
        seal_result = seal_formal_run(
            run_dir, run_id=formal_run_id, git_commit_sha=git_sha_after
        )
        formal_run_sealed = True
    except Exception as exc:
        formal_run_sealed = False
        seal_error = f"{type(exc).__name__}: {exc}"

    if formal_run_sealed:
        from runtime.artifact_seal import verify_seal
        verification = verify_seal(run_dir)
        if verification.get("status") != "VERIFIED":
            formal_run_sealed = False
            seal_error = f"post_seal_verification:{verification.get('reason', verification.get('status'))}"

    # A sealed directory is an integrity fact, not an economic success fact.
    # DRY_RUN, failed backtests, blocked ledgers, and fixture runs must never
    # enter the registry as FORMAL_RUN_VERIFIED.
    formally_verified = bool(
        formal_run_sealed
        and not dry_run
        and status == "VERIFIED"
        and return_code == 0
        and not fixture_mode
        and git_tree_clean_after
        and head_unchanged(git_sha_before, PROJECT_ROOT)
    )

    # ── v5.1.6: Write formal_run_candidate_registry ──
    try:
        reg_dir = PROJECT_ROOT / "exports" / "formal_evidence_registry"
        reg_dir.mkdir(parents=True, exist_ok=True)
        candidate = {
            "schema_version": "formal_run_candidate_v5_1_6",
            "status": "FORMAL_RUN_VERIFIED" if formally_verified else "FORMAL_RUN_BLOCKED",
            "formal_run_id": formal_run_id,
            "formal_pit_run_id": pit_run_id,
            "package_id": package_id,
            # Keep the historical key as an alias, but make the two distinct
            # identities explicit: self/content hash versus file-byte hash.
            "manifest_sha256": manifest.get("manifest_sha256", ""),
            "manifest_content_sha256": manifest.get("manifest_sha256", ""),
            "manifest_file_sha256": manifest_file_sha256,
            "seal_manifest_file_sha256": (
                hashlib.sha256((run_dir / "seal_manifest.json").read_bytes()).hexdigest()
                if formal_run_sealed and (run_dir / "seal_manifest.json").exists()
                else ""
            ),
            "seal_verified": formal_run_sealed,
            "economic_status": status,
            "capital_authority": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = reg_dir / "formal_run_candidate_registry.json.tmp"
        tmp.write_text(json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True))
        tmp.replace(reg_dir / "formal_run_candidate_registry.json")
    except Exception as exc:
        registry_error = f"{type(exc).__name__}: {exc}"
        formally_verified = False

    manifest["formal_run_sealed"] = formal_run_sealed
    manifest["formally_verified"] = formally_verified
    manifest["registry_write_error"] = registry_error
    if not formal_run_sealed:
        manifest["seal_error"] = seal_error
    return manifest


def _read_initial_capital(package_dir: Path) -> float:
    """Read initial capital from package's initial_account.json."""
    ia_path = package_dir / "initial_account.json"
    if ia_path.exists():
        try:
            ia = json.loads(ia_path.read_text(encoding="utf-8"))
            return float(ia.get("initial_cash_cny", ia.get("initial_capital", 500_000)))
        except Exception:
            pass
    return 500_000.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fixture-mode", action="store_true", help="Skip worktree checks for testing; results marked non-production.")
    parser.add_argument("--pit-run-id", default="", help="formal_pit_run_id from PIT Pipeline")
    parser.add_argument("--package-id", default="", help="package_id from Admission Pipeline")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--acceptance-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "production_acceptance.yaml",
    )
    args = parser.parse_args()
    result = run(
        preflight=args.preflight,
        package=args.package,
        output_root=args.output_root,
        end_date=args.end_date,
        dry_run=args.dry_run,
        config_path=args.config,
        acceptance_config_path=args.acceptance_config,
        fixture_mode=args.fixture_mode,
        pit_run_id=args.pit_run_id,
        package_id=args.package_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if (
        result["status"] in {"DRY_RUN", "VERIFIED"}
        and not result.get("registry_write_error")
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
