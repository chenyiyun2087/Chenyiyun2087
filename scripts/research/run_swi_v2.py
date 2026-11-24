#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Execution entry point for SWI_FACTOR V2.0 Research Pipeline.
Enforces RESEARCH_ONLY governance rules.
"""

import sys
import os

# Add project root to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from research.pair_bottom_second_wave.v2.pipeline_v2 import PipelineV2


def main():
    print("Launching SWI_FACTOR V2.0 Execution...")
    pipeline = PipelineV2()
    results = pipeline.run()
    print("Done. Generated reports:")
    for r in results["reports"]:
        print(f" - {r}")


if __name__ == "__main__":
    main()
