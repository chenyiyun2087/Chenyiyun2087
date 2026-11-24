#!/usr/bin/env python3
"""CLI entry point for Pair Bottom Backtest Research.

Usage:
  python scripts/research/run_pair_bottom.py --stage data-audit
  python scripts/research/run_pair_bottom.py --stage all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parents[2]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from research.pair_bottom.pipeline import PairBottomPipeline


def main():
    parser = argparse.ArgumentParser(description="Pair Bottom Backtest CLI")
    parser.add_argument("--config", default="research/pair_bottom/config/baseline.yaml", help="Config file path")
    parser.add_argument("--stage", default="all", choices=["data-audit", "baseline", "events", "similarity", "all"], help="Execution stage")
    args = parser.parse_args()

    pipeline = PairBottomPipeline()
    if args.stage == "data-audit":
        res = pipeline.run_phase0_data_audit()
        print("Data audit completed:", res)
    else:
        res = pipeline.run_pipeline()
        print("Pipeline execution completed successfully:", res)


if __name__ == "__main__":
    main()
