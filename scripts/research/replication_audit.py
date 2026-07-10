"""PR10: Baseline replication audit and diff report.

Generates baseline_replication_report.json and baseline_replication_diff.csv
comparing adapter output against reference production export.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def generate_replication_report(
    p0_outputs: list[pd.DataFrame],
    c0_outputs: list[pd.DataFrame],
    reference_dates: list[str],
    output_dir: Path,
) -> dict[str, Any]:
    """Generate baseline replication audit artifacts.

    Parameters
    ----------
    p0_outputs : Per-date DataFrames from ProductionStrategyAdapter.
    c0_outputs : Per-date DataFrames from ChampionStrategyAdapter.
    reference_dates : Signal dates tested.
    output_dir : Directory to write report files.

    Returns
    -------
    Manifest dict with report paths and summary.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # P0 analysis
    p0_counts = [len(df) for df in p0_outputs]
    c0_counts = [len(df) for df in c0_outputs]

    p0_symbols = set()
    c0_symbols = set()
    for df in p0_outputs:
        if "symbol" in df.columns:
            p0_symbols.update(df["symbol"].unique())
    for df in c0_outputs:
        if "symbol" in df.columns:
            c0_symbols.update(df["symbol"].unique())

    # P0 vs C0 overlap per date
    daily_overlap = []
    for i, (p0_df, c0_df) in enumerate(zip(p0_outputs, c0_outputs)):
        if p0_df.empty or c0_df.empty:
            daily_overlap.append(0)
        else:
            p0_syms = set(p0_df["symbol"].unique())
            c0_syms = set(c0_df["symbol"].unique())
            overlap = len(p0_syms & c0_syms) / max(len(p0_syms | c0_syms), 1)
            daily_overlap.append(float(overlap))

    # Identity report
    identity = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "test_dates": len(reference_dates),
        "p0": {
            "strategy": "production_governed_vol_position",
            "avg_candidates_per_date": float(np.mean(p0_counts)) if p0_counts else 0,
            "unique_symbols": len(p0_symbols),
            "dates_tested": len(p0_outputs),
        },
        "c0": {
            "strategy": "production_governed_vol_position_v1_2b_dynamic_score",
            "avg_candidates_per_date": float(np.mean(c0_counts)) if c0_counts else 0,
            "unique_symbols": len(c0_symbols),
            "dates_tested": len(c0_outputs),
        },
        "p0_vs_c0": {
            "avg_daily_symbol_overlap": float(np.mean(daily_overlap)) if daily_overlap else 0,
            "symbols_only_in_p0": len(p0_symbols - c0_symbols),
            "symbols_only_in_c0": len(c0_symbols - p0_symbols),
        },
    }

    id_path = output_dir / "baseline_replication_report.json"
    id_path.write_text(json.dumps(identity, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Diff CSV
    diff_rows = []
    for i, date in enumerate(reference_dates):
        p0_df = p0_outputs[i] if i < len(p0_outputs) else pd.DataFrame()
        c0_df = c0_outputs[i] if i < len(c0_outputs) else pd.DataFrame()
        diff_rows.append({
            "signal_date": date,
            "p0_count": len(p0_df),
            "c0_count": len(c0_df),
            "differ": "YES" if (set(p0_df.get("symbol", [])) != set(c0_df.get("symbol", []))) else "NO",
            "overlap_pct": daily_overlap[i] if i < len(daily_overlap) else 0,
        })

    diff_df = pd.DataFrame(diff_rows)
    diff_path = output_dir / "baseline_replication_diff.csv"
    diff_df.to_csv(diff_path, index=False)

    return {
        "identity_report": str(id_path),
        "diff_csv": str(diff_path),
        "p0_avg_count": identity["p0"]["avg_candidates_per_date"],
        "c0_avg_count": identity["c0"]["avg_candidates_per_date"],
        "p0_c0_identity_differ": identity["p0_vs_c0"]["symbols_only_in_p0"] > 0 or identity["p0_vs_c0"]["symbols_only_in_c0"] > 0,
    }
