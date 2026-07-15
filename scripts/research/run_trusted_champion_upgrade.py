"""CLI for read-only evidence building and sequential promotion audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.trusted_champion_rotation import load_rotation_config
from scripts.research.trusted_champion_upgrade import (
    approval_patch, decision_dict, evaluate_promotion, load_evidence, update_shadow_ledger,
    write_immutable_evidence,
)
from scripts.research_full_pool_liquidity_strategies import load_prices, load_scores
from runtime.corporate_action_snapshot import CorporateActionSnapshotBuilder


def _default_output(kind: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(PROJECT_ROOT / "exports" / "trusted_champion_upgrade" / f"{stamp}_{kind}")


def _db_url() -> str | None:
    return os.getenv("CHENYIYUN_DB_URL")


FORMAL_STRATEGIES = (
    "baseline_full_liquidity_detail_vol_position",
    "baseline_full_liquidity_detail_market_gate",
    "baseline_full_liquidity",
    "tiered_liquidity_then_bs_v2",
    "adaptive_market_style",
)


def _git_state() -> dict[str, object]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip())
        return {"commit": commit, "clean": not dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": "UNKNOWN", "clean": False}


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _freeze_formal_inputs(engine, output: Path, start_date: str, end_date: str | None) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(f"immutable formal input directory already exists: {output}")
    output.mkdir(parents=True)
    cutoff = end_date or str(datetime.now().date())
    scores = load_scores(engine, start_date=start_date, end_date=cutoff, min_pool_size=5000)
    if scores.empty:
        raise RuntimeError("formal_score_snapshot_empty")
    prices = load_prices(engine, scores["trade_date"].min(), scores["trade_date"].max(), 10)
    if prices.empty:
        raise RuntimeError("formal_price_snapshot_empty")
    builder = CorporateActionSnapshotBuilder(engine)
    corporate = builder.build_corporate_actions(start_date, cutoff)
    lifecycle = builder.build_lifecycle_panel(start_date, cutoff)
    if corporate.empty or lifecycle.empty:
        raise RuntimeError("formal_corporate_or_lifecycle_snapshot_empty")
    if not corporate["source_complete"].astype(bool).all():
        raise RuntimeError("corporate_action_source_incomplete")
    score_path = output / "scores.csv"
    price_path = output / "prices.csv"
    corporate_path = output / "corporate_actions.csv"
    lifecycle_path = output / "security_lifecycle.csv"
    calendar_path = output / "trade_calendar.csv"
    score_identity = scores[["symbol", "trade_date"]].copy()
    score_identity["symbol"] = score_identity["symbol"].astype(str).str.zfill(6)
    score_identity["trade_date"] = pd.to_datetime(score_identity["trade_date"], errors="coerce").dt.date
    lifecycle_identity = lifecycle[["symbol", "trade_date", "is_listed"]].copy()
    lifecycle_identity["symbol"] = lifecycle_identity["symbol"].astype(str).str.zfill(6)
    lifecycle_identity["trade_date"] = pd.to_datetime(lifecycle_identity["trade_date"], errors="coerce").dt.date
    survivor_audit = score_identity.merge(lifecycle_identity, on=["symbol", "trade_date"], how="left")
    survivorship_violations = int(survivor_audit["is_listed"].fillna(0).ne(1).sum())
    if survivorship_violations:
        raise RuntimeError("historical_universe_survivorship_violation")
    scores.to_csv(score_path, index=False)
    prices.to_csv(price_path, index=False)
    corporate.to_csv(corporate_path, index=False)
    lifecycle.to_csv(lifecycle_path, index=False)
    pd.DataFrame({"trade_date": sorted(lifecycle_identity["trade_date"].dropna().unique())}).to_csv(calendar_path, index=False)
    pit = _write_pit_inputs(engine, output / "pit", start_date, cutoff)
    source_payload = {
        "query_cutoff": cutoff, "start_date": start_date,
        "score_rows": len(scores), "price_rows": len(prices),
        "corporate_rows": len(corporate), "lifecycle_rows": len(lifecycle),
        "survivorship_violations": survivorship_violations,
    }
    source_sha = hashlib.sha256(json.dumps(source_payload, sort_keys=True).encode()).hexdigest()
    manifest = {
        "snapshot_schema_version": "strict_corporate_lifecycle_snapshot_v2",
        "dataset_version": f"trusted_champion_formal_{start_date}_{cutoff}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": source_sha,
        "lifecycle_source_sha256": source_sha,
        "snapshot_sha256": _hash_file(corporate_path),
        "lifecycle_snapshot_sha256": _hash_file(lifecycle_path),
        "scores_sha256": _hash_file(score_path), "prices_sha256": _hash_file(price_path),
        "trade_calendar_sha256": _hash_file(calendar_path),
        "data_start": str(pd.to_datetime(scores["trade_date"]).min().date()),
        "data_end": str(pd.to_datetime(scores["trade_date"]).max().date()),
        "rows": source_payload, "pit": pit,
    }
    manifest_path = output / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path), "score_path": str(score_path),
            "price_path": str(price_path), "corporate_path": str(corporate_path),
            "lifecycle_path": str(lifecycle_path), "pit_dir": str(output / "pit")}


def _write_recursive_manifest(output: Path, payload: dict[str, object]) -> None:
    status_path = output / "formal_run_status.json"
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    hashes = {
        str(path.relative_to(output)): _hash_file(path)
        for path in sorted(output.rglob("*")) if path.is_file() and path.name != "formal_manifest.json"
    }
    (output / "formal_manifest.json").write_text(json.dumps({
        "strategy_id": "trusted_champion_rotation_v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": hashes, "production_mutation_enabled": False, "order_generation_enabled": False,
        "broker_api_enabled": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_pit_inputs(engine, output: Path, start_date: str, end_date: str | None) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(f"immutable PIT directory already exists: {output}")
    output.mkdir(parents=True)
    start_key = int(pd.Timestamp(start_date).strftime("%Y%m%d"))
    end_key = int(pd.Timestamp(end_date or datetime.now().date()).strftime("%Y%m%d"))
    announcements = pd.read_sql(text("""
        SELECT DISTINCT ts_code AS symbol, ann_date
        FROM tushare_stock.dwd_fina_indicator
        WHERE ann_date BETWEEN :start_key AND :end_key AND ann_date <= :end_key
    """), engine, params={"start_key": start_key, "end_key": end_key})
    universe = pd.read_sql(text("""
        SELECT trade_date, COUNT(DISTINCT ts_code) AS eligible_universe_count
        FROM tushare_stock.ods_daily
        WHERE trade_date BETWEEN :start_key AND :end_key AND COALESCE(vol, 0) > 0
        GROUP BY trade_date ORDER BY trade_date
    """), engine, params={"start_key": start_key, "end_key": end_key})
    announcements["symbol"] = announcements["symbol"].astype(str).str.extract(r"(\d{6})", expand=False)
    announcements["ann_date"] = pd.to_datetime(
        announcements["ann_date"].astype(str), format="%Y%m%d", errors="coerce"
    )
    universe["trade_date"] = pd.to_datetime(
        universe["trade_date"].astype(str), format="%Y%m%d", errors="coerce"
    )
    if announcements[["symbol", "ann_date"]].isna().any().any() or universe.empty:
        raise RuntimeError("PIT announcement or universe extract is incomplete")
    paths = {
        "earnings_announcements.csv": announcements,
        "eligible_universe.csv": universe,
    }
    hashes: dict[str, str] = {}
    for name, frame in paths.items():
        path = output / name
        frame.to_csv(path, index=False)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "status": "PIT_INPUTS_EXTRACTED_READ_ONLY", "query_cutoff": end_key,
        "start_key": start_key, "rows": {name: int(len(frame)) for name, frame in paths.items()},
        "sha256": hashes, "credentials_persisted": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_data_evidence(args: argparse.Namespace) -> Path:
    status = {
        "status": "BLOCKED_DATA_SOURCE", "stage": "BLOCKED",
        "blocked_stage": "RESEARCH_BACKTEST", "database_source_verified": False,
        "start_date": args.start_date, "end_date": args.end_date,
        "read_only": True, "credentials_persisted": False,
    }
    url = _db_url()
    if not url:
        status["blockers"] = ["CHENYIYUN_DB_URL_UNSET"]
        return write_immutable_evidence(args.output_dir, {"data_source_status.json": status})
    try:
        engine = create_engine(url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            tables = connection.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='tushare_stock' AND table_name IN "
                "('ads_strategy_stock_final_di','ods_daily','dwd_fina_indicator','ods_dividend')"
            )).scalar_one()
        status.update({
            "status": "READY_FOR_STRICT_ACCOUNT_BUILD" if int(tables) == 4 else "BLOCKED_DATA_SOURCE",
            "database_source_verified": int(tables) == 4,
            "required_tables_found": int(tables), "required_tables_expected": 4,
            "blockers": [] if int(tables) == 4 else ["required_database_tables_missing"],
        })
    except Exception as exc:
        status["blockers"] = ["database_read_failed"]
        status["error_type"] = type(exc).__name__
    output_path = Path(args.output_dir).resolve()
    pit_dir = output_path.parent / f"{output_path.name}_pit_inputs"
    account_dir = output_path.parent / f"{output_path.name}_strict_account_bundle"
    if status["database_source_verified"] and not args.dry_run:
        status["pit_inputs"] = _write_pit_inputs(engine, pit_dir, args.start_date, args.end_date)
        status["pit_input_dir"] = str(pit_dir)
    if status["database_source_verified"] and args.run_backtest and not args.dry_run:
        status["strict_account_bundle_dir"] = str(account_dir)
    output = write_immutable_evidence(output_path, {"data_source_status.json": status})
    if status["database_source_verified"] and args.run_backtest and not args.dry_run:
        command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "research_trusted_strategy_account_backtest.py"),
            "--start-date", args.start_date, "--execution-mode", "strict_t1_open_precommit",
            "--strategies", (
                "baseline_full_liquidity_detail_vol_position,"
                "baseline_full_liquidity_detail_market_gate,baseline_full_liquidity,"
                "tiered_liquidity_then_bs_v2,adaptive_market_style"
            ),
            "--trade-cost-rate", "0.00075", "--slippage-rate", "0.001",
            "--output-dir", str(account_dir),
        ]
        if args.end_date:
            command.extend(["--end-date", args.end_date])
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    return output


def formal_run(args: argparse.Namespace) -> Path:
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"immutable formal output directory already exists: {output}")
    git_state = _git_state()
    blockers: list[str] = []
    if not git_state["clean"]:
        blockers.append("DIRTY_WORKTREE")
    url = _db_url()
    if not url:
        blockers.append("CHENYIYUN_DB_URL_UNSET")
    if blockers:
        return write_immutable_evidence(output, {"formal_run_status.json": {
            "status": "BLOCKED", "blocked_stage": "RESEARCH_BACKTEST", "blockers": blockers,
            "git": git_state, "production_modified": False, "orders_generated": False,
        }})
    output.mkdir(parents=True)
    status: dict[str, object] = {
        "status": "BLOCKED", "blocked_stage": "RESEARCH_BACKTEST", "git": git_state,
        "production_modified": False, "orders_generated": False, "broker_api_used": False,
    }
    try:
        engine = create_engine(str(url))
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        inputs = _freeze_formal_inputs(engine, output / "inputs", args.start_date, args.end_date)
        status["inputs"] = inputs
        if str(inputs["data_start"]) > str(args.start_date):
            status["blockers"] = ["HISTORICAL_RANGE_INCOMPLETE"]
            _write_recursive_manifest(output, status)
            return output
        account_dir = output / "strict_account_bundle"
        account_command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "research_trusted_strategy_account_backtest.py"),
            "--start-date", args.start_date, "--execution-mode", "strict_t1_open_precommit",
            "--require-verified-evidence", "--strategies", ",".join(FORMAL_STRATEGIES),
            "--trade-cost-rate", "0.00075", "--slippage-rate", "0.001",
            "--scores-snapshot", str(inputs["score_path"]), "--prices-snapshot", str(inputs["price_path"]),
            "--corporate-action-snapshot", str(inputs["corporate_path"]),
            "--corporate-action-manifest", str(inputs["manifest_path"]),
            "--security-lifecycle-snapshot", str(inputs["lifecycle_path"]),
            "--security-lifecycle-manifest", str(inputs["manifest_path"]),
            "--output-dir", str(account_dir),
        ]
        if args.end_date:
            account_command.extend(["--end-date", args.end_date])
        subprocess.run(account_command, cwd=PROJECT_ROOT, check=True)
        rotation_dir = output / "rotation"
        rotation_command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "research" / "run_trusted_champion_rotation.py"),
            "--source-dir", str(account_dir),
            "--earnings-announcements", str(Path(str(inputs["pit_dir"])) / "earnings_announcements.csv"),
            "--eligible-universe", str(Path(str(inputs["pit_dir"])) / "eligible_universe.csv"),
            "--output-dir", str(rotation_dir),
        ]
        subprocess.run(rotation_command, cwd=PROJECT_ROOT, check=True)
        report = json.loads((account_dir / "trusted_account_backtest_report.json").read_text(encoding="utf-8"))
        provenance = report.get("provenance") or {}
        acceptance = json.loads((rotation_dir / "champion_rotation_acceptance.json").read_text(encoding="utf-8"))
        evidence = {
            "database_source_verified": True, "data_start": inputs["data_start"],
            "acceptance_passed": bool(acceptance.get("passed")),
            "strict_ledger_status": provenance.get("strict_ledger_status"),
            "strict_evidence_derived": provenance.get("strict_evidence_derived"),
            "corporate_action_coverage": provenance.get("corporate_action_coverage"),
            "lifecycle_session_coverage": provenance.get("lifecycle_session_coverage"),
            "t_plus_one_violations": provenance.get("t_plus_1_fill_violations"),
            "order_conservation_errors": provenance.get("order_conservation_errors"),
            "reproducibility_status": provenance.get("reproducibility_status"),
        }
        decision = evaluate_promotion("RESEARCH_BACKTEST", evidence, load_rotation_config(args.config))
        status.update({"status": decision.status, "evidence": evidence, "blockers": list(decision.blockers),
                       "next_stage": decision.next_stage, "account_dir": str(account_dir),
                       "rotation_dir": str(rotation_dir)})
        if decision.eligible:
            patch = approval_patch(decision, load_rotation_config(args.config))
            (output / "pending_manual_approval_patch.json").write_text(
                json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except Exception as exc:
        status["blockers"] = ["FORMAL_RUN_FAILED"]
        status["error_type"] = type(exc).__name__
        status["error_code"] = str(exc).split("\n", 1)[0][:160]
    _write_recursive_manifest(output, status)
    return output


def audit(args: argparse.Namespace) -> Path:
    config = load_rotation_config(args.config)
    evidence = load_evidence(args.evidence)
    decision = evaluate_promotion(args.current_stage, evidence, config)
    payloads = {"promotion_decision.json": decision_dict(decision)}
    if decision.eligible:
        payloads["pending_manual_approval_patch.json"] = approval_patch(decision, config)
    return write_immutable_evidence(args.output_dir, payloads)


def shadow_update(args: argparse.Namespace) -> Path:
    config = load_rotation_config(args.config)
    daily = load_evidence(args.daily_evidence)
    prior: list[dict[str, object]] = []
    if args.prior_ledger:
        raw = json.loads(Path(args.prior_ledger).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("prior shadow ledger must be a JSON list")
        prior = raw
    ledger, status = update_shadow_ledger(prior, daily, args.current_stage, config)
    payloads = {"shadow_ledger.json": ledger, "shadow_status.json": status}
    decision = evaluate_promotion(args.current_stage, status.get("evidence") or status, config)
    if decision.eligible:
        payloads["pending_manual_approval_patch.json"] = approval_patch(decision, config)
    return write_immutable_evidence(args.output_dir, payloads)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trusted champion upgrade governance; never mutates production.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "trusted_champion_rotation_v1.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-data")
    build.add_argument("--start-date", default="2013-01-01")
    build.add_argument("--end-date", default=None)
    build.add_argument("--output-dir", default=_default_output("data"))
    build.add_argument("--dry-run", action="store_true")
    build.add_argument("--run-backtest", action="store_true")
    check = sub.add_parser("audit")
    check.add_argument("--current-stage", required=True)
    check.add_argument("--evidence", required=True)
    check.add_argument("--output-dir", default=_default_output("audit"))
    shadow = sub.add_parser("shadow-update")
    shadow.add_argument("--current-stage", required=True, choices=["SHADOW_DISABLED", "SHADOW_ENABLED"])
    shadow.add_argument("--daily-evidence", required=True)
    shadow.add_argument("--prior-ledger", default=None)
    shadow.add_argument("--output-dir", default=_default_output("shadow"))
    formal = sub.add_parser("formal-run")
    formal.add_argument("--start-date", default="2013-01-01")
    formal.add_argument("--end-date", default=None)
    formal.add_argument("--output-dir", default=_default_output("formal"))
    args = parser.parse_args()
    if args.command == "formal-run":
        output = formal_run(args)
    elif args.command == "build-data":
        output = build_data_evidence(args)
    elif args.command == "shadow-update":
        output = shadow_update(args)
    else:
        output = audit(args)
    print(json.dumps({
        "status": "COMPLETE_FAIL_CLOSED", "output_dir": str(output),
        "production_modified": False, "orders_generated": False, "broker_api_used": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
