"""Orchestrate one immutable five-strategy formal run and dual-ledger replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.research.run_dual_ledger_acceptance import run as run_dual_ledger
from scripts.research.run_full_history_strict_backtest import build_backtest_command


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORMAL_STRATEGIES = (
    "production_governed_vol_position",
    "production_governed_vol_position_v1_2b_dynamic_score",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
    "production_governed_vol_position_v1_2b_strict_precommit_uplift",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()


def _blocked(
    preflight: Path, output_root: Path, reason: str, preflight_payload: dict[str, Any]
) -> dict[str, Any]:
    payload = {
        "schema_version": "immutable_formal_run_v1",
        "status": "BLOCKED",
        "formal_run_started": False,
        "reason": reason,
        "preflight": str(preflight),
        "preflight_status": preflight_payload.get("status"),
        "preflight_evidence_sha256": preflight_payload.get("evidence_sha256"),
    }
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
) -> dict[str, Any]:
    preflight_payload = json.loads(preflight.read_text(encoding="utf-8"))
    if preflight_payload.get("status") != "READY_FOR_FORMAL_RUN":
        return _blocked(
            preflight,
            output_root,
            "preflight_not_ready",
            preflight_payload,
        )
    if Path(preflight_payload.get("package", "")).resolve() != package.resolve():
        return _blocked(
            preflight,
            output_root,
            "preflight_package_identity_mismatch",
            preflight_payload,
        )
    code_sha = _git_sha()
    input_sha = str(preflight_payload["evidence_sha256"])
    run_id = f"formal-{input_sha[:16]}-{code_sha[:12]}"
    run_dir = output_root / run_id
    if run_dir.exists():
        raise FileExistsError(f"immutable_formal_run_exists:{run_id}")
    run_dir.mkdir(parents=True)
    account_output = run_dir / "account_backtest"
    command = build_backtest_command(
        "2013-01-01",
        end_date,
        strategy=",".join(FORMAL_STRATEGIES),
        cost_rate=0.00075,
        slippage_bps=10,
        initial_cash=500_000,
        output_dir=str(account_output),
        scores_snapshot=str(package / "scores.csv"),
        prices_snapshot=str(package / "prices.csv"),
        corporate_action_snapshot=str(package / "strict_corporate_actions.csv"),
        corporate_action_manifest=str(package / "strict_snapshot_manifest.json"),
        security_lifecycle_snapshot=str(package / "strict_security_lifecycle.csv"),
        security_lifecycle_manifest=str(package / "strict_snapshot_manifest.json"),
        trade_calendar_snapshot=str(package / "trade_calendar.csv"),
    )
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
            for strategy in FORMAL_STRATEGIES:
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
                if len(ledger_results) == len(FORMAL_STRATEGIES)
                and all(item["status"] == "VERIFIED" for item in ledger_results)
                else "LEDGER_BLOCKED"
            )
    manifest: dict[str, Any] = {
        "schema_version": "immutable_formal_run_v1",
        "formal_run_id": run_id,
        "status": status,
        "dry_run": dry_run,
        "strategy_ids": list(FORMAL_STRATEGIES),
        "dynamic_champion_role": "ADMISSION_CANDIDATE",
        "comparison_strategy_role": "MATCH_ONLY",
        "period_start": "2013-01-01",
        "period_end": end_date,
        "cost_rate_one_way": 0.00075,
        "slippage_bps_one_way": 10,
        "git_commit_sha": code_sha,
        "preflight_path": str(preflight),
        "preflight_file_sha256": _sha(preflight),
        "preflight_evidence_sha256": input_sha,
        "source_manifest_sha256": _sha(package / "source_manifest.json"),
        "command": command,
        "return_code": return_code,
        "dual_ledger_results": ledger_results,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "immutable": True,
    }
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
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"DRY_RUN", "VERIFIED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
