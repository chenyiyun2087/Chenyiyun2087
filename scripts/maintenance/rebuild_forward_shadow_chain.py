#!/usr/bin/env python3
"""Reconstruct shadow evidence in a separate directory from frozen packages.

This creates historical engineering replay evidence, never broker executions or
a replacement signal package. Promotion is a separate, guarded operator action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def inventory(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def rebuild_chain(shadow, days: list[str], packages: Path, zone: Path, emit) -> dict:
    """Keep execution-day actions ordered; absent frozen signals stay absent."""
    if days != sorted(set(days)) or not days:
        raise ValueError("execution dates must be nonempty, unique and sorted")
    if days[-1] > date.today().isoformat():
        raise ValueError("future execution reconstruction is forbidden")
    if zone.exists() and any(zone.iterdir()):
        raise ValueError("staging execution directory must be empty")
    zone.mkdir(parents=True, exist_ok=True)
    no_signal = []
    for day in days:
        package = shadow._latest_package_for_execution(day, packages)
        if package is not None:
            result = shadow.precommit(execution_date=day, packages_zone=packages,
                                      execution_zone=zone)
            emit(day, "precommit", result)
        else:
            no_signal.append(day)
            emit(day, "precommit", {"status": "NO_FROZEN_SIGNAL", "orders_created": 0})
        if (zone / day / "orders.json").is_file():
            result = shadow.reconcile_from_package(execution_date=day,
                         execution_zone=zone, packages_zone=packages)
            emit(day, "reconcile", result)
        else:
            emit(day, "reconcile", {"status": "NO_PRECOMMITTED_ORDERS"})
        current_signal = packages / day / "signal_package_manifest.json"
        if current_signal.is_file():
            result = shadow.sell_precommit(business_date=day,
                         execution_zone=zone, packages_zone=packages)
            emit(day, "sell_precommit", result)
        else:
            emit(day, "sell_precommit", {"status": "NO_FROZEN_SIGNAL", "orders_created": 0})
        emit(day, "nav", shadow.nav(execution_date=day, execution_zone=zone))
        shadow.replay_all(zone, as_of_date=day)
    state = shadow.replay_all(zone, as_of_date=days[-1])
    return {"status": "STAGED", "dates": days, "no_execution_signal_dates": no_signal,
            "orders": len(state.orders), "positions": len(state.positions),
            "round_trips": state.completed_round_trips(),
            "qualification": "HISTORICAL_ENGINEERING_RECONSTRUCTION",
            "promotion_performed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    source = args.source_root.expanduser().resolve()
    staging = args.staging_root.expanduser().resolve()
    source_evidence = source / "exports/forward_shadow_evidence"
    target_evidence = staging / "exports/forward_shadow_evidence"
    if target_evidence == source_evidence or source_evidence in target_evidence.parents:
        raise SystemExit("staging may not be inside the live evidence tree")
    os.environ["CHENYIYUN_SOURCE_REPO"] = str(source)
    os.environ["DISABLE_APP_SCHEDULER_LOOP"] = "1"
    from scripts.ops.task_worker_launcher import load_env, ENV_FILE
    load_env(ENV_FILE)
    from scripts.ops import run_daily_shadow as shadow
    packages = source_evidence / "packages"
    days = [d for d in shadow.load_trade_calendar(need_date=args.end_date)
            if args.start_date <= d <= args.end_date]
    before = inventory(packages)
    plan = {"dates": days, "source_root": str(source), "staging_root": str(staging),
            "frozen_package_file_count": len(before)}
    if not args.execute:
        print(json.dumps({"status": "DRY_RUN", **plan}, ensure_ascii=False, indent=2))
        return
    staging.mkdir(parents=True, exist_ok=True)
    progress = staging / "reconstruction_steps.jsonl"
    def emit(day, step, result):
        with progress.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"day": day, "step": step, "result": result},
                                    ensure_ascii=False, default=str) + "\n")
        print(f"{day} {step}: {result.get('status', 'completed')}", flush=True)
    result = rebuild_chain(shadow, days, packages, target_evidence / "execution", emit)
    after = inventory(packages)
    if before != after:
        raise RuntimeError("frozen packages changed during reconstruction; promotion forbidden")
    result.update(plan, package_sha256=before,
                  completed_at=datetime.now().isoformat(timespec="seconds"))
    (staging / "reconstruction.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "package_sha256"},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
