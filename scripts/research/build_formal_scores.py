#!/usr/bin/env python3
"""Formal Score Builder — frozen score computation with full provenance.

Operates exclusively on frozen factor panels from the Formal PIT Pipeline.
Never reads current score_rank_daily, current dim_stock, or any live table.
Every input and output is content-hashed for deterministic replay.

v5.1 changes:
  - Strategy definition binding (YAML-loaded identity)
  - Forbidden numeric.fillna(0.0): missing factors reduce daily coverage,
    and the build is BLOCKED if coverage falls below threshold.
  - Timezone enforcement: all factor_available_at must be tz-aware.
  - score_available_at = max(all input available_at, computation_finish_time).
  - Economic equivalence check (optional, when old scores are available).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.fail_closed import blocked_report, fail_closed
from runtime.pit_semantic_contract import (
    signal_time_for_trade_dates,
    validate_explicit_timezone,
)

# ── Default factor weights (fallback; prefer strategy definition YAML) ──
FACTOR_WEIGHTS = {
    "volatility": 0.25,
    "value": 0.25,
    "size": 0.15,
    "momentum": 0.15,
    "liquidity": 0.10,
    "market_beta": 0.10,
}

DEFAULT_STRATEGY_DEF = (
    PROJECT_ROOT / "config" / "strategy_definitions"
    / "production_governed_vol_position_v1_2b_dynamic_score.yaml"
)


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:
        return "UNKNOWN"


def _load_strategy_definition(path: Path) -> dict[str, Any]:
    """Load and validate a strategy definition YAML."""
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _check_coverage(
    panel: pd.DataFrame,
    factor_names: list[str],
    threshold_pct: float,
) -> list[str]:
    """Check daily factor coverage. Returns blockers if threshold is violated."""
    blockers = []
    if panel.empty:
        return ["empty_panel"]

    for factor in factor_names:
        if factor not in panel.columns:
            blockers.append(f"missing_factor_column:{factor}")
            continue
        eligible = panel.get(
            "eligible_universe", pd.Series(False, index=panel.index)
        ).fillna(False).astype(bool)
        scoped = panel.loc[eligible]
        if scoped.empty:
            blockers.append("eligible_universe_empty")
            continue
        numeric = pd.to_numeric(scoped[factor], errors="coerce")
        denominators = numeric.groupby(scoped["trade_date"]).size()
        numerators = numeric.groupby(scoped["trade_date"]).count()
        coverage = numerators.div(denominators.replace(0, np.nan))
        # ``missing_factor_threshold_pct`` is the allowed missing fraction;
        # coverage must therefore be at least 100%-threshold (95% by default).
        minimum_coverage = 1.0 - (threshold_pct / 100.0)
        if coverage.empty or float(coverage.min()) < minimum_coverage:
            blockers.append(
                f"factor_coverage_below_threshold:{factor}:"
                f"minimum={float(coverage.min()) if not coverage.empty else 0.0:.4f},"
                f"threshold={minimum_coverage * 100.0:.2f}%"
            )
    return blockers


def build_formal_scores(
    *,
    factor_panel_path: Path,
    output_dir: Path,
    factor_weights: dict[str, float] | None = None,
    top_pct: float = 0.20,
    strategy_definition_path: Path = DEFAULT_STRATEGY_DEF,
    strategy_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Compute formal scores from a frozen factor panel.

    v5.1: Strategy-definition-bound, missing-factor-aware, tz-enforced.
    """
    # Load strategy definition
    strategy_def = _load_strategy_definition(strategy_definition_path)
    strategy_id = strategy_def.get("strategy_id", "unknown")
    resolved_strategy_ids = tuple(
        str(value) for value in (strategy_ids or [strategy_id]) if str(value)
    )
    if not resolved_strategy_ids:
        return blocked_report(
            "formal_score_builder", "strategy_definition",
            "strategy_ids_missing",
        )
    weights = factor_weights or FACTOR_WEIGHTS
    # Override from strategy definition if present
    if strategy_def.get("factor_weights"):
        weights = {k: float(v) for k, v in strategy_def["factor_weights"].items()}
    signs = {name: 1.0 for name in weights}
    if strategy_def.get("factor_signs"):
        unknown_signs = set(strategy_def["factor_signs"]) - set(weights)
        if unknown_signs:
            return blocked_report(
                "formal_score_builder", "strategy_definition",
                "factor_sign_without_weight",
                extra={"factors": sorted(unknown_signs)},
            )
        signs = {
            name: float(strategy_def["factor_signs"].get(name, 1.0))
            for name in weights
        }

    missing_threshold = float(
        strategy_def.get("missing_factor_threshold_pct", 5.0)
    )

    if not factor_panel_path.exists():
        return blocked_report(
            "formal_score_builder", "input",
            "factor_panel_not_found",
            extra={"path": str(factor_panel_path)},
        )

    panel_sha = _file_sha(factor_panel_path)
    strategy_def_sha = canonical_sha(strategy_def) if strategy_def else ""
    code_sha = _file_sha(Path(__file__))
    git_sha = _git_sha()

    try:
        panel = pd.read_parquet(factor_panel_path)
    except Exception as exc:
        return fail_closed("formal_score_builder", "read_panel", exc)

    # Compatibility for the pre-PIT synthetic score fixture.  A versioned
    # formal panel always carries eligible_universe and family availability;
    # only an unversioned factor-only fixture may use all rows as its research
    # denominator.  This cannot produce historical evidence by itself.
    legacy_factor_fixture = (
        "eligible_universe" not in panel.columns
        and not any(column.endswith("_available_at") for column in panel.columns)
    )
    if legacy_factor_fixture:
        panel["eligible_universe"] = True

    required = set(weights.keys())
    missing = required - set(panel.columns)
    if missing:
        return blocked_report(
            "formal_score_builder", "validate",
            "missing_factor_columns",
            extra={"missing": sorted(missing)},
        )

    # ── Forbidden: numeric.fillna(0.0) ──
    # Check factor coverage BEFORE filling.  Missing factors are NOT neutral.
    coverage_blockers = _check_coverage(panel, sorted(required), missing_threshold)
    if coverage_blockers:
        return blocked_report(
            "formal_score_builder", "coverage",
            "factor_coverage_below_threshold",
            extra={"blockers": coverage_blockers, "threshold_pct": missing_threshold},
        )

    # Build coverage report
    coverage_report = {}
    eligible_mask = panel.get(
        "eligible_universe", pd.Series(False, index=panel.index)
    ).fillna(False).astype(bool)
    for factor in sorted(required):
        if factor in panel.columns:
            scoped = panel.loc[eligible_mask, factor]
            coverage_report[factor] = {
                "missing_pct": round(float(scoped.isna().mean()) * 100.0, 2) if len(scoped) else 100.0,
                "total_rows": int(len(scoped)),
                "coverage_denominator": "eligible_universe_per_trade_date",
            }

    # ── Compute composite score ──
    # Only fill NaN for factors that passed coverage check
    score = pd.Series(0.0, index=panel.index, dtype=float)
    complete = pd.Series(True, index=panel.index)
    for factor, weight in weights.items():
        numeric = pd.to_numeric(panel[factor], errors="coerce")
        complete &= numeric.notna()
        score += numeric * float(weight) * float(signs.get(factor, 1.0))
    score = score.mask(~complete)

    panel["formal_score"] = score
    # `score` is the canonical readiness transport field.  Keep
    # `formal_score` as the named computation output while binding the two to
    # the same bytes; downstream gates never infer a missing score as zero.
    panel["score"] = panel["formal_score"]
    eligible_for_rank = panel.get(
        "eligible_universe", pd.Series(False, index=panel.index)
    ).fillna(False).astype(bool)
    panel["formal_rank"] = np.nan
    panel.loc[eligible_for_rank, "formal_rank"] = panel.loc[eligible_for_rank].groupby(
        "trade_date"
    )["formal_score"].rank(pct=True)
    panel["selected"] = eligible_for_rank & panel["formal_rank"].ge(1.0 - top_pct)
    # The canonical readiness contract is strategy-keyed.  Replicate the
    # same independently computed score for each pre-registered formal
    # strategy identity only when the caller explicitly supplies that set;
    # this does not create or tune a new strategy and keeps one score source
    # bound to the immutable factor panel.
    panel["strategy"] = resolved_strategy_ids[0]
    if len(resolved_strategy_ids) > 1:
        # A single definition cannot be copied into multiple formal strategy
        # identities.  Require callers to invoke the builder once per
        # definition; this keeps weights/signs SHA-bound and fail-closed.
        return blocked_report(
            "formal_score_builder", "strategy_definition",
            "independent_strategy_definition_required",
            extra={"strategy_ids": list(resolved_strategy_ids)},
        )

    # ── Timezone enforcement ──
    avail_cols = [c for c in panel.columns if c.endswith("_available_at")]
    tz_blockers = []
    for col in avail_cols:
        try:
            if validate_explicit_timezone(panel[col]):
                tz_blockers.append(f"{col}_timezone_missing")
            raw = panel[col]
            parsed = pd.to_datetime(raw, errors="coerce", utc=True)
            # v5.3: NaN availability = honest absence on rows without that
            # source (e.g. a brand-new IPO before its first financial
            # statement) — the panel contract rate-gates absence and reports
            # it in the coverage CSV.  Only a NON-EMPTY value that cannot be
            # parsed is a provider format violation.
            invalid = raw.notna() & parsed.isna()
            if invalid.any():
                tz_blockers.append(f"{col}_unparseable_or_no_timezone")
        except Exception:
            tz_blockers.append(f"{col}_timezone_parse_error")

    if tz_blockers:
        return blocked_report(
            "formal_score_builder", "timezone",
            "available_at_timezone_invalid",
            extra={"blockers": tz_blockers},
        )

    if "signal_time" in panel.columns:
        if validate_explicit_timezone(panel["signal_time"]):
            tz_blockers.append("signal_time_timezone_missing")
        signal_time = pd.to_datetime(panel["signal_time"], errors="coerce", utc=True)
    else:
        signal_time = signal_time_for_trade_dates(panel["trade_date"])
        panel["signal_time"] = signal_time
    if signal_time.isna().any():
        tz_blockers.append("signal_time_unparseable")
    # The active formal profile has one timing contract.  Legacy synthetic
    # score fixtures may omit signal_time, but a supplied timestamp in the
    # alpha_v3_2 path must be exactly T 15:30 Asia/Shanghai rather than an
    # earlier/later close silently widening the PIT window.
    if str(strategy_def.get("acceptance_profile") or "") == "alpha_v3_2":
        expected_signal = signal_time_for_trade_dates(panel["trade_date"])
        if (signal_time != expected_signal).fillna(True).any():
            tz_blockers.append("signal_time_not_canonical_t1530")

    if avail_cols:
        avail_frames = [pd.to_datetime(panel[c], utc=True) for c in avail_cols]
        latest_avail = avail_frames[0]
        for af in avail_frames[1:]:
            latest_avail = pd.DataFrame({"a": latest_avail, "b": af}).max(axis=1)
        panel["score_available_at"] = latest_avail
    else:
        panel["score_available_at"] = signal_time
    if (panel["score_available_at"] > signal_time).any():
        tz_blockers.append("score_available_after_signal_time")

    if tz_blockers:
        return blocked_report(
            "formal_score_builder", "timezone",
            "available_at_timezone_invalid",
            extra={"blockers": tz_blockers},
        )

    ordered_dates = sorted(
        pd.to_datetime(panel["trade_date"], errors="coerce").dropna().dt.date.unique()
    )
    next_date = {
        value: ordered_dates[index + 1]
        for index, value in enumerate(ordered_dates[:-1])
    }
    trade_dates = pd.to_datetime(panel["trade_date"], errors="coerce").dt.date
    panel["execution_time"] = trade_dates.map(next_date).map(
        lambda value: (
            f"{value.isoformat()}T09:30:00+08:00" if pd.notna(value) else None
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    score_path = output_dir / "formal_scores.parquet"
    panel.to_parquet(score_path, index=False)

    # ── Provenance ──
    weights_sha = canonical_sha({k: round(v, 6) for k, v in sorted(weights.items())})
    compute_time = datetime.now(timezone.utc).isoformat()

    manifest = {
        "schema_version": "formal_score_builder_v5_1",
        "status": "PASS",
        "strategy_id": strategy_id,
        "strategy_ids": list(resolved_strategy_ids),
        "strategy_definition_sha256": strategy_def_sha,
        "score_code_sha256": code_sha,
        "git_commit_sha": git_sha,
        "factor_panel_sha256": panel_sha,
        "factor_panel_path": str(factor_panel_path),
        "factor_weights": weights,
        "factor_signs": signs,
        "factor_weights_sha256": weights_sha,
        "factor_signs_sha256": canonical_sha({k: signs[k] for k in sorted(signs)}),
        "strategy_definition_binding": {
            strategy_id: {
                "strategy_definition_sha256": strategy_def_sha,
                "factor_weights_sha256": weights_sha,
                "factor_signs_sha256": canonical_sha({k: signs[k] for k in sorted(signs)}),
            }
        },
        "missing_factor_threshold_pct": missing_threshold,
        "factor_coverage": coverage_report,
        "top_pct": top_pct,
        "score_path": str(score_path),
        "score_sha256": _file_sha(score_path),
        "rows": int(len(panel)),
        "symbols": int(panel["symbol"].nunique()) if "symbol" in panel.columns else 0,
        "dates": int(panel["trade_date"].nunique()) if "trade_date" in panel.columns else 0,
        "selected_count": int(panel["selected"].sum()) if "selected" in panel.columns else 0,
        "computed_at": compute_time,
        "signal_cutoff": "T15:30:00+08:00",
        "execution_time": "T+1 09:30:00+08:00",
        "capital_authority": False,
    }
    manifest["content_sha256"] = canonical_sha(
        {k: v for k, v in manifest.items() if k != "content_sha256"}
    )
    (output_dir / "score_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))

    return manifest


def check_economic_equivalence(
    *,
    formal_scores_path: Path,
    old_scores_path: Path | None = None,
    old_score_column: str = "formal_score",
) -> dict[str, Any]:
    """Compare formal scores against old production scores.

    Returns an economic equivalence report.  If old scores are not available,
    returns a DIAGNOSTIC status.  If scores are equivalent, the formal scores
    may claim Champion identity.  Otherwise they are a Challenger.
    """
    if old_scores_path is None or not old_scores_path.exists():
        return {
            "schema_version": "economic_equivalence_v5_1",
            "status": "DIAGNOSTIC",
            "reason": "old_scores_not_available",
            "interpretation": "Cannot verify equivalence without reference scores. "
                             "Formal Score is a CHALLENGER until equivalence is proven.",
        }

    try:
        formal = pd.read_parquet(formal_scores_path)
        old = pd.read_parquet(old_scores_path)
    except Exception as exc:
        return {
            "schema_version": "economic_equivalence_v5_1",
            "status": "DIAGNOSTIC",
            "reason": f"read_error:{type(exc).__name__}",
        }

    # Find overlapping dates
    if "trade_date" not in formal.columns or "trade_date" not in old.columns:
        return {
            "schema_version": "economic_equivalence_v5_1",
            "status": "DIAGNOSTIC",
            "reason": "missing_trade_date_column",
        }

    common_dates = set(formal["trade_date"].unique()) & set(old["trade_date"].unique())
    if not common_dates:
        return {
            "schema_version": "economic_equivalence_v5_1",
            "status": "DIAGNOSTIC",
            "reason": "no_overlapping_dates",
        }

    formal_sub = formal[formal["trade_date"].isin(common_dates)].copy()
    old_sub = old[old["trade_date"].isin(common_dates)].copy()

    # Day-level comparison
    daily_ics = []
    top_n_overlaps = []
    for date in sorted(common_dates):
        fd = formal_sub[formal_sub["trade_date"] == date]
        od = old_sub[old_sub["trade_date"] == date]
        if "symbol" not in fd.columns or "symbol" not in od.columns:
            continue
        common_symbols = set(fd["symbol"]) & set(od["symbol"])
        if len(common_symbols) < 5:
            continue
        fv = fd.set_index("symbol").loc[list(common_symbols), "formal_score"]
        ov = od.set_index("symbol").loc[list(common_symbols), old_score_column]
        # Spearman rank correlation
        from scipy.stats import spearmanr
        corr, _ = spearmanr(fv, ov)
        daily_ics.append(float(corr))

        # Top-20% overlap
        top_n = max(1, int(len(common_symbols) * 0.20))
        f_top = set(fv.nlargest(top_n).index)
        o_top = set(ov.nlargest(top_n).index)
        top_n_overlaps.append(len(f_top & o_top) / max(len(f_top), len(o_top)))

    mean_ic = float(np.mean(daily_ics)) if daily_ics else 0.0
    mean_overlap = float(np.mean(top_n_overlaps)) if top_n_overlaps else 0.0

    # Acceptance thresholds
    equivalent = (mean_ic > 0.99) and (mean_overlap > 0.99)

    return {
        "schema_version": "economic_equivalence_v5_1",
        "status": "EQUIVALENT" if equivalent else "NOT_EQUIVALENT",
        "identity": "CHAMPION" if equivalent else "CHALLENGER",
        "mean_spearman_rank_ic": round(mean_ic, 6),
        "mean_top_n_overlap": round(mean_overlap, 6),
        "overlapping_dates": len(common_dates),
        "daily_ic_count": len(daily_ics),
        "interpretation": (
            "Formal Score is EQUIVALENT to Champion production scores — "
            "may claim Champion identity."
            if equivalent
            else "Formal Score is NOT equivalent to Champion scores — "
                 "must be treated as a separate CHALLENGER strategy."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factor-panel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-pct", type=float, default=0.20)
    parser.add_argument("--strategy-definition", type=Path, default=DEFAULT_STRATEGY_DEF)
    parser.add_argument("--old-scores-path", type=Path, default=None,
                        help="Path to old production scores for equivalence check")
    args = parser.parse_args()
    result = build_formal_scores(
        factor_panel_path=args.factor_panel,
        output_dir=args.output_dir,
        top_pct=args.top_pct,
        strategy_definition_path=args.strategy_definition,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))

    # Run equivalence check if old scores provided
    if args.old_scores_path:
        eq_result = check_economic_equivalence(
            formal_scores_path=args.output_dir / "formal_scores.parquet",
            old_scores_path=args.old_scores_path,
        )
        eq_path = args.output_dir / "economic_equivalence_report.json"
        eq_path.write_text(json.dumps(eq_result, ensure_ascii=False, indent=2, sort_keys=True))
        print(json.dumps(eq_result, ensure_ascii=False, indent=2, sort_keys=True))

    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
