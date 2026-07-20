#!/usr/bin/env python3
"""Fail-closed, read-only production strategy tournament.

The command evaluates explicit immutable evidence directories.  It never
writes production tables, creates orders, changes release state, or calls a
broker.  Missing evidence makes a candidate INELIGIBLE; aliases and fallback
strategies are deliberately not accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import cmp_to_key
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from sqlalchemy.engine import make_url

from runtime.contracts import ReleaseIdentity


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "strategy_tournament.yaml"
RELEASE_REGISTRY = PROJECT_ROOT / "config" / "strategy_release_registry.yaml"
STRATEGY_CARDS = PROJECT_ROOT / "strategy_cards"
EVIDENCE_CONTRACT = "tournament_evidence.json"


@dataclass
class CandidateResult:
    strategy_id: str
    source_dir: str = ""
    candidate_type: str = "single"
    underlying_releases: list[str] = field(default_factory=list)
    complexity_rank: int = 1
    metrics: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, bool] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    input_hashes: dict[str, str] = field(default_factory=dict)
    eligible: bool = False
    rank: int | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "source_dir": self.source_dir,
            "candidate_type": self.candidate_type,
            "underlying_releases": self.underlying_releases,
            "complexity_rank": self.complexity_rank,
            "metrics": self.metrics,
            "gates": self.gates,
            "blockers": self.blockers,
            "eligible": self.eligible,
            "rank": self.rank,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "UNRESOLVED"


def _worktree_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _candidate_universe(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    universe: dict[str, dict[str, Any]] = {}
    registry = _load_yaml(RELEASE_REGISTRY).get("releases", {})
    for strategy_id, release in registry.items():
        if str(release.get("lifecycle_status", "")).upper() == "LEGACY":
            continue
        universe[str(strategy_id)] = {
            "candidate_type": "single",
            "underlying_releases": [str(release.get("release_id") or strategy_id)],
            "complexity_rank": 1,
        }
    for path in sorted(STRATEGY_CARDS.glob("*.yaml")):
        card = _load_yaml(path)
        strategy_id = str(card.get("strategy_id") or "")
        if strategy_id and str(card.get("status", "")).upper() != "LEGACY":
            universe.setdefault(
                strategy_id,
                {"candidate_type": "single", "underlying_releases": [], "complexity_rank": 1},
            )
    universe.update(config.get("composite_candidates", {}))
    return universe


def _parse_sources(values: list[str]) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError("--source must be STRATEGY_ID=DIRECTORY")
        strategy_id, raw_path = raw.split("=", 1)
        strategy_id = strategy_id.strip()
        if not strategy_id or strategy_id in sources:
            raise ValueError(f"duplicate or empty strategy source: {strategy_id!r}")
        path = Path(raw_path).expanduser()
        sources[strategy_id] = path if path.is_absolute() else PROJECT_ROOT / path
    return sources


def _require_readonly_environment(url_env: str, attestation_env: str) -> dict[str, str]:
    raw_url = os.getenv(url_env, "").strip()
    if not raw_url:
        raise RuntimeError(f"missing read-only database URL environment: {url_env}")
    attestation = os.getenv(attestation_env, "").strip().lower()
    if attestation not in {"1", "true", "yes"}:
        raise RuntimeError(f"missing read-only attestation: set {attestation_env}=1")
    parsed = make_url(raw_url)
    username = str(parsed.username or "").lower()
    if username in {"root", "admin", "administrator"}:
        raise RuntimeError("privileged database users are forbidden for tournament validation")
    return {"url_env": url_env, "attestation_env": attestation_env, "username": username}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def _locate_nav(source: Path) -> tuple[Path | None, pd.DataFrame]:
    candidates = (
        source / "trusted_account_backtest_nav.csv",
        source / "stitched_oos_nav.csv",
        source / "daily_nav.parquet",
        source / "A9" / "daily_nav.parquet",
    )
    for path in candidates:
        if not path.is_file():
            continue
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else _read_csv(path)
        if not frame.empty:
            return path, frame
    return None, pd.DataFrame()


def _filter_exact_strategy(frame: pd.DataFrame, strategy_id: str) -> pd.DataFrame:
    if frame.empty or "strategy" not in frame.columns:
        return frame.copy()
    return frame[frame["strategy"].astype(str).eq(strategy_id)].copy()


def _nav_metrics(nav: pd.DataFrame, as_of: date, oos_start: date) -> tuple[dict[str, Any], pd.DataFrame]:
    date_col = next((column for column in ("trade_date", "date", "signal_date") if column in nav), None)
    value_col = next((column for column in ("total_equity", "nav", "equity") if column in nav), None)
    if not date_col or not value_col:
        raise ValueError("NAV requires a date column and total_equity/nav/equity")
    frame = nav[[date_col, value_col]].rename(columns={date_col: "trade_date", value_col: "equity"})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    frame = frame.dropna().drop_duplicates("trade_date").sort_values("trade_date")
    frame = frame[
        (frame["trade_date"].dt.date >= oos_start)
        & (frame["trade_date"].dt.date <= as_of)
    ].copy()
    if len(frame) < 2 or float(frame["equity"].iloc[0]) <= 0:
        raise ValueError("insufficient OOS NAV rows")
    returns = frame["equity"].pct_change().dropna()
    total_return = float(frame["equity"].iloc[-1] / frame["equity"].iloc[0] - 1.0)
    annualized_return = float((1.0 + total_return) ** (252 / len(returns)) - 1.0)
    drawdown = frame["equity"] / frame["equity"].cummax() - 1.0
    max_drawdown = float(drawdown.min())
    calmar = annualized_return / abs(max_drawdown) if max_drawdown < -1e-12 else 999.0
    tail_count = max(1, math.ceil(len(returns) * 0.05))
    cvar_95 = float(returns.nsmallest(tail_count).mean())
    quarterly = frame.assign(quarter=frame["trade_date"].dt.to_period("Q")).groupby("quarter")["equity"].agg(["first", "last"])
    quarterly["quarter_return"] = quarterly["last"] / quarterly["first"] - 1.0
    quarterly_rows = quarterly.reset_index()[["quarter", "quarter_return"]]
    quarterly_rows["quarter"] = quarterly_rows["quarter"].astype(str)
    return {
        "window_start": frame["trade_date"].iloc[0].date().isoformat(),
        "window_end": frame["trade_date"].iloc[-1].date().isoformat(),
        "trading_days": int(len(frame)),
        "net_oos_total_return": total_return,
        "net_oos_annualized_return": annualized_return,
        "max_drawdown": max_drawdown,
        "positive_quarter_ratio": float((quarterly["quarter_return"] > 0).mean()),
        "calmar": float(calmar),
        "cvar_95_daily": cvar_95,
    }, quarterly_rows


def _input_hashes(source: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".json", ".csv", ".parquet", ".yaml", ".yml"}:
            hashes[str(path.relative_to(source))] = _sha256(path)
    return hashes


def _contract_value(contract: dict[str, Any], key: str, default: Any = None) -> Any:
    metrics = contract.get("metrics", {}) if isinstance(contract.get("metrics"), dict) else {}
    return metrics.get(key, contract.get(key, default))


def _load_candidate(
    strategy_id: str,
    meta: dict[str, Any],
    source: Path | None,
    as_of: date,
    tournament: dict[str, Any],
) -> tuple[CandidateResult, pd.DataFrame]:
    result = CandidateResult(
        strategy_id=strategy_id,
        candidate_type=str(meta.get("candidate_type", "single")),
        underlying_releases=[str(value) for value in meta.get("underlying_releases", [])],
        complexity_rank=int(meta.get("complexity_rank", 1)),
    )
    if source is None:
        result.blockers.append("missing_explicit_source")
        return result, pd.DataFrame()
    result.source_dir = str(source)
    if not source.is_dir():
        result.blockers.append("source_directory_missing")
        return result, pd.DataFrame()
    contract_path = source / EVIDENCE_CONTRACT
    if not contract_path.is_file():
        result.blockers.append(f"missing_{EVIDENCE_CONTRACT}")
        return result, pd.DataFrame()
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result.blockers.append(f"invalid_evidence_contract:{type(exc).__name__}")
        return result, pd.DataFrame()
    if str(contract.get("strategy_id")) != strategy_id:
        result.blockers.append("strategy_identity_mismatch")
        return result, pd.DataFrame()
    try:
        identity = ReleaseIdentity.model_validate(contract.get("release_identity"))
    except Exception as exc:
        result.blockers.append(f"incomplete_release_identity:{type(exc).__name__}")
        return result, pd.DataFrame()
    if identity.strategy_id != strategy_id:
        result.blockers.append("release_identity_strategy_mismatch")
        return result, pd.DataFrame()
    if abs(identity.initial_capital - float(tournament["initial_cash"])) > 0.005:
        result.blockers.append("release_identity_initial_capital_mismatch")
        return result, pd.DataFrame()
    required_experiments = set(tournament.get("required_experiments") or [])
    actual_experiments = set(contract.get("experiments") or [])
    missing_experiments = sorted(required_experiments - actual_experiments)
    if missing_experiments:
        result.blockers.append("missing_matched_experiments:" + ",".join(missing_experiments))
        return result, pd.DataFrame()
    nav_path, nav = _locate_nav(source)
    nav = _filter_exact_strategy(nav, strategy_id)
    if nav_path is None or nav.empty:
        result.blockers.append("exact_strategy_nav_missing")
        return result, pd.DataFrame()
    try:
        metrics, quarters = _nav_metrics(
            nav, as_of, pd.Timestamp(tournament["oos_start"]).date()
        )
    except ValueError as exc:
        result.blockers.append(f"invalid_nav:{exc}")
        return result, pd.DataFrame()
    result.metrics.update(metrics)
    for key in (
        "dsr_confidence", "pbo", "corporate_action_coverage",
        "t_plus_one_violations", "stress_annualized_return",
        "max_single_position_weight", "max_single_industry_weight",
        "max_correlated_theme_weight", "max_top2_risk_contribution", "max_single_order_adv_ratio",
        "turnover", "total_cost", "strict_ledger_status", "evidence_status",
        "dual_ledger_status", "worst_20d_return", "cost_after_alpha",
        "top5_trade_profit_dependency", "market_regime_count",
        "random_baseline_passed", "reverse_baseline_passed",
        "quarterly_random_baseline_passed", "factor_ablation_status",
        "full_history_start", "data_complete_through", "capacity_100k_pass",
        "capacity_500k_pass", "trade_day_coverage",
    ):
        result.metrics[key] = _contract_value(contract, key)
    result.input_hashes = _input_hashes(source)
    if nav_path:
        result.metrics["nav_file"] = str(nav_path.relative_to(source))
    quarters.insert(0, "strategy_id", strategy_id)
    return result, quarters


def _gate_candidate(
    result: CandidateResult,
    baseline: CandidateResult | None,
    tournament: dict[str, Any],
) -> None:
    if result.blockers:
        result.eligible = False
        return
    gates = tournament["hard_gates"]
    metrics = result.metrics
    required = (
        "dsr_confidence", "pbo", "corporate_action_coverage", "t_plus_one_violations",
        "stress_annualized_return", "max_single_position_weight",
        "max_single_industry_weight", "max_correlated_theme_weight", "max_top2_risk_contribution",
        "max_single_order_adv_ratio", "full_history_start", "data_complete_through",
        "capacity_100k_pass", "capacity_500k_pass", "trade_day_coverage",
        "dual_ledger_status", "worst_20d_return", "cost_after_alpha",
        "top5_trade_profit_dependency", "market_regime_count",
        "random_baseline_passed", "reverse_baseline_passed",
        "quarterly_random_baseline_passed", "factor_ablation_status",
    )
    missing = [key for key in required if metrics.get(key) is None]
    if missing:
        result.blockers.append("missing_contract_fields:" + ",".join(missing))
        result.eligible = False
        return

    full_history_ok = pd.Timestamp(metrics["full_history_start"]).date() <= pd.Timestamp(tournament["full_history_start"]).date()
    evidence_end_matches = str(metrics["data_complete_through"]) == str(metrics["window_end"])
    baseline_return = baseline.metrics.get("net_oos_annualized_return") if baseline else None
    result.gates = {
        "reproducible_evidence": metrics.get("evidence_status") == "REPRODUCIBLE",
        "strict_ledger_verified": metrics.get("strict_ledger_status") == "VERIFIED",
        "dual_ledger_verified": metrics.get("dual_ledger_status") == "VERIFIED",
        "full_history_coverage": bool(full_history_ok),
        "matched_data_end": bool(evidence_end_matches),
        "trade_day_coverage": float(metrics["trade_day_coverage"]) >= 0.98,
        "absolute_return": float(metrics["net_oos_annualized_return"]) >= float(gates["min_net_oos_annualized_return"]),
        "return_above_baseline": baseline_return is not None and float(metrics["net_oos_annualized_return"]) > float(baseline_return),
        "max_drawdown": abs(float(metrics["max_drawdown"])) <= float(gates["max_oos_drawdown_abs"]),
        "positive_quarters": float(metrics["positive_quarter_ratio"]) >= float(gates["min_positive_quarter_ratio"]),
        "dsr": float(metrics["dsr_confidence"]) >= float(gates["min_dsr_confidence"]),
        "pbo": float(metrics["pbo"]) <= float(gates["max_pbo"]),
        "corporate_actions": float(metrics["corporate_action_coverage"]) >= float(gates["min_corporate_action_coverage"]),
        "t_plus_one": int(metrics["t_plus_one_violations"]) <= int(gates["max_t_plus_one_violations"]),
        "stress_return": float(metrics["stress_annualized_return"]) >= float(gates["min_stress_annualized_return"]),
        "capacity_100k": bool(metrics["capacity_100k_pass"]),
        "capacity_500k": bool(metrics["capacity_500k_pass"]),
        "single_position": float(metrics["max_single_position_weight"]) <= float(gates["max_single_position_weight"]),
        "industry": float(metrics["max_single_industry_weight"]) <= float(gates["max_single_industry_weight"]),
        "theme": float(metrics["max_correlated_theme_weight"]) <= float(gates["max_correlated_theme_weight"]),
        "top2_risk": float(metrics["max_top2_risk_contribution"]) <= float(gates["max_top2_risk_contribution"]),
        "adv": float(metrics["max_single_order_adv_ratio"]) <= float(gates["max_single_order_adv_ratio"]),
        "cost_after_alpha": (
            float(metrics["cost_after_alpha"]) > 0
            if bool(gates.get("require_positive_cost_after_alpha", True)) else True
        ),
        "top5_trade_dependency": float(metrics["top5_trade_profit_dependency"]) <= float(gates["max_top5_trade_profit_dependency"]),
        "market_regimes": int(metrics["market_regime_count"]) >= int(gates["min_market_regime_count"]),
        "random_baseline": bool(metrics["random_baseline_passed"]),
        "reverse_baseline": bool(metrics["reverse_baseline_passed"]),
        "quarterly_random_baseline": bool(metrics["quarterly_random_baseline_passed"]),
        "factor_ablation": (
            str(metrics["factor_ablation_status"]) == "COMPLETE"
            if bool(gates.get("require_factor_ablation_complete", True)) else True
        ),
    }
    if result.strategy_id == tournament["baseline_strategy"]:
        result.gates["return_above_baseline"] = False
    result.blockers.extend(name for name, passed in result.gates.items() if not passed)
    result.eligible = not result.blockers


def _compare_candidates(left: CandidateResult, right: CandidateResult) -> int:
    left_return = float(left.metrics["net_oos_annualized_return"])
    right_return = float(right.metrics["net_oos_annualized_return"])
    if abs(left_return - right_return) >= 0.02:
        return -1 if left_return > right_return else 1
    comparisons = (
        (-abs(float(left.metrics["max_drawdown"])), -abs(float(right.metrics["max_drawdown"]))),
        (float(left.metrics["calmar"]), float(right.metrics["calmar"])),
        (float(left.metrics["cvar_95_daily"]), float(right.metrics["cvar_95_daily"])),
        (-float(left.metrics.get("total_cost") or 0.0), -float(right.metrics.get("total_cost") or 0.0)),
        (-float(left.metrics.get("turnover") or 0.0), -float(right.metrics.get("turnover") or 0.0)),
        (-left.complexity_rank, -right.complexity_rank),
    )
    for left_value, right_value in comparisons:
        if abs(left_value - right_value) > 1e-12:
            return -1 if left_value > right_value else 1
    return -1 if left.strategy_id < right.strategy_id else 1 if left.strategy_id > right.strategy_id else 0


def _baseline_reference_valid(result: CandidateResult | None) -> bool:
    """The incumbent is a comparator, not a promotion candidate.

    It may miss the challenger's return/drawdown objectives, but its identity,
    data, ledger and statistical evidence must still be trustworthy.
    """
    if result is None or any(blocker.startswith("missing_contract_fields") for blocker in result.blockers):
        return False
    required = {
        "reproducible_evidence", "strict_ledger_verified", "dual_ledger_verified", "full_history_coverage",
        "matched_data_end", "trade_day_coverage", "dsr", "pbo",
        "corporate_actions", "t_plus_one", "capacity_100k", "capacity_500k",
    }
    return required.issubset(result.gates) and all(result.gates[name] for name in required)


def evaluate_tournament(
    *,
    as_of: date,
    config_path: Path,
    sources: dict[str, Path],
    precheck_only: bool,
    db_environment: dict[str, str] | None,
) -> tuple[dict[str, Any], list[CandidateResult], pd.DataFrame]:
    raw_config = _load_yaml(config_path)
    tournament = raw_config["tournament"]
    universe = _candidate_universe(tournament)
    unknown_sources = sorted(set(sources) - set(universe))
    if unknown_sources:
        raise ValueError(f"sources not registered as strategies: {unknown_sources}")

    results: list[CandidateResult] = []
    quarter_frames: list[pd.DataFrame] = []
    for strategy_id, meta in sorted(universe.items()):
        result, quarters = _load_candidate(
            strategy_id, meta, sources.get(strategy_id), as_of, tournament
        )
        results.append(result)
        if not quarters.empty:
            quarter_frames.append(quarters)

    baseline = next((item for item in results if item.strategy_id == tournament["baseline_strategy"]), None)
    if baseline is not None:
        _gate_candidate(baseline, baseline, tournament)
    baseline_reference = baseline if _baseline_reference_valid(baseline) else None
    for result in results:
        if result is not baseline:
            _gate_candidate(result, baseline_reference, tournament)
    if baseline is not None:
        baseline.eligible = False
        if "baseline_reference_only" not in baseline.blockers:
            baseline.blockers.append("baseline_reference_only")

    eligible = sorted((item for item in results if item.eligible), key=cmp_to_key(_compare_candidates))
    for index, result in enumerate(eligible, start=1):
        result.rank = index
    winner = eligible[0] if eligible else None
    status = "PRECHECK_ONLY" if precheck_only else "REPRODUCIBLE"
    promotion_status = "PROMOTION_READY_FOR_SHADOW" if winner and not precheck_only else "PROMOTION_BLOCKED"
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "git_commit_sha": _git_sha(),
        "worktree_clean": _worktree_clean(),
        "config_path": str(config_path),
        "config_sha": _sha256(config_path),
        "evidence_status": status,
        "promotion_status": promotion_status,
        "winner_strategy_id": winner.strategy_id if winner else None,
        "baseline_strategy_id": tournament["baseline_strategy"],
        "database_environment": db_environment,
        "read_only": True,
        "orders_generated": False,
        "broker_accessed": False,
        "rollback_release_id": _load_yaml(RELEASE_REGISTRY).get("active_production_release_id"),
        "approval": {"required_count": int(tournament["shadow_release"]["approval_count"]), "records": []},
        "shadow_release": tournament["shadow_release"],
        "candidates": [item.public_dict() for item in results],
    }
    manifest["manifest_sha"] = _canonical_sha(manifest)
    quarterly = pd.concat(quarter_frames, ignore_index=True) if quarter_frames else pd.DataFrame(columns=["strategy_id", "quarter", "quarter_return"])
    return manifest, results, quarterly


def _write_outputs(output_dir: Path, manifest: dict[str, Any], results: list[CandidateResult], quarterly: pd.DataFrame) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be new or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    gate_rows = [
        {"strategy_id": item.strategy_id, "gate": gate, "passed": passed}
        for item in results for gate, passed in item.gates.items()
    ]
    ranking_rows = []
    for item in sorted(results, key=lambda value: (value.rank is None, value.rank or 9999, value.strategy_id)):
        ranking_rows.append(
            {
                "rank": item.rank,
                "strategy_id": item.strategy_id,
                "candidate_type": item.candidate_type,
                "eligible": item.eligible,
                "annualized_return": item.metrics.get("net_oos_annualized_return"),
                "max_drawdown": item.metrics.get("max_drawdown"),
                "positive_quarter_ratio": item.metrics.get("positive_quarter_ratio"),
                "calmar": item.metrics.get("calmar"),
                "cvar_95_daily": item.metrics.get("cvar_95_daily"),
                "blockers": "|".join(item.blockers),
            }
        )
    pd.DataFrame(ranking_rows).to_csv(output_dir / "strategy_ranking.csv", index=False)
    pd.DataFrame(gate_rows).to_csv(output_dir / "gate_results.csv", index=False)
    quarterly.to_csv(output_dir / "quarterly_metrics.csv", index=False)
    (output_dir / "input_hashes.json").write_text(
        json.dumps({item.strategy_id: item.input_hashes for item in results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# 生产策略统一赛马",
        "",
        f"- 截止日：`{manifest['as_of']}`",
        f"- 证据状态：`{manifest['evidence_status']}`",
        f"- 晋级状态：`{manifest['promotion_status']}`",
        f"- 胜者：`{manifest['winner_strategy_id'] or 'NONE'}`",
        "- 本命令只读，未生成订单，未访问券商。",
        "",
        "## 排名与阻塞",
        "",
        "|排名|策略|类型|年化收益|最大回撤|状态|阻塞|",
        "|---:|---|---|---:|---:|---|---|",
    ]
    for row in ranking_rows:
        ann = "—" if row["annualized_return"] is None else f"{float(row['annualized_return']):.2%}"
        mdd = "—" if row["max_drawdown"] is None else f"{float(row['max_drawdown']):.2%}"
        lines.append(
            f"|{row['rank'] or '—'}|`{row['strategy_id']}`|{row['candidate_type']}|{ann}|{mdd}|"
            f"{'ELIGIBLE' if row['eligible'] else 'INELIGIBLE'}|{row['blockers'] or '—'}|"
        )
    (output_dir / "tournament_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest["artifacts"] = {
        name: _sha256(output_dir / name)
        for name in ("strategy_ranking.csv", "gate_results.csv", "quarterly_metrics.csv", "input_hashes.json", "tournament_report.md")
    }
    manifest["manifest_sha"] = _canonical_sha({key: value for key, value in manifest.items() if key != "manifest_sha"})
    (output_dir / "tournament_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the read-only production strategy tournament")
    parser.add_argument("--as-of", required=True, help="Evidence cutoff date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[], metavar="STRATEGY_ID=DIRECTORY")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db-url-env", default="CHENYIYUN_DB_URL")
    parser.add_argument("--db-read-only-env", default="CHENYIYUN_DB_READ_ONLY")
    parser.add_argument("--precheck-only", action="store_true")
    args = parser.parse_args()

    as_of = pd.Timestamp(args.as_of).date()
    sources = _parse_sources(args.source)
    db_environment = None
    if not args.precheck_only:
        if not _worktree_clean():
            raise RuntimeError("formal tournament requires a clean worktree")
        db_environment = _require_readonly_environment(args.db_url_env, args.db_read_only_env)
    manifest, results, quarterly = evaluate_tournament(
        as_of=as_of,
        config_path=args.config,
        sources=sources,
        precheck_only=args.precheck_only,
        db_environment=db_environment,
    )
    _write_outputs(args.output_dir, manifest, results, quarterly)
    print(json.dumps({
        "manifest": str(args.output_dir / "tournament_manifest.json"),
        "promotion_status": manifest["promotion_status"],
        "winner": manifest["winner_strategy_id"],
    }, ensure_ascii=False))
    return 0 if args.precheck_only or manifest["winner_strategy_id"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
