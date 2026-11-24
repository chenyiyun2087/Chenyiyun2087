#!/usr/bin/env python3
"""CLI Runner for Pair Bottom Second Wave Research Pipeline.

Usage:
    python scripts/research/run_pair_bottom_second_wave.py
"""
import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from research.pair_bottom_second_wave.pipeline import SecondWavePipeline


def main():
    print("Initializing Pair Bottom Second Wave Pipeline...")
    pipeline = SecondWavePipeline()
    res = pipeline.run_pipeline()
    print("Execution completed:", res)


if __name__ == "__main__":
    main()
