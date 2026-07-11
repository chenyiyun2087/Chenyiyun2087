"""Independent frozen oracle infrastructure for PR18.

Provides:
  - OracleSource enum: where the reference output comes from
  - FrozenOracle: loads independent P0/C0 reference decisions
  - detect_self_referential(): prevents comparing adapter-A vs wrapped-adapter-A
  - export_frozen_oracle(): one-time export of production/champion decisions

Design principle:
  The oracle MUST come from an independent source — frozen production DB export,
  approved ledger, or independently-run production engine output.
  It MUST NOT be re-computed by the same Adapter code that the runtime wraps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Oracle source classification
# ---------------------------------------------------------------------------


class OracleSource(str, Enum):
    """Where the reference output was generated."""
    FROZEN_PRODUCTION_FILE = "FROZEN_PRODUCTION_FILE"
    FROZEN_CHAMPION_FILE = "FROZEN_CHAMPION_FILE"
    PRODUCTION_DB_EXPORT = "PRODUCTION_DB_EXPORT"
    APPROVED_LEDGER = "APPROVED_LEDGER"


# ---------------------------------------------------------------------------
# Oracle provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OracleProvenance:
    """Immutable record of how an oracle was generated.

    PR19: Extended with generator file SHA, command, calendar/corporate-action/
    lifecycle SHAs, database schema SHA, SQL SHA, record count, and approval
    fields to make provenance tamper-resistant and auditable.
    """
    source: OracleSource
    generated_at: str  # ISO timestamp
    git_commit_sha: str
    generating_class: str  # fully-qualified class name of the generator
    generating_function: str  # fully-qualified function name
    config_sha: str = ""
    data_snapshot_sha: str = ""
    # ---- PR19: hardened provenance fields ----
    generator_file_sha: str = ""       # SHA256 of the generator source file
    generator_command: str = ""        # CLI command used to produce the oracle
    calendar_sha: str = ""             # SHA of trade calendar snapshot
    corporate_action_sha: str = ""     # SHA of corporate action data
    lifecycle_sha: str = ""            # SHA of security lifecycle data
    database_schema_sha: str = ""      # SHA of DB schema version info
    sql_sha: str = ""                  # SHA of the SQL query text used
    record_count: int = 0              # Number of decision records
    approved_by: str = ""              # Identity of approver
    approval_sha: str = ""             # SHA of the approval record
    # ---- legacy ----
    adapter_identity: str = ""  # StrategyIdentity.experiment_id if from adapter
    notes: str = ""


# ---------------------------------------------------------------------------
# Frozen reference data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenDailyDecision:
    """One day's frozen P0/C0 decision for comparison."""
    signal_date: str
    symbols: tuple[str, ...]  # top-N symbols in rank order
    rank_scores: tuple[float, ...]  # corresponding rank scores
    final_weights: tuple[float, ...]  # final portfolio weights
    total_exposure: float
    exit_decisions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # Each exit dict: {symbol, entry_date, exit_date, exit_reason, exit_shares}


@dataclass(frozen=True)
class FrozenOracleState:
    """Complete frozen oracle state for one strategy over many dates."""
    strategy_id: str
    experiment_id: str
    provenance: OracleProvenance
    decisions: dict[str, FrozenDailyDecision] = field(default_factory=dict)
    # keyed by signal_date string
    n_dates: int = 0
    date_range: tuple[str, str] = ("", "")

    def get_decision(self, signal_date: str) -> FrozenDailyDecision | None:
        return self.decisions.get(str(signal_date))

    @property
    def dates(self) -> list[str]:
        return sorted(self.decisions.keys())


# ---------------------------------------------------------------------------
# Self-referential detection
# ---------------------------------------------------------------------------


SELF_REFERENTIAL_CLASSES = frozenset({
    "ProductionStrategyAdapter",
    "ChampionStrategyAdapter",
    "AdapterRuntime",
})


