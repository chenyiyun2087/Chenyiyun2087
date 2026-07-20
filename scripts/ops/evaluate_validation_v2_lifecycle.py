#!/usr/bin/env python3
"""Evaluate real Shadow evidence; never promotes or authorizes capital."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.shadow_lifecycle import evaluate_shadow_lifecycle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.daily_evidence.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("rows", [])
    status = evaluate_shadow_lifecycle(rows).to_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
