#!/usr/bin/env python3
"""Cross-check the readiness Artifact against its archived raw outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


REQUIRED_FILES = (
    "artifact.json",
    "readiness.json",
    "report.md",
    "monthly_returns.csv",
    "gate_matrix.csv",
    "upgrade_evidence.csv",
)
MOJIBAKE_MARKERS = ("�", "锟斤拷", "绛栫暐", "涓荤敓浜")


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validate(package: Path) -> dict[str, Any]:
    blockers: list[str] = []
    decoded: dict[str, str] = {}
    for filename in REQUIRED_FILES:
        path = package / filename
        if not path.exists():
            blockers.append(f"missing_file:{filename}")
            continue
        try:
            decoded[filename] = path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            blockers.append(f"invalid_utf8:{filename}")
    if blockers:
        result = {"status": "BLOCKED", "blockers": blockers, "package": str(package)}
        result["validation_sha256"] = _canonical_sha(result)
        return result

    joined = "\n".join(decoded.values())
    for marker in MOJIBAKE_MARKERS:
        if marker in joined:
            blockers.append(f"mojibake_marker:{marker}")
    if "US$" in joined or '"currency":"USD"' in joined.replace(" ", ""):
        blockers.append("non_cny_currency_detected")

    try:
        artifact = json.loads(decoded["artifact.json"])
        readiness = json.loads(decoded["readiness.json"])
    except json.JSONDecodeError as exc:
        blockers.append(f"json_invalid:{exc.msg}")
        artifact = {}
        readiness = {}
    manifest = artifact.get("manifest") or {}
    snapshot = artifact.get("snapshot") or {}
    datasets = snapshot.get("datasets") or {}
    title = str(manifest.get("title") or "")
    blocks = manifest.get("blocks") or []
    if not blocks or blocks[0].get("body") != f"# {title}":
        blockers.append("visible_title_mismatch")
    if snapshot.get("status") == "blocked" and not snapshot.get("accessIssues"):
        blockers.append("blocked_snapshot_without_access_issue")
    headline = (datasets.get("headline") or [{}])[0]
    decision = (readiness.get("decision") or {}).get("decision")
    if headline.get("decision") != decision:
        blockers.append("decision_dataset_mismatch")
    if float(headline.get("allowed_capital_cny", -1)) != float(
        (readiness.get("decision") or {}).get("allowed_capital_cny", -2)
    ):
        blockers.append("capital_dataset_mismatch")

    monthly_csv = pd.read_csv(package / "monthly_returns.csv")
    monthly_rows = datasets.get("monthly_returns") or []
    if len(monthly_csv) != len(monthly_rows):
        blockers.append("monthly_row_count_mismatch")
    else:
        for csv_row, artifact_row in zip(
            monthly_csv.to_dict(orient="records"), monthly_rows
        ):
            if str(csv_row["month"]) != str(artifact_row.get("month")):
                blockers.append("monthly_period_mismatch")
                break
            if abs(
                float(csv_row["monthly_return"])
                - float(artifact_row.get("monthly_return"))
            ) > 1e-12:
                blockers.append("monthly_return_mismatch")
                break
    gate_csv = pd.read_csv(package / "gate_matrix.csv")
    if len(gate_csv) != len(datasets.get("gate_matrix") or []):
        blockers.append("gate_row_count_mismatch")
    upgrade_csv = pd.read_csv(package / "upgrade_evidence.csv")
    if len(upgrade_csv) != len(datasets.get("upgrade_evidence") or []):
        blockers.append("upgrade_evidence_row_count_mismatch")
    if not manifest.get("charts") or not manifest.get("tables"):
        blockers.append("report_visuals_or_tables_missing")
    if "人民币元" not in decoded["artifact.json"]:
        blockers.append("cny_unit_label_missing")

    result = {
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
        "package": str(package),
        "decision": decision,
        "snapshot_status": snapshot.get("status"),
        "monthly_rows": len(monthly_rows),
        "gate_rows": len(datasets.get("gate_matrix") or []),
        "upgrade_evidence_rows": len(datasets.get("upgrade_evidence") or []),
        "utf8_files_checked": len(REQUIRED_FILES),
        "currency": "CNY",
    }
    result["validation_sha256"] = _canonical_sha(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.package)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
