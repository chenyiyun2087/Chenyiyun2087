#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Execution entry point for SWI_FACTOR V2.1 Evidence Hardening & Independent Recomputation.
Enforces RESEARCH_ONLY governance rules.
"""

import sys
import os

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from research.pair_bottom_second_wave.v2.pipeline_v2_1 import PipelineV2_1


def main():
    print("Launching SWI_FACTOR V2.1 Execution...")
    pipeline = PipelineV2_1()
    results = pipeline.run()
    print("\nExecution Finished. Generated reports:")
    for r in results["reports"]:
        print(f" - {r}")


if __name__ == "__main__":
    main()