def detect_self_referential(
    oracle: FrozenOracleState,
    test_class_name: str,
    test_function_name: str = "",
) -> dict[str, Any]:
    """Detect if oracle and test path use the same code.

    Returns dict with:
      is_self_referential: bool
      reason: str
      oracle_class: str
      test_class: str
    """
    oracle_class = oracle.provenance.generating_class
    oracle_fn = oracle.provenance.generating_function

    # Check 1: Same class
    if oracle_class and test_class_name:
        oracle_short = oracle_class.split(".")[-1]
        test_short = test_class_name.split(".")[-1]
        if oracle_short == test_short:
            return {
                "is_self_referential": True,
                "reason": f"SELF_REFERENTIAL_ORACLE: oracle and test use same class '{oracle_short}'",
                "oracle_class": oracle_class,
                "test_class": test_class_name,
            }

    # Check 2: Oracle was generated by an AdapterRuntime wrapping an Adapter
    if oracle_class.split(".")[-1] in SELF_REFERENTIAL_CLASSES:
        return {
            "is_self_referential": True,
            "reason": f"SELF_REFERENTIAL_ORACLE: oracle generated by adapter class '{oracle_class}' — must be independent production output",
            "oracle_class": oracle_class,
            "test_class": test_class_name,
        }

    # Check 3: Oracle generating function is a known adapter method
    adapter_methods = {"rank", "build_weights", "rank_as_of", "target_exposure"}
    oracle_fn_short = oracle_fn.split(".")[-1] if oracle_fn else ""
    if oracle_fn_short in adapter_methods:
        return {
            "is_self_referential": True,
            "reason": f"SELF_REFERENTIAL_ORACLE: oracle generated by adapter method '{oracle_fn_short}'",
            "oracle_class": oracle_class,
            "test_class": test_class_name,
        }

    return {
        "is_self_referential": False,
        "reason": "",
        "oracle_class": oracle_class,
        "test_class": test_class_name,
    }


# ---------------------------------------------------------------------------
# Oracle loader
# ---------------------------------------------------------------------------


def load_frozen_oracle_from_file(oracle_path: Path) -> FrozenOracleState:
    """Load a frozen oracle from a JSON/parquet file.

    Expected format:
      oracle_path/
        provenance.json    — OracleProvenance fields
        decisions.parquet  — columns: signal_date, symbols (JSON list),
                              rank_scores (JSON list), final_weights (JSON list),
                              total_exposure, exit_decisions (JSON list)
    """
    if not oracle_path.is_dir():
        raise FileNotFoundError(f"Oracle directory not found: {oracle_path}")

    prov_path = oracle_path / "provenance.json"
    if not prov_path.is_file():
        raise FileNotFoundError(f"Oracle provenance missing: {prov_path}")

    provenance_data = json.loads(prov_path.read_text(encoding="utf-8"))
    # Separate provenance fields from metadata fields
    prov_fields = {f.name for f in OracleProvenance.__dataclass_fields__.values()}
    prov_kwargs = {k: v for k, v in provenance_data.items() if k in prov_fields}
    provenance = OracleProvenance(**prov_kwargs)

    decisions_path = oracle_path / "decisions.parquet"
    if not decisions_path.is_file():
        raise FileNotFoundError(f"Oracle decisions missing: {decisions_path}")

    df = pd.read_parquet(decisions_path)
    decisions: dict[str, FrozenDailyDecision] = {}
    for _, row in df.iterrows():
        sd = str(row["signal_date"])
        symbols = tuple(json.loads(row["symbols"]) if isinstance(row["symbols"], str) else row["symbols"])
        rank_scores = tuple(json.loads(row["rank_scores"]) if isinstance(row["rank_scores"], str) else row["rank_scores"])
        final_weights = tuple(json.loads(row["final_weights"]) if isinstance(row["final_weights"], str) else row["final_weights"])
        exit_decisions_raw = row.get("exit_decisions", "[]")
        exit_decisions = tuple(
            json.loads(exit_decisions_raw) if isinstance(exit_decisions_raw, str) else exit_decisions_raw
        )
        decisions[sd] = FrozenDailyDecision(
            signal_date=sd,
            symbols=symbols,
            rank_scores=rank_scores,
            final_weights=final_weights,
            total_exposure=float(row["total_exposure"]),
            exit_decisions=exit_decisions,
        )

    dates = sorted(decisions.keys())
    return FrozenOracleState(
        strategy_id=provenance_data.get("strategy_id", ""),
        experiment_id=provenance_data.get("experiment_id", ""),
        provenance=provenance,
        decisions=decisions,
        n_dates=len(dates),
        date_range=(dates[0] if dates else "", dates[-1] if dates else ""),
    )


