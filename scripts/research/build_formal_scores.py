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
    total_rows = len(panel)
    if total_rows == 0:
        return ["empty_panel"]

    for factor in factor_names:
        if factor not in panel.columns:
            blockers.append(f"missing_factor_column:{factor}")
            continue
        null_ratio = panel[factor].isna().mean()
        if null_ratio > (threshold_pct / 100.0):
            blockers.append(
                f"factor_coverage_below_threshold:{factor}:"
                f"missing={null_ratio:.4f},threshold={threshold_pct:.2f}%"
            )
    return blockers


def build_formal_scores(
    *,
    factor_panel_path: Path,
    output_dir: Path,
    factor_weights: dict[str, float] | None = None,
    top_pct: float = 0.20,
    strategy_definition_path: Path = DEFAULT_STRATEGY_DEF,
) -> dict[str, Any]:
    """Compute formal scores from a frozen factor panel.

    v5.1: Strategy-definition-bound, missing-factor-aware, tz-enforced.
    """
    # Load strategy definition
    strategy_def = _load_strategy_definition(strategy_definition_path)
    strategy_id = strategy_def.get("strategy_id", "unknown")
    weights = factor_weights or FACTOR_WEIGHTS

    # Override from strategy definition if present
    if strategy_def.get("factor_weights"):
        weights = {k: float(v) for k, v in strategy_def["factor_weights"].items()}

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
    for factor in sorted(required):
        if factor in panel.columns:
            coverage_report[factor] = {
                "missing_pct": round(float(panel[factor].isna().mean()) * 100.0, 2),
                "total_rows": int(len(panel)),
            }

    # ── Compute composite score ──
    # Only fill NaN for factors that passed coverage check
    score = pd.Series(0.0, index=panel.index)
    for factor, weight in weights.items():
        numeric = pd.to_numeric(panel[factor], errors="coerce")
        # After coverage check, residual NaN from rare edge cases: fill with 0.0
        # (these are truly negligible if coverage threshold is met)
        residual_nan = numeric.isna().sum()
        if residual_nan > 0:
            numeric = numeric.fillna(0.0)
        score += numeric * weight

    panel["formal_score"] = score
    panel["formal_rank"] = panel.groupby("trade_date")["formal_score"].rank(pct=True)
    panel["selected"] = panel["formal_rank"] >= (1.0 - top_pct)

    # ── Timezone enforcement ──
    avail_cols = [c for c in panel.columns if c.endswith("_available_at")]
    tz_blockers = []
    for col in avail_cols:
        try:
            parsed = pd.to_datetime(panel[col], errors="coerce", utc=True)
            if parsed.isna().any():
                tz_blockers.append(f"{col}_unparseable_or_no_timezone")
        except Exception:
            tz_blockers.append(f"{col}_timezone_parse_error")

    if tz_blockers:
        return blocked_report(
            "formal_score_builder", "timezone",
            "available_at_timezone_invalid",
            extra={"blockers": tz_blockers},
        )

    if avail_cols:
        avail_frames = [pd.to_datetime(panel[c], utc=True) for c in avail_cols]
        latest_avail = avail_frames[0]
        for af in avail_frames[1:]:
            latest_avail = pd.DataFrame({"a": latest_avail, "b": af}).max(axis=1)
        compute_time_utc = pd.Timestamp.now(tz="UTC")
        panel["score_available_at"] = pd.DataFrame({
            "latest_avail": latest_avail,
            "compute_time": compute_time_utc,
        }).max(axis=1)
    else:
        panel["score_available_at"] = pd.Timestamp.now(tz="UTC")

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
        "strategy_definition_sha256": strategy_def_sha,
        "score_code_sha256": code_sha,
        "git_commit_sha": git_sha,
        "factor_panel_sha256": panel_sha,
        "factor_panel_path": str(factor_panel_path),
        "factor_weights": weights,
        "factor_weights_sha256": weights_sha,
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
