#!/usr/bin/env python3
"""Safely rebuild a historical forward-shadow execution window.

The queue's old historical replay could run a later day's precommit before
the previous day's reconcile.  This command preserves the affected evidence,
clears only the requested execution dates, and rebuilds the chain in its
contractual order:

  prior-day sell decision -> precommit -> reconcile -> next-day sell decision
  -> NAV

It never writes the database.  Without ``--execute`` it only prints the
validated plan.  The default window is the 2026-09-14..18 trading week.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _iso_dates(raw_dates: list[str] | None) -> list[str]:
    values = raw_dates or [
        "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
    ]
    out = []
    for raw in values:
        value = str(raw).strip().replace("/", "-")
        if len(value) == 8 and value.isdigit():
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError(f"invalid date: {raw}")
        out.append(value)
    if out != sorted(set(out)):
        raise ValueError("dates must be unique and in ascending order")
    return out


def _previous_open_day(shadow, first_date: str) -> str:
    days = shadow.load_trade_calendar(need_date=first_date)
    previous = [day for day in days if day < first_date]
    if not previous:
        raise RuntimeError(f"no previous open day before {first_date}")
    return previous[-1]


def _backup_and_clear(shadow, dates: list[str], backup_root: Path) -> dict:
    zone = shadow.EXECUTION_ZONE
    moved = []
    backup_execution = backup_root / "execution"
    backup_execution.mkdir(parents=True, exist_ok=True)
    for date in dates:
        target_dir = zone / date
        if target_dir.exists():
            if target_dir.is_symlink():
                raise RuntimeError(f"refusing to clear symlink: {target_dir}")
            destination = backup_execution / date
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target_dir), str(destination))
            moved.append(str(destination))
        event_path = zone / "events" / f"{date}.jsonl"
        if event_path.exists():
            destination = backup_execution / "events" / event_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(event_path), str(destination))
            moved.append(str(destination))

    summary_path = zone / "nav" / "nav_summary.json"
    if summary_path.exists():
        destination = backup_root / "nav_summary.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(summary_path, destination)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        days = summary.get("days") or {}
        for date in dates:
            days.pop(date, None)
        summary["days"] = days
        summary["generated_at"] = datetime.now().isoformat(timespec="seconds")
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {"backup_root": str(backup_root), "moved": moved}


def rebuild(dates: list[str], *, execute: bool) -> dict:
    from scripts.ops import run_daily_shadow as shadow

    first_date = dates[0]
    previous_day = _previous_open_day(shadow, first_date)
    plan = {
        "dates": dates,
        "previous_sell_business_date": previous_day,
        "steps": [
            {"task": "alpha_signal_sell_precommit", "business_date": previous_day},
            *[
                {"task": task, "business_date": date}
                for date in dates
                for task in (
                    "alpha_signal_precommit",
                    "alpha_signal_execution_reconcile",
                    "alpha_signal_sell_precommit",
                    "alpha_signal_nav",
                )
            ],
        ],
    }
    if not execute:
        return {"status": "DRY_RUN", **plan}

    backup_parent = shadow.EVIDENCE_ROOT / "exports" / "forward_shadow_evidence" / "repair_backups"
    backup_root = backup_parent / (
        "weekly_shadow_rebuild_" + datetime.now().strftime("%Y%m%dT%H%M%S")
    )
    backup_root.mkdir(parents=True, exist_ok=False)
    backup = _backup_and_clear(shadow, dates, backup_root)

    results = []
    results.append({
        "task": "alpha_signal_sell_precommit",
        "business_date": previous_day,
        "result": shadow.sell_precommit(business_date=previous_day),
    })
    for date in dates:
        results.append({
            "task": "alpha_signal_precommit",
            "business_date": date,
            "result": shadow.precommit(execution_date=date),
        })
        results.append({
            "task": "alpha_signal_execution_reconcile",
            "business_date": date,
            "result": shadow.reconcile_from_package(execution_date=date),
        })
        results.append({
            "task": "alpha_signal_sell_precommit",
            "business_date": date,
            "result": shadow.sell_precommit(business_date=date),
        })
        results.append({
            "task": "alpha_signal_nav",
            "business_date": date,
            "result": shadow.nav(execution_date=date),
        })

    machine = shadow.replay_all(shadow.EXECUTION_ZONE, as_of_date=dates[-1])
    return {
        "status": "REBUILT",
        **plan,
        **backup,
        "results": results,
        "replay": {
            "orders": len(machine.orders),
            "positions": len(machine.positions),
            "completed_round_trips": machine.completed_round_trips(),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", dest="dates", action="append")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        rebuild(_iso_dates(args.dates), execute=args.execute),
        ensure_ascii=False,
        indent=2,
        default=str,
    ))


if __name__ == "__main__":
    main()
