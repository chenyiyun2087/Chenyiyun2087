#!/usr/bin/env python3
"""Audit whether local or configured sources can build a 2018+ PIT factor panel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha, load_validation_profile


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_pit_factor_sources(
    output_dir: Path,
    *,
    profile_name: str = "formal_v5_0",
    processed_dir: Path = PROJECT_ROOT / "data/processed",
) -> dict[str, Any]:
    profile = load_validation_profile(profile_name)
    candidates: list[dict[str, Any]] = []
    for path in sorted(processed_dir.glob("bs_training_dataset*.parquet")):
        frame = pd.read_parquet(path, columns=["trade_date"])
        dates = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce")
        candidates.append(
            {
                "path": str(path),
                "sha256": _file_sha(path),
                "rows": int(len(frame)),
                "unique_dates": int(dates.nunique()),
                "sample_start": (
                    dates.min().date().isoformat() if dates.notna().any() else None
                ),
                "sample_end": (
                    dates.max().date().isoformat() if dates.notna().any() else None
                ),
            }
        )
    minimum = int(profile["economic_alpha_qualification"]["min_trading_days"])
    usable = [row for row in candidates if row["unique_dates"] >= minimum]
    db_configured = bool(os.getenv("CHENYIYUN_DB_URL"))
    blockers = []
    if not usable:
        blockers.append(f"no_local_panel_has_{minimum}_trading_days")
    if not db_configured:
        blockers.append("CHENYIYUN_DB_URL_not_configured")
    blockers.extend(
        [
            "formal_financial_announcement_available_at_chain_missing",
            "industry_classification_available_at_chain_missing",
            "adjustment_factor_anchor_available_at_chain_missing",
        ]
    )
    report: dict[str, Any] = {
        "schema_version": "alpha_v4_5_pit_factor_panel_source_audit_v1",
        "profile": profile_name,
        "status": "PASS" if not blockers else "BLOCKED",
        "target_range": {
            "start": profile["core_period"]["min_start_date"],
            "end_policy": profile["core_period"]["end_date_policy"],
            "minimum_trading_days": minimum,
            "target_trading_days": int(
                profile["economic_alpha_qualification"]["target_trading_days"]
            ),
        },
        "local_candidates": candidates,
        "database_configured": db_configured,
        "required_database_sources": [
            "tushare_stock.ods_stk_factor",
            "tushare_stock.dws_liquidity_factor",
            "tushare_stock.dim_stock",
            "tushare_stock.dim_trade_cal",
        ],
        "required_pit_fields": [
            "trade_date",
            "symbol",
            "factor_value",
            "available_at",
            "source_snapshot_sha256",
            "industry_available_at",
            "adjustment_factor_available_at",
            "financial_announcement_available_at",
        ],
        "blockers": blockers,
        "automatic_fallback_to_short_panel": False,
        "capital_authority": False,
    }
    report["content_sha256"] = canonical_sha(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pit_factor_panel_source_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", default="formal_v5_0")
    args = parser.parse_args()
    print(
        json.dumps(
            audit_pit_factor_sources(
                args.output_dir, profile_name=args.profile
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