def load_frozen_oracle_from_db(
    engine,
    strategy_id: str,
    start_date: str,
    end_date: str,
) -> FrozenOracleState:
    """Load frozen oracle from production database tables.

    Reads from live_positions, live_trades, and live_daily_snapshots
    to reconstruct the actual production decisions.
    """
    from sqlalchemy import text

    # Load daily candidates from production output tables
    candidates_sql = text("""
        SELECT signal_date, symbol, rank_score, effective_weight
        FROM chenyiyun.ads_trusted_strategy_candidates
        WHERE strategy_id = :sid
          AND signal_date BETWEEN :start AND :end
        ORDER BY signal_date, rank_order
    """)
    try:
        candidates = pd.read_sql(candidates_sql, engine, params={
            "sid": strategy_id, "start": start_date, "end": end_date,
        })
    except Exception as exc:
        raise RuntimeError(
            f"Oracle DB query FAILED — cannot load candidates for strategy '{strategy_id}' "
            f"({start_date} to {end_date}). SQL: {candidates_sql.text}. "
            f"Original error: {exc!r}"
        ) from exc

    # Load exit decisions from production tables
    exits_sql = text("""
        SELECT symbol, entry_date, exit_date, exit_reason, shares as exit_shares
        FROM chenyiyun.live_trades
        WHERE strategy_id = :sid
          AND exit_date BETWEEN :start AND :end
          AND direction = 'SELL'
        ORDER BY exit_date
    """)
    try:
        exits = pd.read_sql(exits_sql, engine, params={
            "sid": strategy_id, "start": start_date, "end": end_date,
        })
    except Exception as exc:
        raise RuntimeError(
            f"Oracle DB query FAILED — cannot load exits for strategy '{strategy_id}' "
            f"({start_date} to {end_date}). SQL: {exits_sql.text}. "
            f"Original error: {exc!r}"
        ) from exc

    # Build FrozenDailyDecisions
    decisions: dict[str, FrozenDailyDecision] = {}
    if not candidates.empty:
        for signal_date, grp in candidates.groupby("signal_date"):
            sd = str(signal_date)
            grp_sorted = grp.sort_values("rank_score", ascending=False)
            symbols = tuple(grp_sorted["symbol"].astype(str).tolist())
            rank_scores = tuple(grp_sorted["rank_score"].astype(float).tolist())
            final_weights = tuple(grp_sorted["effective_weight"].astype(float).tolist())
            total_exposure = float(sum(final_weights))

            day_exits: list[dict[str, Any]] = []
            if not exits.empty:
                day_exit_df = exits[exits["exit_date"] == sd]
                day_exits = day_exit_df.to_dict("records")

            decisions[sd] = FrozenDailyDecision(
                signal_date=sd,
                symbols=symbols,
                rank_scores=rank_scores,
                final_weights=final_weights,
                total_exposure=total_exposure,
                exit_decisions=tuple(day_exits),
            )

    provenance = OracleProvenance(
        source=OracleSource.PRODUCTION_DB_EXPORT,
        generated_at=datetime.now(timezone.utc).isoformat(),
        git_commit_sha=_get_production_git_sha(),
        generating_class="frozen_oracle.load_frozen_oracle_from_db",
        generating_function="load_frozen_oracle_from_db",
        adapter_identity=strategy_id,
        database_schema_sha=canonical_sha(str(engine.url)),
        sql_sha=canonical_sha(str(candidates_sql.text) + str(exits_sql.text)),
        record_count=len(decisions),
    )

    dates = sorted(decisions.keys())
    return FrozenOracleState(
        strategy_id=strategy_id,
        experiment_id=strategy_id,
        provenance=provenance,
        decisions=decisions,
        n_dates=len(dates),
        date_range=(dates[0] if dates else "", dates[-1] if dates else ""),
    )


