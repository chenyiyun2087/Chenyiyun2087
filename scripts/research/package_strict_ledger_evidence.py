"""Package selected strict-ledger evidence into an immutable project exports path."""
from __future__ import annotations
import argparse, hashlib, json, shutil
from pathlib import Path

REQUIRED = [
    "trusted_account_backtest_report.json", "trusted_account_backtest_ledger_events.csv",
    "trusted_account_backtest_ledger_execution_snapshot.csv", "trusted_account_backtest_trades.csv", "trusted_account_backtest_nav.csv", "trusted_account_backtest_summary.csv", "trusted_account_backtest_adaptive_decisions.csv", "trusted_account_backtest_ledger_prices.csv",
]
AUDIT_REQUIRED = [
    "replay/strict_ledger_replay_report.json", "execution_replay/strict_execution_replay_report.json",
    "deviation/strict_execution_deviation_report.json", "risk_events/strict_missed_risk_events_report.json",
    "validation/strict_precommit_account_validation.json",
]

def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def package(run_dir: Path, destination_root: Path, commit: str) -> dict:
    report = json.loads((run_dir / "trusted_account_backtest_report.json").read_text(encoding="utf-8"))
    run_id = run_dir.name
    provenance=report.get("provenance",{}); destination = destination_root / commit / str(provenance.get("data_snapshot_fingerprint")) / str(provenance.get("config_fingerprint")) / run_id
    if provenance.get("reproducibility_status") != "REPRODUCIBLE" or not provenance.get("report_worktree_clean"):
        raise RuntimeError("refuse to package non-reproducible or dirty strict evidence")
    destination.mkdir(parents=True, exist_ok=False)
    missing=[name for name in [*REQUIRED, *AUDIT_REQUIRED] if not (run_dir/name).exists()]
    if missing: raise RuntimeError(f"required evidence missing: {missing}")
    names = list(REQUIRED)
    for relative in [*AUDIT_REQUIRED, "pytest_output.txt"]:
        if (run_dir / relative).exists(): names.append(relative)
    for relative in names:
        target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(run_dir / relative, target)
    hashes = {relative: _hash(destination / relative) for relative in names}
    manifest = {"commit": commit, "run_id": run_id, "source_run_dir": str(run_dir), "report_provenance": report.get("provenance", {}), "files": hashes}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    sums_path = destination / "SHA256SUMS"
    sums_path.write_text("\n".join(f"{digest}  {name}" for name, digest in sorted(hashes.items())) + "\n", encoding="utf-8")
    return {"destination": str(destination), "manifest": str(destination / "manifest.json"), "sha256sums": str(sums_path), "file_count": len(names)}

if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--run-dir",type=Path,required=True); parser.add_argument("--destination-root",type=Path,required=True); parser.add_argument("--commit",required=True)
    args=parser.parse_args(); print(json.dumps(package(args.run_dir,args.destination_root,args.commit),ensure_ascii=False,indent=2))
