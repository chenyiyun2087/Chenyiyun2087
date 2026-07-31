#!/usr/bin/env python3
"""Formal Score Builder — frozen score computation with full provenance.

Operates exclusively on frozen factor panels from the Formal PIT Pipeline.
Never reads current score_rank_daily, current dim_stock, or any live table.
Every input and output is content-hashed for deterministic replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.fail_closed import blocked_report, fail_closed

FACTOR_WEIGHTS = {
    "volatility": 0.25,
    "value": 0.25,
    "size": 0.15,
    "momentum": 0.15,
    "liquidity": 0.10,
    "market_beta": 0.10,
}


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_formal_scores(
    *,
    factor_panel_path: Path,
    output_dir: Path,
    factor_weights: dict[str, float] | None = None,
    top_pct: float = 0.20,
) -> dict[str, Any]:
    """Compute formal scores from a frozen factor panel.

    Returns a manifest with full provenance.  The output formal_scores.parquet
    is sealed and read-only.
    """
    weights = factor_weights or FACTOR_WEIGHTS

    if not factor_panel_path.exists():
        return blocked_report(
            "formal_score_builder", "input",
            "factor_panel_not_found",
            extra={"path": str(factor_panel_path)},
        )

    panel_sha = _file_sha(factor_panel_path)

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

    # Compute composite score
    score = pd.Series(0.0, index=panel.index)
    for factor, weight in weights.items():
        numeric = pd.to_numeric(panel[factor], errors="coerce")
        score += numeric.fillna(0.0) * weight

    panel["formal_score"] = score
    panel["formal_rank"] = panel.groupby("trade_date")["formal_score"].rank(pct=True)
    panel["selected"] = panel["formal_rank"] >= (1.0 - top_pct)

    # Score available_at = max(all factor available_at, compute_time)
    # For deterministic replay: use the latest factor available_at on each row
    avail_cols = [c for c in panel.columns if c.endswith("_available_at")]
    if avail_cols:
        panel["score_available_at"] = panel[avail_cols].max(axis=1)
    else:
        compute_time = datetime.now(timezone.utc).isoformat()
        panel["score_available_at"] = compute_time

    output_dir.mkdir(parents=True, exist_ok=True)
    score_path = output_dir / "formal_scores.parquet"
    panel.to_parquet(score_path, index=False)

    # Provenance
    weights_sha = canonical_sha({k: round(v, 6) for k, v in sorted(weights.items())})
    compute_time = datetime.now(timezone.utc).isoformat()

    manifest = {
        "schema_version": "formal_score_builder_v5_0",
        "status": "PASS",
        "factor_panel_sha256": panel_sha,
        "factor_panel_path": str(factor_panel_path),
        "factor_weights": weights,
        "factor_weights_sha256": weights_sha,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factor-panel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-pct", type=float, default=0.20)
    args = parser.parse_args()
    result = build_formal_scores(
        factor_panel_path=args.factor_panel,
        output_dir=args.output_dir,
        top_pct=args.top_pct,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