# ---------------------------------------------------------------------------
# Oracle export (one-time, from production)
# ---------------------------------------------------------------------------


def export_frozen_oracle(
    decisions: dict[str, pd.DataFrame],  # signal_date -> DataFrame with columns
    strategy_id: str,
    experiment_id: str,
    git_commit_sha: str,
    generating_class: str,
    generating_function: str,
    output_dir: Path,
    config_sha: str = "",
    data_snapshot_sha: str = "",
) -> FrozenOracleState:
    """Export production/champion decisions as a frozen oracle file.

    This should be run ONCE from the production scheduler or an independent
    production engine run.  The resulting files become the immutable reference
    for golden regression tests.

    Parameters
    ----------
    decisions: dict mapping signal_date -> DataFrame with columns:
        symbol, rank_score, final_portfolio_weight, total_exposure
        and optionally: exit_symbol, exit_date, exit_reason, exit_shares
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    frozen: dict[str, FrozenDailyDecision] = {}

    for signal_date, df in sorted(decisions.items()):
        sd = str(signal_date)
        symbols = tuple(df["symbol"].astype(str).tolist())
        rank_scores = tuple(df.get("rank_score", df.get("rank_score", [0] * len(df))).astype(float).tolist())
        final_weights = tuple(df["final_portfolio_weight"].astype(float).tolist())
        total_exposure = float(sum(final_weights))

        exit_cols = [c for c in ["exit_symbol", "exit_date", "exit_reason", "exit_shares"] if c in df.columns]
        exit_decisions: tuple[dict[str, Any], ...] = ()
        if exit_cols:
            exit_decisions = tuple(df[exit_cols].to_dict("records"))

        frozen[sd] = FrozenDailyDecision(
            signal_date=sd,
            symbols=symbols,
            rank_scores=rank_scores,
            final_weights=final_weights,
            total_exposure=total_exposure,
            exit_decisions=exit_decisions,
        )

        rows.append({
            "signal_date": sd,
            "symbols": json.dumps(list(symbols)),
            "rank_scores": json.dumps(list(rank_scores)),
            "final_weights": json.dumps(list(final_weights)),
            "total_exposure": total_exposure,
            "exit_decisions": json.dumps(list(exit_decisions)),
        })

    # Write decisions parquet
    pd.DataFrame(rows).to_parquet(output_dir / "decisions.parquet", index=False)

    # Write provenance
    generator_file = Path(__file__).resolve()
    try:
        generator_file_sha_val = sha256_hex(generator_file.read_bytes())
    except Exception:
        generator_file_sha_val = ""

    provenance = OracleProvenance(
        source=OracleSource.FROZEN_PRODUCTION_FILE,
        generated_at=datetime.now(timezone.utc).isoformat(),
        git_commit_sha=git_commit_sha,
        generating_class=generating_class,
        generating_function=generating_function,
        config_sha=config_sha,
        data_snapshot_sha=data_snapshot_sha,
        generator_file_sha=generator_file_sha_val,
        generator_command=f"export_frozen_oracle(strategy_id={strategy_id}, experiment_id={experiment_id})",
        record_count=len(frozen),
        adapter_identity=experiment_id,
        notes=f"Frozen {experiment_id} oracle exported from {generating_class}",
    )

    prov_data = {
        "strategy_id": strategy_id,
        "experiment_id": experiment_id,
        **provenance.__dict__,
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(prov_data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    return FrozenOracleState(
        strategy_id=strategy_id,
        experiment_id=experiment_id,
        provenance=provenance,
        decisions=frozen,
        n_dates=len(frozen),
        date_range=(rows[0]["signal_date"] if rows else "", rows[-1]["signal_date"] if rows else ""),
    )


# ---------------------------------------------------------------------------
# Oracle comparison
# ---------------------------------------------------------------------------


@dataclass
class OracleComparisonResult:
    """Result of comparing runtime output against frozen oracle."""
    strategy_id: str
    experiment_id: str
    n_dates: int
    n_dates_matched: int
    n_dates_missing_oracle: int
    candidate_diff_max: int
    top5_diff_max: int
    weight_diff_max: float
    exposure_diff_max: float
    rank_score_diff_max: float
    exit_diff_count: int
    exit_diff_details: list[dict[str, Any]]
    passed: bool
    is_self_referential: bool = False
    self_referential_reason: str = ""


def compare_against_oracle(
    oracle: FrozenOracleState,
    runtime_outputs: dict[str, pd.DataFrame],  # signal_date -> runtime output DataFrame
    top_n: int = 5,
    weight_tolerance: float = 0.0001,
    test_class_name: str = "",
) -> OracleComparisonResult:
    """Compare runtime outputs against a frozen independent oracle.

    Parameters
    ----------
    oracle: The frozen independent reference
    runtime_outputs: Daily runtime output DataFrames keyed by signal_date.
        Each DataFrame must have columns: symbol, rank_score, final_portfolio_weight
    top_n: Number of top candidates to compare
    weight_tolerance: Maximum allowed weight difference
    test_class_name: Name of the test/runtime class for self-referential detection

    Returns
    -------
    OracleComparisonResult with detailed diffs
    """
    # Self-referential check
    ref_check = detect_self_referential(oracle, test_class_name)
    if ref_check["is_self_referential"]:
        return OracleComparisonResult(
            strategy_id=oracle.strategy_id,
            experiment_id=oracle.experiment_id,
            n_dates=len(runtime_outputs),
            n_dates_matched=0,
            n_dates_missing_oracle=0,
            candidate_diff_max=-1,
            top5_diff_max=-1,
            weight_diff_max=-1.0,
            exposure_diff_max=-1.0,
            rank_score_diff_max=-1.0,
            exit_diff_count=0,
            exit_diff_details=[],
            passed=False,
            is_self_referential=True,
            self_referential_reason=ref_check["reason"],
        )

    n_matched = 0
    n_missing = 0
    max_candidate_diff = 0
    max_top5_diff = 0
    max_weight_diff = 0.0
    max_exposure_diff = 0.0
    max_rank_score_diff = 0.0
    exit_diffs: list[dict[str, Any]] = []

    for signal_date, rt_output in sorted(runtime_outputs.items()):
        frozen = oracle.get_decision(str(signal_date))
        if frozen is None:
            n_missing += 1
            continue

        n_matched += 1

        # Compare candidates
        rt_symbols = rt_output["symbol"].astype(str).head(top_n).tolist()
        oracle_symbols = list(frozen.symbols[:top_n])
        candidate_diff = len(set(rt_symbols).symmetric_difference(set(oracle_symbols)))
        top5_diff = 0 if rt_symbols == oracle_symbols else candidate_diff
        max_candidate_diff = max(max_candidate_diff, candidate_diff)
        max_top5_diff = max(max_top5_diff, top5_diff)

        # Compare rank scores for common symbols
        if "rank_score" in rt_output.columns and frozen.rank_scores:
            rt_rs = rt_output[["symbol", "rank_score"]].copy()
            rt_rs["symbol"] = rt_rs["symbol"].astype(str)
            oracle_rs = pd.DataFrame({
                "symbol": list(frozen.symbols[:top_n]),
                "oracle_rank_score": list(frozen.rank_scores[:top_n]),
            })
            rs_merged = rt_rs.merge(oracle_rs, on="symbol", how="inner")
            if not rs_merged.empty:
                rs_diff = float(
                    (pd.to_numeric(rs_merged["rank_score"], errors="coerce")
                     - pd.to_numeric(rs_merged["oracle_rank_score"], errors="coerce"))
                    .abs().max()
                )
                max_rank_score_diff = max(max_rank_score_diff, rs_diff)

        # Compare weights for common symbols
        rt_weights = rt_output[["symbol", "final_portfolio_weight"]].copy()
        rt_weights["symbol"] = rt_weights["symbol"].astype(str)
        oracle_w = pd.DataFrame({
            "symbol": list(frozen.symbols[:top_n]),
            "weight": list(frozen.final_weights[:top_n]),
        })
        merged = rt_weights.merge(oracle_w, on="symbol", how="outer", suffixes=("_rt", "_oracle")).fillna(0.0)
        if not merged.empty:
            w_diff = float((merged["final_portfolio_weight"] - merged["weight"]).abs().max())
            max_weight_diff = max(max_weight_diff, w_diff)

        # Compare exposure
        rt_exposure = float(rt_output["final_portfolio_weight"].head(top_n).sum())
        exposure_diff = abs(rt_exposure - frozen.total_exposure)
        max_exposure_diff = max(max_exposure_diff, exposure_diff)

        # Compare exits (real comparison, not hardcoded 0)
        if frozen.exit_decisions:
            rt_exits = _extract_exits_from_output(rt_output, str(signal_date))
            day_exit_diff = _compare_exits(rt_exits, list(frozen.exit_decisions))
            if day_exit_diff:
                exit_diffs.extend(day_exit_diff)

    passed = (
        n_matched > 0
        and n_missing == 0  # PR19: all dates must match — no missing allowed
        and max_candidate_diff == 0
        and max_top5_diff == 0
        and max_rank_score_diff <= weight_tolerance
        and max_weight_diff <= weight_tolerance
        and max_exposure_diff <= weight_tolerance
        and len(exit_diffs) == 0
        and not ref_check["is_self_referential"]
    )

    return OracleComparisonResult(
        strategy_id=oracle.strategy_id,
        experiment_id=oracle.experiment_id,
        n_dates=len(runtime_outputs),
        n_dates_matched=n_matched,
        n_dates_missing_oracle=n_missing,
        candidate_diff_max=max_candidate_diff,
        top5_diff_max=max_top5_diff,
        weight_diff_max=float(max_weight_diff),
        exposure_diff_max=float(max_exposure_diff),
        rank_score_diff_max=float(max_rank_score_diff),
        exit_diff_count=len(exit_diffs),
        exit_diff_details=exit_diffs,
        passed=passed,
    )


def _extract_exits_from_output(rt_output: pd.DataFrame, signal_date: str) -> list[dict[str, Any]]:
    """Extract exit decisions from runtime output if present.

    PR19: Only returns rows that have at least one non-empty exit field.
    """
    exits = []
    exit_cols = ["exit_symbol", "exit_date", "exit_reason", "exit_shares", "symbol"]
    available = [c for c in exit_cols if c in rt_output.columns]
    if "exit_date" in available or "exit_reason" in available:
        for _, row in rt_output.iterrows():
            exit_info = {}
            has_data = False
            for c in available:
                val = str(row[c]) if pd.notna(row[c]) else ""
                exit_info[c] = val
                # PR19: Track if any exit field actually has data
                if c in ("exit_symbol", "exit_date", "exit_reason", "exit_shares") and val:
                    has_data = True
            if has_data:
                exits.append(exit_info)  # Only include if actual exit data present
    return exits


def _compare_exits(
    rt_exits: list[dict[str, Any]],
    oracle_exits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare exit decisions day-by-day.

    Checks: exit symbol, exit date, exit reason, exit shares.
    """
    diffs = []
    rt_by_symbol = {str(e.get("symbol", e.get("exit_symbol", ""))): e for e in rt_exits}
    oracle_by_symbol = {str(e.get("symbol", e.get("exit_symbol", ""))): e for e in oracle_exits}

    all_symbols = set(rt_by_symbol.keys()) | set(oracle_by_symbol.keys())

    for sym in all_symbols:
        rt_exit = rt_by_symbol.get(sym, {})
        oracle_exit = oracle_by_symbol.get(sym, {})

        if bool(rt_exit) != bool(oracle_exit):
            diffs.append({
                "symbol": sym,
                "type": "exit_presence_mismatch",
                "runtime_has_exit": bool(rt_exit),
                "oracle_has_exit": bool(oracle_exit),
            })
            continue

        if not rt_exit:
            continue

        for field in ["exit_date", "exit_reason", "exit_shares"]:
            rt_val = str(rt_exit.get(field, "")).strip()
            oracle_val = str(oracle_exit.get(field, "")).strip()
            if rt_val != oracle_val:
                diffs.append({
                    "symbol": sym,
                    "type": f"exit_{field}_mismatch",
                    "runtime_value": rt_val,
                    "oracle_value": oracle_val,
                })

    return diffs


