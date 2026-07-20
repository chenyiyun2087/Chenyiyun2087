#!/usr/bin/env python3
"""Read-only schema audit for all PIT V2 source components."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from sqlalchemy import create_engine, inspect

from scoreRank.core.db_config import build_sqlalchemy_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def audit(engine, config_path: Path) -> dict[str, object]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    inspector = inspect(engine)
    findings: list[dict[str, object]] = []
    for component in config.get("components") or []:
        schema, table = str(component["source"]).split(".", 1)
        exists = inspector.has_table(table, schema=schema)
        actual = {column["name"] for column in inspector.get_columns(table, schema=schema)} if exists else set()
        required = set(component.get("required_columns") or []) | set(component.get("primary_key") or [])
        missing = sorted(required - actual)
        findings.append({
            "component": component["name"], "source": component["source"],
            "table_exists": exists, "missing_columns": missing,
            "status": "READY" if exists and not missing else "BLOCKED",
        })
    blocked = [item for item in findings if item["status"] == "BLOCKED"]
    return {
        "status": "READY" if not blocked else "BLOCKED",
        "schema_version": config.get("schema_version"),
        "component_count": len(findings), "blocked_count": len(blocked),
        "findings": findings,
        "write_operations_performed": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "pit_snapshot.yaml")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(create_engine(build_sqlalchemy_url()), args.config)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if result["status"] != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
