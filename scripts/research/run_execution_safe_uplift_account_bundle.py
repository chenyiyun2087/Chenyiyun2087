"""Reproducible strict-precommit run wrapper, not a checkpoint/resume runner.

It fingerprints inputs before a complete rerun.  It does not persist account
state, so ``--resume`` only verifies the same manifest and re-executes.
"""
from __future__ import annotations

import argparse, hashlib, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = "production_governed_vol_position,production_governed_vol_position_v1_2b_gate_tuned,production_governed_vol_position_v1_2b_strict_precommit_uplift"


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True); p.add_argument("--checkpoint-dir", required=True)
    p.add_argument("--resume", action="store_true", help="Verify the prior manifest, then rerun from the start."); p.add_argument("--checkpoint-every", type=int, default=20, help="Compatibility-only; true account checkpoints are not implemented.")
    p.add_argument("--data-cache-dir", required=True); p.add_argument("--start-date", default="2023-01-03"); p.add_argument("--end-date", default="2026-06-17")
    a = p.parse_args(); checkpoint = Path(a.checkpoint_dir) / a.run_id; checkpoint.mkdir(parents=True, exist_ok=True)
    payload = {"strategies": STRATEGIES, "start_date": a.start_date, "end_date": a.end_date, "execution_mode": "strict_t1_open_precommit", "checkpoint_every": a.checkpoint_every, "data_cache_dir": a.data_cache_dir}
    fp = _fingerprint(payload); manifest = checkpoint / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()).get("fingerprint") != fp:
        raise RuntimeError("checkpoint fingerprint mismatch; refuse non-continuous resume")
    if a.resume and not manifest.exists(): raise RuntimeError("missing checkpoint manifest")
    manifest.write_text(json.dumps({"fingerprint": fp, "payload": payload, "status": "running"}, indent=2))
    cmd = [sys.executable, str(ROOT / "scripts/research_trusted_strategy_account_backtest.py"), "--risk-profile", "adaptive", "--start-date", a.start_date, "--end-date", a.end_date, "--strategies", STRATEGIES, "--execution-mode", "strict_t1_open_precommit"]
    out = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True).stdout
    manifest.write_text(json.dumps({"fingerprint": fp, "payload": payload, "status": "complete", "report": out}, indent=2))
    print(out)


if __name__ == "__main__": main()