# ---------------------------------------------------------------------------
# SHA helpers
# ---------------------------------------------------------------------------


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_sha(value: Any) -> str:
    return sha256_hex(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    )


# ---------------------------------------------------------------------------
# PR19: Production git SHA resolution
# ---------------------------------------------------------------------------


def _get_production_git_sha() -> str:
    """Resolve the production git commit SHA.

    Tries: 1) git rev-parse HEAD, 2) environment variable,
    3) falls back to a marker that provenance validation will flag.
    """
    import os
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[2],
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    env_sha = os.environ.get("CHENYIYUN_GIT_SHA", "")
    if env_sha:
        return env_sha
    # Fallback: provenance validation will flag this as insufficient
    return "UNRESOLVED_GIT_SHA"


# ---------------------------------------------------------------------------
# PR19: Oracle provenance validation
# ---------------------------------------------------------------------------


def validate_oracle_provenance(oracle: FrozenOracleState) -> dict[str, Any]:
    """Validate that an oracle's provenance is complete and trustworthy.

    Required fields vary by OracleSource:
      - FROZEN_PRODUCTION_FILE / FROZEN_CHAMPION_FILE:
          config_sha, data_snapshot_sha, generator_file_sha, git_commit_sha
      - PRODUCTION_DB_EXPORT:
          database_schema_sha, sql_sha, git_commit_sha (not "db_export")
      - APPROVED_LEDGER:
          approval_sha, approved_by

    Returns dict with:
      passed: bool
      errors: list[str] — hard failures that prevent oracle use
      warnings: list[str] — concerns that don't block use but should be addressed
    """
    prov = oracle.provenance
    errors: list[str] = []
    warnings: list[str] = []

    # Check 1: Self-referential generating class
    if prov.generating_class.split(".")[-1] in SELF_REFERENTIAL_CLASSES:
        errors.append(
            f"Oracle generated by adapter class '{prov.generating_class}' — "
            f"must be independent production output"
        )

    # Check 2: Self-referential generating function
    adapter_methods = {"rank", "build_weights", "rank_as_of", "target_exposure"}
    fn_short = prov.generating_function.split(".")[-1] if prov.generating_function else ""
    if fn_short in adapter_methods:
        errors.append(
            f"Oracle generated by adapter method '{prov.generating_function}' — "
            f"must be independent production output"
        )

    # Check 3: git_commit_sha must be real
    if prov.git_commit_sha in ("db_export", "unknown", "UNRESOLVED_GIT_SHA", ""):
        errors.append(
            f"Oracle git_commit_sha is '{prov.git_commit_sha}' — "
            f"must be a real commit SHA"
        )

    # Check 4: Source-specific requirements
    if prov.source in (OracleSource.FROZEN_PRODUCTION_FILE, OracleSource.FROZEN_CHAMPION_FILE):
        for field_name, display in [
            ("config_sha", "config_sha"),
            ("data_snapshot_sha", "data_snapshot_sha"),
            ("generator_file_sha", "generator_file_sha"),
        ]:
            if not getattr(prov, field_name, ""):
                errors.append(
                    f"File-based oracle missing '{display}' — "
                    f"required for independent verification"
                )

    if prov.source == OracleSource.PRODUCTION_DB_EXPORT:
        if not prov.database_schema_sha:
            warnings.append("DB oracle missing database_schema_sha")
        if not prov.sql_sha:
            warnings.append("DB oracle missing sql_sha")

    if prov.source == OracleSource.APPROVED_LEDGER:
        if not prov.approval_sha:
            errors.append(
                "Approved ledger oracle missing 'approval_sha' — "
                "cannot verify approval"
            )
        if not prov.approved_by:
            errors.append(
                "Approved ledger oracle missing 'approved_by' — "
                "cannot verify approver identity"
            )

    # Check 5: record count should be > 0 for a useful oracle
    if oracle.n_dates == 0:
        errors.append("Oracle has zero decision dates — cannot be a valid reference")

    passed = len(errors) == 0
    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "oracle_source": prov.source.value,
        "oracle_strategy_id": oracle.strategy_id,
    }


# ---------------------------------------------------------------------------
# PR19: Enhanced self-referential detection with hash-based checks
# ---------------------------------------------------------------------------


def check_oracle_independence(
    oracle: FrozenOracleState,
    runtime_class_name: str = "",
    runtime_file_sha: str = "",
    runtime_git_sha: str = "",
) -> dict[str, Any]:
    """Comprehensive oracle independence check.

    Combines string-based detection (class/function names) with hash-based
    checks (file SHA, git commit SHA) to prevent tampering.

    Parameters
    ----------
    oracle: The frozen oracle state to check.
    runtime_class_name: Name of the Runtime class generating test outputs.
    runtime_file_sha: SHA256 of the runtime source file.
    runtime_git_sha: Git commit SHA of the runtime code.

    Returns dict with:
      is_independent: bool — True if oracle is truly independent
      is_self_referential: bool — True if oracle was generated by adapter code
      errors: list[str] — hard failures
      warnings: list[str] — concerns
    """
    prov = oracle.provenance
    errors: list[str] = []
    warnings: list[str] = []

    # --- String-based checks (from detect_self_referential) ---
    ref_check = detect_self_referential(oracle, runtime_class_name)
    if ref_check["is_self_referential"]:
        errors.append(ref_check["reason"])

    # --- Hash-based checks ---
    # Check 1: generator_file_sha matches runtime_file_sha
    if prov.generator_file_sha and runtime_file_sha:
        if prov.generator_file_sha == runtime_file_sha:
            errors.append(
                "SELF_REFERENTIAL_HASH: oracle generator_file_sha matches "
                "runtime_file_sha — same file produced both oracle and test"
            )

    # Check 2: git commit SHA matches
    if prov.git_commit_sha and runtime_git_sha:
        if prov.git_commit_sha == runtime_git_sha:
            warnings.append(
                "SAME_GIT_COMMIT: oracle and runtime share git commit "
                f"'{prov.git_commit_sha[:8]}' — may indicate same codebase"
            )

    # Check 3: Approved oracle — documents known bias but does NOT override
    # self-referential detection. Approval can accept known bias; it cannot
    # convert a self-referencing oracle into independent data.
    if prov.approval_sha and prov.approved_by:
        if errors:
            warnings.append(
                f"Oracle approved by '{prov.approved_by}' — "
                f"approval documents known bias but does NOT make self-referential "
                f"data independent. {len(errors)} independence errors remain as HARD failures."
            )
        else:
            warnings.append(
                f"Oracle approved by '{prov.approved_by}' — "
                f"independence checks passed; approval is supplementary."
            )
        # CRITICAL: errors list is NOT cleared — approval never overrides
        # self-referential detection. An adapter-generated oracle with an
        # approval signature is still self-referential and must fail.

    # Check 4: generator file sha missing (for file-based oracles)
    if prov.source in (OracleSource.FROZEN_PRODUCTION_FILE, OracleSource.FROZEN_CHAMPION_FILE):
        if not prov.generator_file_sha:
            errors.append(
                "File-based oracle missing 'generator_file_sha' — "
                "cannot verify file-level independence"
            )

    is_self_referential = bool(errors)
    is_independent = not is_self_referential

    return {
        "is_independent": is_independent,
        "is_self_referential": is_self_referential,
        "errors": errors,
        "warnings": warnings,
        "oracle_source": prov.source.value,
        "oracle_class": prov.generating_class,
        "runtime_class": runtime_class_name,
    }
